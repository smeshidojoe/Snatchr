"""
Список истории Spotlight: карточка со скроллом; строки = обложка + ссылка
(усечённая посередине) + площадка + кнопки (ножницы / копировать / …).

Новая загрузка «наезжает» сверху (position + opacity), остальные строки плавно
сдвигаются вниз. Размер карточки статичен — переполнение уходит в скролл.
"""

import os
import math
import time as _time

from PySide6.QtCore import (
    Qt, QRectF, QPoint, Signal, QPropertyAnimation, QEasingCurve, QTimer
)
from PySide6.QtGui import (
    QPainter, QColor, QPen, QPixmap, QFontMetrics, QPainterPath, QLinearGradient,
)
from PySide6.QtWidgets import QWidget, QScrollArea, QFrame

from core import fonts, themes
from core.i18n import tr
from core.icons import themed_pixmap
from core.trimmer import res_label
from ui import anim
from ui.widgets import SmoothScroll
from core import perflog


def _blend(c0, c1, t):
    """Линейная интерполяция двух QColor (t: 0 -> c0, 1 -> c1)."""
    t = max(0.0, min(1.0, t))
    return QColor(
        int(c0.red() + (c1.red() - c0.red()) * t),
        int(c0.green() + (c1.green() - c0.green()) * t),
        int(c0.blue() + (c1.blue() - c0.blue()) * t),
        int(c0.alpha() + (c1.alpha() - c0.alpha()) * t),
    )

# Глиф -> файл иконки в assets/Themes (перекрашивается под цвет темы).
_GLYPH_ICON = {"scissors": "crop.png", "copy": "copy.png"}


def _same_file(a, b):
    try:
        return bool(a) and bool(b) and os.path.normpath(a) == os.path.normpath(b)
    except Exception:
        return False



def _list_scrolling(widget):
    """Едет ли сейчас список, которому принадлежит виджет."""
    w = widget.parent()
    while w is not None:
        checker = getattr(w, "is_scrolling", None)
        if callable(checker):
            return checker()
        w = w.parent()
    return False


# ------------------------------------------------------------------ #
class GlyphButton(QWidget):
    """Кнопка истории: иконка (crop/copy) или три точки (more) на скруглённой
    подложке — подложка есть всегда, чтобы читалось как кнопка."""
    clicked = Signal()

    def __init__(self, app, glyph, parent=None):
        super().__init__(parent)
        self.app = app
        self._glyph = glyph
        self._hover = False
        s = app._s
        self.setFixedSize(s(34), s(34))
        self.setCursor(Qt.PointingHandCursor)
        self._hover_t = 0.0
        self._pressed = False
        self._press_p = 1.0        # прогресс анимации нажатия (1 = покой)
        self._reload_theme()

    def _reload_theme(self):
        """Цвета и затонированные иконки из текущей палитры (зовётся и при
        живой смене темы)."""
        app, s = self.app, self.app._s
        theme = app.settings.get("theme", themes.DEFAULT_THEME)
        pal = themes.palette(theme)
        self._fg = QColor(pal["muted"])
        self._fg_h = QColor(pal["text"])
        self._base_bg = QColor(pal["sel_chip"])
        self._hover_bg = QColor(pal["sel_chip"]).lighter(140)
        isz = s(19) if self._glyph == "scissors" else s(16)   # ножницы чуть крупнее
        f = _GLYPH_ICON.get(self._glyph)
        self._pm = themed_pixmap(theme, f, pal["muted"], isz) if f else None
        self._pm_h = themed_pixmap(theme, f, pal["text"], isz) if f else None

    def apply_theme(self, pal=None):
        self._reload_theme()
        self.update()

    def sync_hover(self):
        """Сверяет подсветку с РЕАЛЬНЫМ положением курсора.

        Qt шлёт leaveEvent, когда курсор уходит с виджета, но НЕ когда виджет
        уезжает из-под неподвижного курсора — а строки истории двигаются
        (каскад, вставка, пересчёт). Из-за этого подсветка залипала."""
        from PySide6.QtGui import QCursor
        try:
            under = self.rect().contains(self.mapFromGlobal(QCursor.pos()))
        except RuntimeError:
            return
        if under != self._hover:
            self._hover = under
            self._animate_hover(1.0 if under else 0.0)

    def set_glyph(self, g):
        self._glyph = g
        self.update()

    def enterEvent(self, e):
        self._hover = True
        self._animate_hover(1.0)

    def leaveEvent(self, e):
        self._hover = False
        self._animate_hover(0.0)

    def _animate_hover(self, to):
        if _list_scrolling(self):
            self._hover_t = to           # во время прокрутки — без анимации
            self.update()
            return
        anim.animate(self, self._hover_t, to, 150, self._hover_tick,
                     easing=QEasingCurve.OutCubic, attr="_hover_anim")

    def _hover_tick(self, v):
        self._hover_t = v
        self.update()

    def mousePressEvent(self, e):
        self._pressed = (e.button() == Qt.LeftButton
                         and self.rect().contains(e.position().toPoint()))

    def mouseReleaseEvent(self, e):
        was = self._pressed
        self._pressed = False
        if was and self.rect().contains(e.position().toPoint()):
            self.press_bounce()
            self.clicked.emit()

    def press_bounce(self):
        """Отклик на нажатие: сжатие -> лёгкий перелёт -> возврат (как у
        переключателей в настройках)."""
        def tick(v):
            self._press_p = v
            self.update()

        def fin():
            self._press_p = 1.0
            self.update()

        anim.animate(self, 0.0, 1.0, 240, tick,
                     easing=QEasingCurve.Linear, on_finished=fin,
                     attr="_press_anim")

    @staticmethod
    def _scale_of(p):
        # Сжатие -> overshoot -> возврат (та же кривая, что у чекбокса).
        if p <= 0.30:
            return 1.0 + (0.88 - 1.0) * (p / 0.30)
        if p <= 0.70:
            return 0.88 + (1.10 - 0.88) * ((p - 0.30) / 0.40)
        return 1.10 + (1.0 - 1.10) * ((p - 0.70) / 0.30)

    def _row_alpha(self):
        """Прозрачность строки-родителя: кнопки — отдельные виджеты, и общая
        прозрачность художника строки на них не распространяется."""
        return getattr(self.parent(), "_alpha", 1.0)

    def _op(self, p, v):
        p.setOpacity(v * self._row_alpha())

    def paintEvent(self, event):
        alpha = self._row_alpha()
        if alpha <= 0.0:
            return                        # строка ещё не проявилась
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setRenderHint(QPainter.SmoothPixmapTransform, True)
        p.setOpacity(alpha)               # дальше всё идёт через _op
        s = self.app._s
        w, h = self.width(), self.height()
        t = self._hover_t
        # Масштаб нажатия — вокруг центра кнопки.
        if self._press_p < 1.0:
            k = self._scale_of(self._press_p)
            p.translate(w / 2.0, h / 2.0)
            p.scale(k, k)
            p.translate(-w / 2.0, -h / 2.0)
        # постоянная подложка (меньше габаритов кнопки; ярче при наведении)
        inset = s(4)
        p.setPen(Qt.NoPen)
        p.setBrush(_blend(self._base_bg, self._hover_bg, t))
        p.drawRoundedRect(QRectF(inset, inset, w - 2 * inset, h - 2 * inset), s(7), s(7))

        col = _blend(self._fg, self._fg_h, t)
        cx, cy = w / 2, h / 2
        # «close» — крестик (кнопка обрезки активного файла закрывает панель),
        # при наведении краснеет (подложка + сам крестик).
        if self._glyph == "close":
            red = QColor("#e5484d")
            red_bg = QColor("#e5484d"); red_bg.setAlpha(60)
            p.setPen(Qt.NoPen)
            p.setBrush(_blend(self._base_bg, red_bg, t))
            p.drawRoundedRect(QRectF(inset, inset, w - 2 * inset, h - 2 * inset),
                              s(7), s(7))
            col = _blend(self._fg, red, t)
            d = s(5)
            pen = QPen(col, max(1.6, s(1.9)))
            pen.setCapStyle(Qt.RoundCap)
            p.setPen(pen)
            p.drawLine(int(cx - d), int(cy - d), int(cx + d), int(cy + d))
            p.drawLine(int(cx + d), int(cy - d), int(cx - d), int(cy + d))
            p.end()
            return
        # «stop» — залитый квадрат (отмена идущей загрузки)
        if self._glyph == "stop":
            d = s(5)
            p.setPen(Qt.NoPen)
            p.setBrush(col)
            p.drawRoundedRect(QRectF(cx - d, cy - d, 2 * d, 2 * d), s(2), s(2))
            p.end()
            return
        if self._pm is not None and not self._pm.isNull():
            # плавный кроссфейд между обычной и «наведённой» иконкой
            x0, y0 = int((w - self._pm.width()) / 2), int((h - self._pm.height()) / 2)
            self._op(p, 1.0 - t)
            p.drawPixmap(x0, y0, self._pm)
            if self._pm_h is not None and not self._pm_h.isNull():
                self._op(p, t)
                p.drawPixmap(x0, y0, self._pm_h)
            self._op(p, 1.0)
        elif self._glyph == "more":
            p.setBrush(col)
            for dx in (-s(6), 0, s(6)):
                p.drawEllipse(QRectF(cx + dx - s(1.6), cy - s(1.6), s(3.2), s(3.2)))
        p.end()


# ------------------------------------------------------------------ #
class HistoryRow(QWidget):
    """Одна строка истории. entry — запись из core.history."""

    trimClicked = Signal(object)
    closeTrimClicked = Signal(object)     # клик по крестику активного файла
    stopClicked = Signal(object)          # отмена идущей загрузки
    copyClicked = Signal(object)
    moreClicked = Signal(object, QPoint)

    THUMB_W = 84
    THUMB_H = 52

    def __init__(self, app, entry, width, parent=None,
                 downloading=False, allow_trim=True, pending=False, fetching=False):
        super().__init__(parent)
        self.app = app
        self.entry = entry
        s = app._s
        self._h = s(72)
        self._active = False              # идёт ли обрезка этого файла
        self._allow_trim = allow_trim
        # fetching — идёт анализ ссылки (спиннер+«Fetching…» в блоке); pending —
        # проанализирован, ждёт Download (подсвечен, без кнопок); downloading —
        # идёт загрузка; normal — готовый ролик.
        self._state = ("fetching" if fetching else "pending" if pending
                       else "downloading" if downloading else "normal")
        self._spin_angle = 0
        self._transition_t = 0.0          # 1->0: «Fetching…» уезжает, обложка проявляется
        self._spin_timer = QTimer(self)
        self._spin_timer.setInterval(33)
        self._spin_timer.timeout.connect(self._spin_tick)
        if self._state == "fetching":
            self._spin_timer.start()
        self._frac = 0.0
        self._draw_frac = 0.0            # отрисованная доля (плавно догоняет)
        self._hover_t = 0.0
        self._pulse_t = -1.0              # -1 = нет пульсации
        self._err_t = 0.0                # интенсивность «покраснения» ошибки (0 = нет)
        self._err_text = ""              # краткое пояснение поверх блока
        self._dl = {}                    # прогресс: speed/size/downloaded/eta/pct
        self._dl_t = 0.0                 # транзишн (0 = обычная, 1 = пилюли скачивания)
        self.resize(width, self._h)
        self._reload_theme()
        self._hover = False
        self._pm = self._load_thumb()
        self._alpha = 1.0                # общая прозрачность строки (см. _op)
        self._fit_cache = {}             # масштабированные обложки (см. _fit)
        self._sub = self._make_sub()     # площадка + разрешение (Instagram · 1080p)
        # плавное заполнение полосы прогресса
        self._prog_timer = QTimer(self)
        self._prog_timer.setInterval(16)
        self._prog_timer.timeout.connect(self._prog_tick)

        self._btn_more = GlyphButton(app, "more", self)
        self._btn_copy = GlyphButton(app, "copy", self)
        self._btn_trim = GlyphButton(app, "scissors", self) if allow_trim else None
        self._btn_stop = GlyphButton(app, "stop", self)     # отмена загрузки
        if self._btn_trim is not None:
            self._btn_trim.clicked.connect(self._on_trim_btn)
        self._btn_copy.clicked.connect(lambda: self.copyClicked.emit(self.entry))
        self._btn_stop.clicked.connect(lambda: self.stopClicked.emit(self.entry))
        self._btn_more.clicked.connect(
            lambda: self.moreClicked.emit(
                self.entry, self._btn_more.mapToGlobal(QPoint(0, self._btn_more.height()))))
        self._apply_state()
        self._layout()
        if self._state == "downloading":     # строка создана сразу как загрузка —
            self._animate_dl(1.0)            # пилюли всё равно появляются анимацией

    def _plain_title(self):
        """Заголовок без пиктограмм (см. fonts.plain). Считается один раз на
        запись: отрисовка идёт десятки раз в секунду."""
        raw = self.entry.get("title") or self.entry.get("url", "")
        if raw != getattr(self, "_title_raw", None):
            self._title_raw = raw
            self._title_plain = fonts.plain(raw)
        return self._title_plain

    def _make_sub(self):
        """Подпись под заголовком: автор (если известен) ИНАЧЕ площадка, затем
        длина (для проанализированной ссылки) или разрешение (для готового файла)."""
        parts = []
        # Пиктограммы режем: их нет в нашем шрифте, а подстановка эмодзи-шрифта
        # стоит ~600 мс на главном потоке (см. fonts.plain).
        primary = fonts.plain(self.entry.get("uploader")
                              or self.entry.get("host", ""))
        if primary:
            parts.append(primary)
        if self.entry.get("is_gallery"):
            n = int(self.entry.get("media_count") or 0)
            if n:
                parts.append("%d %s" % (n, tr("media")))
            return "  ·  ".join(parts)
        h = self.entry.get("height") or 0
        dur = self.entry.get("duration")
        # Готовый ролик — показываем разрешение; до скачивания (pending) — длину.
        if self._state == "normal" and h:
            parts.append(res_label(h))
        elif dur:
            parts.append(self._fmt_dur(dur))
        elif h:
            parts.append(res_label(h))
        return "  ·  ".join(parts)

    @staticmethod
    def _fmt_dur(secs):
        secs = int(secs)
        h, rem = divmod(secs, 3600)
        m, ss = divmod(rem, 60)
        return f"{h}:{m:02d}:{ss:02d}" if h else f"{m:02d}:{ss:02d}"

    # --- состояние загрузки -------------------------------------------- #
    def is_downloading(self):
        return self._state == "downloading"

    def set_progress(self, frac, info=None):
        self._frac = max(0.0, min(1.0, frac or 0.0))
        if info:
            self._dl = info                  # speed/size/downloaded/eta/percent_str
        if not self._prog_timer.isActive():
            self._prog_timer.start()

    def _animate_dl(self, to, on_finished=None):
        # Обложка (thumbnail) качается мгновенно — без слайда текста/появления
        # блока скорости: _dl_t держим на 0, только зовём on_finished.
        if self.entry.get("is_image"):
            self._dl_t = 0.0
            self.update()
            if on_finished:
                on_finished()
            return
        anim.animate(self, self._dl_t, to, 720, self._dl_tick,
                     easing=QEasingCurve.InOutCubic, on_finished=on_finished,
                     attr="_dl_anim")

    def _dl_tick(self, v):
        self._dl_t = v
        self.update()

    def _res_fps_label(self):
        if self.entry.get("is_audio"):       # аудио — пилюля разрешения не нужна
            return ""
        h = self.entry.get("height") or 0
        if not h:
            return ""
        lbl = res_label(h)
        fps = self.entry.get("fps") or 0
        if fps:
            lbl += " %dfps" % int(round(fps))
        return lbl

    def _draw_pill(self, p, x, ycenter, text, color, fixed_w=None):
        s = self.app._s
        f = fonts.font(s(9), "Semibold")
        padx = s(4)                          # боковые отступы поменьше — пилюля уже
        h = s(16)
        w = fixed_w if fixed_w else QFontMetrics(f).horizontalAdvance(text) + 2 * padx
        r = QRectF(x, ycenter - h / 2.0, w, h)
        # Непрозрачная подложка (chip) — пилюля читается и на треке, и на залитом
        # прогрессе (иначе на светлой теме текст сливается с заливкой).
        bg = QColor(self._chip); bg.setAlpha(235)
        p.setPen(Qt.NoPen); p.setBrush(bg)
        p.drawRoundedRect(r, h / 2.0, h / 2.0)
        p.setFont(f); p.setPen(QColor(color))
        p.drawText(r, Qt.AlignCenter, text)
        return w

    def _pill_w(self, text):
        s = self.app._s
        f = fonts.font(s(9), "Semibold")
        return QFontMetrics(f).horizontalAdvance(text) + 2 * s(4)

    def _fill_x(self):
        s = self.app._s
        return s(4) + self._draw_frac * (self.width() - s(8))

    def _draw_text_split(self, p, rect, text, col_norm):
        """Рисует текст двумя цветами по линии заполнения прогресса: под заливкой —
        on_accent (контраст к акценту), вне — обычный. Шрифт задаёт вызывающий."""
        fill_x = self._fill_x() if self._dl_t > 0.001 else rect.left()
        if fill_x > rect.left():
            p.save()
            p.setClipRect(QRectF(rect.left(), rect.top(),
                                 fill_x - rect.left(), rect.height()))
            p.setPen(self._on_accent)
            p.drawText(rect, Qt.AlignVCenter | Qt.AlignLeft, text)
            p.restore()
        if fill_x < rect.right():
            p.save()
            p.setClipRect(QRectF(fill_x, rect.top(),
                                 rect.right() - fill_x + 2, rect.height()))
            p.setPen(col_norm)
            p.drawText(rect, Qt.AlignVCenter | Qt.AlignLeft, text)
            p.restore()

    def _draw_dl_stats(self, p, s, text_x, right):
        """Нижний ряд загрузки: ФИКСИРОВАННЫЕ колонки (зарезервированная ширина по
        максимуму) -> цифры не «прыгают». Что не влезло (узкое окно) — не рисуем."""
        t = self._dl_t
        self._op(p, t)
        bot_c = s(47) + (1.0 - t) * s(12)
        fstat = fonts.font(s(9), "Regular")
        p.setFont(fstat)
        if self._dl.get("stage") == "post":      # постобработка после 100%
            self._draw_text_split(p, QRectF(text_x, bot_c - s(8),
                                            right - text_x, s(16)),
                                  tr("Processing…"), self._muted)
            self._op(p, 1.0)
            return
        if self._dl.get("stage") == "convert":
            # Конвертация: пилюля скорости (1.31x) + текст сразу за ней на всю
            # оставшуюся ширину (в общих колонках он обрезался об «100.0%»).
            x = text_x
            spd = self._dl.get("speed") or ""
            if spd:
                x += self._draw_pill(p, x, bot_c, spd, self._muted) + s(8)
            self._draw_text_split(p, QRectF(x, bot_c - s(8), right - x, s(16)),
                                  self._dl.get("percent_str") or tr("Converting…"),
                                  self._muted)
            self._op(p, 1.0)
            return
        fm = QFontMetrics(fstat)
        dl = self._dl.get("downloaded") or ""
        tot = self._dl.get("size") or ""
        pct = self._dl.get("percent_str") or ""
        eta = self._dl.get("eta") or ""
        size_str = f"{dl} / {tot}" if (dl and tot) else (tot or dl)
        cols = [
            ("pill", self._pill_w("000.00MiB/s"), self._dl.get("speed") or ""),
            ("txt", fm.horizontalAdvance("999.99MiB / 99.99GiB"), size_str),
            ("txt", fm.horizontalAdvance("100.0%"), pct),
            ("txt", fm.horizontalAdvance("ETA 00:00:00"), ("ETA " + eta) if eta else ""),
        ]
        x = text_x
        gap = s(12)
        avail = right - text_x
        p.setFont(fstat)
        for i, (kind, cw, val) in enumerate(cols):
            if i > 0 and (x - text_x) + cw > avail:
                break                        # не влезает — прекращаем (набор фиксирован)
            if kind == "pill":
                self._draw_pill(p, x, bot_c, val, self._muted, fixed_w=cw)
            else:
                self._draw_text_split(p, QRectF(x, bot_c - s(8), cw, s(16)),
                                      val, self._muted)
            x += cw + gap
        self._op(p, 1.0)

    def _prog_tick(self):
        self._draw_frac += (self._frac - self._draw_frac) * 0.14
        if abs(self._draw_frac - self._frac) < 0.003:
            self._draw_frac = self._frac
            if self._state != "downloading":
                self._prog_timer.stop()
        self.update()

    def set_preview(self, pm):
        """Раннее превью (обложка из yt-dlp) для pending/идущей строки."""
        if pm is not None and not pm.isNull() and self._state in ("downloading", "pending"):
            self._pm = pm
            if self.is_gallery():        # пост: обложка = верхний слой стопки
                self._gallery_pms = [pm]
            self.update()

    def finish(self, entry, pulse=True):
        """Загрузка завершена: обратная анимация (пилюли уезжают, проявляются
        обычные данные), затем обычная строка с опциональной зелёной пульсацией."""
        self.entry = entry
        self._state = "normal"
        self._frac = 1.0
        self._draw_frac = 1.0
        self._prog_timer.stop()
        self._pm = self._load_thumb()
        self._sub = self._make_sub()     # теперь известно разрешение
        self._apply_state()
        self._layout()
        self._animate_dl(0.0, on_finished=(self.start_pulse if pulse else None))
        self.update()

    def flash_error(self, text):
        """Действие не удалось (напр., не смогли удалить файл): блок слегка
        краснеет + краткий текст поверх, держится пару секунд и плавно гаснет.
        Контент строки (обложка/название) при этом не прячем."""
        self._err_text = text or ""
        self._err_t = 1.0
        self.update()
        QTimer.singleShot(2200, self._fade_error)

    def _fade_error(self):
        anim.animate(self, 1.0, 0.0, 450, self._err_tick,
                     on_finished=self._err_faded, attr="_err_anim")

    def _err_tick(self, v):
        self._err_t = v
        self.update()

    def _err_faded(self):
        self._err_t = 0.0
        self._err_text = ""
        self.update()

    def start_pulse(self):
        anim.animate(self, 0.0, 1.0, 1200, self._pulse_tick,
                     on_finished=self._pulse_done, attr="_pulse_anim")

    def _pulse_tick(self, t):
        self._pulse_t = t
        self.update()

    def _pulse_done(self):
        self._pulse_t = -1.0
        self.update()

    def _apply_state(self):
        dl = self._state == "downloading"
        no_btn = self._state in ("pending", "fetching", "error")
        gal = self.is_gallery()
        if self._btn_more is not None:
            self._btn_more.setVisible(not dl and not no_btn)
        if self._btn_copy is not None:
            # У поста файлов много — копировать в буфер нечего.
            self._btn_copy.setVisible(not dl and not no_btn and not gal)
        if self._btn_trim is not None:                 # обложку и пост не режем
            self._btn_trim.setVisible(not dl and not no_btn
                                      and not self.entry.get("is_image")
                                      and not gal)
        self._btn_stop.setVisible(dl)     # стоп — только пока идёт загрузка

    def is_pending(self):
        return self._state == "pending"

    def is_fetching(self):
        return self._state == "fetching"

    def is_error(self):
        return self._state == "error"

    def to_error(self):
        """Анализ не удался: строка становится красным крестиком (кнопок нет)."""
        self._spin_timer.stop()
        self._transition_t = 0.0
        self._state = "error"
        self._apply_state()
        self.update()

    def _spin_tick(self):
        self._spin_angle = (self._spin_angle + 12) % 360
        self.update()

    def update_entry(self, entry):
        """Обновляет данные строки БЕЗ пересоздания виджета.

        Тяжёлое (обложка с диска, разбор подписи) трогаем только если
        соответствующие поля записи действительно изменились."""
        old = self.entry or {}
        self.entry = entry
        if (old.get("thumb") != entry.get("thumb")
                or old.get("path") != entry.get("path")):
            self._pm = self._load_thumb()
        if (old.get("title") != entry.get("title")
                or old.get("host") != entry.get("host")
                or old.get("height") != entry.get("height")
                or old.get("url") != entry.get("url")):
            self._sub = self._make_sub()
        self.update()

    def to_pending(self, entry):
        """Анализ завершён: «Fetching…» уезжает вниз и гаснет, обложка+инфо
        проявляются (transition 1->0)."""
        self.entry = entry
        self._state = "pending"
        self._sub = self._make_sub()
        self._pm = self._load_thumb()
        self._apply_state()
        self._transition_t = 1.0
        if not self._spin_timer.isActive():
            self._spin_timer.start()      # спиннер крутится, пока уезжает
        anim.animate(self, 1.0, 0.0, 320, self._trans_tick,
                     easing=QEasingCurve.OutCubic, on_finished=self._trans_done,
                     attr="_trans_anim")

    def _trans_tick(self, v):
        self._transition_t = v
        self.update()

    def _trans_done(self):
        self._transition_t = 0.0
        self._spin_timer.stop()
        self.update()

    def _draw_fetching_content(self, p, block, s, dy=0.0):
        """Спиннер + «Fetching…» по центру блока (со сдвигом dy по вертикали)."""
        txt = tr("Fetching…")
        f = fonts.font(s(12), "Medium")
        p.setFont(f)
        tw2 = QFontMetrics(f).horizontalAdvance(txt)
        sp = s(18)
        total = sp + s(8) + tw2
        cy = block.center().y() + dy
        sx = block.center().x() - total / 2.0
        p.save()
        p.translate(sx + sp / 2.0, cy)
        p.rotate(self._spin_angle)
        pen = QPen(self._accent, max(2.0, s(2.2)))
        pen.setCapStyle(Qt.RoundCap)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        p.drawArc(QRectF(-sp / 2.0, -sp / 2.0, sp, sp), 90 * 16, 280 * 16)
        p.restore()
        p.setPen(self._text_col)
        p.drawText(QRectF(sx + sp + s(8), block.top() + dy, tw2 + s(4), block.height()),
                   Qt.AlignVCenter | Qt.AlignLeft, txt)

    def start_downloading(self):
        """Переход pending -> downloading (нажали Download в окне): пилюли скачивания
        появляются анимацией (сверху/снизу + opacity)."""
        self._state = "downloading"
        self._frac = 0.0
        self._draw_frac = 0.0
        self._apply_state()
        self._animate_dl(1.0)
        self.update()

    def _on_trim_btn(self):
        # ножницы открывают обрезку; крестик (активный файл) — закрывает её
        if self._active:
            self.closeTrimClicked.emit(self.entry)
        else:
            self.trimClicked.emit(self.entry)

    def set_active(self, on):
        if on == self._active or self._btn_trim is None:
            return
        self._active = on
        self._btn_trim.set_glyph("close" if on else "scissors")

    def _reload_theme(self):
        """Цвета строки из текущей палитры (зовётся и при живой смене темы)."""
        pal = themes.palette(self.app.settings.get("theme", themes.DEFAULT_THEME))
        self._text_col = QColor(pal["title"])
        self._muted = QColor(pal["muted"])
        self._accent = QColor(pal["accent"])
        self._track = QColor(pal["field_bg"])
        self._ok = QColor(pal["ok"])
        self._err = QColor(pal["error"])
        self._hover_bg = QColor(pal["sel_chip"]); self._hover_bg.setAlpha(150)
        self._chip = QColor(pal["sel_chip"])         # непрозрачная подложка пилюль
        self._on_accent = QColor(pal["on_accent"])   # текст поверх залитого прогресса

    def apply_theme(self, pal=None):
        self._reload_theme()
        for b in (self._btn_more, self._btn_copy, self._btn_trim, self._btn_stop):
            if b is not None:
                b.apply_theme()
        self.update()

    def retranslate(self):
        """Смена языка: пересобрать кэшированную подпись строки.

        Остальные надписи («Fetching…», «Converting…») вычисляются прямо при
        отрисовке, поэтому подхватываются сами."""
        self._sub = self._make_sub()
        self.update()

    def sync_hover(self):
        """Сверяет свою подсветку и подсветку кнопок с положением курсора."""
        from PySide6.QtGui import QCursor
        try:
            under = self.rect().contains(self.mapFromGlobal(QCursor.pos()))
        except RuntimeError:
            return
        if under != self._hover:
            self._hover = under
            self._animate_hover(1.0 if under else 0.0)
        for b in (self._btn_more, self._btn_copy, self._btn_trim, self._btn_stop):
            if b is not None:
                b.sync_hover()

    def set_width(self, w):
        self.resize(w, self._h)
        self._layout()

    def _layout(self):
        s = self.app._s
        w = self.width()
        btn = s(34)
        gap = s(6)
        pad = s(12)
        y = (self._h - btn) // 2
        more_x = w - pad - btn
        copy_x = more_x - gap - btn
        self._btn_more.move(more_x, y)
        self._btn_copy.move(copy_x, y)
        self._btn_stop.move(more_x, y)    # стоп — справа по центру (место кнопок)
        if self._btn_trim is not None:
            trim_x = copy_x - gap - btn
            self._btn_trim.move(trim_x, y)
            self._text_right = trim_x - s(10)
        else:
            self._text_right = copy_x - s(10)

    def is_gallery(self):
        return bool(self.entry.get("is_gallery"))

    def _load_gallery_thumbs(self):
        """До трёх обложек поста для стопки (первая — верхний слой)."""
        out = []
        for t in (self.entry.get("thumbs") or [])[:3]:
            if t and os.path.isfile(t):
                pm = QPixmap(t)
                if not pm.isNull():
                    out.append(pm)
        return out

    def _load_thumb(self):
        if self.is_gallery():
            self._gallery_pms = self._load_gallery_thumbs()
            return self._gallery_pms[0] if self._gallery_pms else None
        thumb = self.entry.get("thumb") or ""
        if thumb and os.path.isfile(thumb):
            pm = QPixmap(thumb)
            if not pm.isNull():
                return pm
        return None

    def _paint_gallery_stack(self, p, tx, ty, tw, th):
        """Стопка из трёх слоёв — пост из нескольких файлов.

        Слоёв ВСЕГДА три, даже если файл один: нижние два тогда просто
        полупрозрачное затемнение, чтобы блок читался как «тут несколько».
        Картинки вписываются с заполнением и обрезкой.
        """
        s = self.app._s
        pms = getattr(self, "_gallery_pms", None) or []
        step = s(5)                       # сдвиг каждого следующего слоя
        # Верхний слой занимает основную площадь; нижние выглядывают справа-снизу.
        base_w, base_h = tw - 2 * step, th - 2 * step
        for i in (2, 1, 0):               # рисуем от дальнего к ближнему
            off = i * step
            r = QRectF(tx + off, ty + off, base_w, base_h)
            path = QPainterPath()
            path.addRoundedRect(r, s(5), s(5))
            p.save()
            p.setClipPath(path)
            pm = pms[i] if i < len(pms) else None
            sc = self._fit(pm, base_w, base_h, blur=self._err_t > 0.0)
            if sc is not None:
                p.drawPixmap(int(r.left()), int(r.top()), sc)
                if i:                     # нижние слои притемняем — они «позади»
                    p.fillRect(r, QColor(0, 0, 0, 90 if i == 1 else 140))
            else:
                # Своей картинки у слоя нет (в посте меньше трёх файлов). Рисуем
                # ПЛОТНУЮ карточку, а не полупрозрачную рамку: слой должен
                # читаться как лист под верхним, иначе выглядит пустым контуром.
                sc = self._fit(pms[0] if pms else None, base_w, base_h)
                if sc is not None:
                    p.drawPixmap(int(r.left()), int(r.top()), sc)
                    p.fillRect(r, QColor(0, 0, 0, 120 if i == 1 else 175))
                else:
                    solid = QColor(self._chip)
                    solid.setAlpha(255)
                    p.fillRect(r, solid.darker(115 if i == 1 else 135))
            p.restore()
            p.setPen(QPen(QColor(0, 0, 0, 70), 1))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(r, s(5), s(5))

    def enterEvent(self, e):
        self._hover = True
        self._animate_hover(1.0)

    def leaveEvent(self, e):
        self._hover = False
        self._animate_hover(0.0)

    def _animate_hover(self, to):
        if _list_scrolling(self):
            self._hover_t = to           # во время прокрутки — без анимации
            self.update()
            return
        anim.animate(self, self._hover_t, to, 160, self._hover_tick,
                     easing=QEasingCurve.OutCubic, attr="_hover_anim")

    def _hover_tick(self, v):
        self._hover_t = v
        self.update()

    SLOW_PAINT_MS = 100.0           # порог разбора медленной отрисовки

    def paintEvent(self, event):
        if not perflog.ENABLED:
            return self._paint_row()
        t0 = _time.perf_counter()
        self._marks = []
        try:
            return self._paint_row()
        finally:
            ms = (_time.perf_counter() - t0) * 1000.0
            perflog.tally("строка", ms)
            if ms >= self.SLOW_PAINT_MS:
                prev, parts = t0, []
                for label, t in self._marks:
                    parts.append("%s %.1f" % (label, (t - prev) * 1000.0))
                    prev = t
                parts.append("хвост %.1f" % ((_time.perf_counter() - prev) * 1000.0))
                perflog.note("%7.1f мс  МЕДЛЕННАЯ отрисовка (state=%s alpha=%.2f "
                             "gallery=%s pm=%s) | %s"
                             % (ms, self._state, self._alpha, self.is_gallery(),
                                "нет" if self._pm is None else "%dx%d"
                                % (self._pm.width(), self._pm.height()),
                                ", ".join(parts)))

    def _ck(self, label):
        """Контрольная точка отрисовки (разбирается только у медленных)."""
        if perflog.ENABLED:
            self._marks.append((label, _time.perf_counter()))

    def _paint_row(self):
        """Порядок отрисовки строки. Сами куски — в методах ниже.

        ВАЖНО про прозрачность: при переходе fetching->pending она ставится
        ОДИН раз (1 - trans) и держится через фон, обложку и заголовок, а
        снимают её `_op(p, 1.0)` внутри нижнего ряда. Наложения после него
        рисуются уже в полную силу. Поэтому куски НЕ обёрнуты в save/restore:
        это изменило бы момент сброса и вид промежуточных кадров.
        """
        if self._alpha <= 0.0:
            return                        # строка ещё не проявилась — рисовать нечего
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setRenderHint(QPainter.SmoothPixmapTransform, True)
        p.setOpacity(self._alpha)         # дальше всё идёт через _op
        self._ck("художник")
        s = self.app._s
        w = self.width()
        block = QRectF(s(4), s(4), w - s(8), self._h - s(8))

        # Состояния разбора рисуются целиком и выходят: ни обложки, ни текста,
        # ни наложений у них нет.
        if self._state == "fetching":
            self._paint_fetching_state(p, block, s)
            p.end()
            return
        if self._state == "error":
            self._paint_error_state(p, block, s)
            p.end()
            return

        # Переход fetching->pending: обычное содержимое проявляется (opacity),
        # поверх — «Fetching…» уезжает вниз и гаснет (см. _paint_overlays).
        trans = self._transition_t
        if trans > 0.0:
            self._op(p, 1.0 - trans)

        self._paint_block_bg(p, block, s)
        self._ck("фон блока")

        tw, th = s(self.THUMB_W), s(self.THUMB_H)
        tx, ty = s(12), (self._h - th) // 2
        self._paint_thumb(p, s, tx, ty, tw, th)
        self._ck("обложка")

        text_x = tx + tw + s(14)
        right_dl = (w - s(12) - s(34)) - s(10)       # до кнопки стоп
        right_norm = self._text_right
        t = self._dl_t
        res = self._res_fps_label()
        self._paint_title(p, s, text_x, right_norm, right_dl, res, t)
        self._ck("заголовок")

        self._paint_pill_and_bottom(p, s, text_x, right_norm, right_dl, res, t)
        self._ck("подпись")

        self._paint_overlays(p, block, s, trans)
        self._ck("наложения")
        p.end()

    # --- куски отрисовки ------------------------------------------------ #
    def _paint_fetching_state(self, p, block, s):
        """Ссылку разбирают: подсвеченный блок + спиннер и «Fetching…» по центру."""
        bg = QColor(self._accent)
        bg.setAlpha(28)
        p.setPen(QPen(self._accent, max(1.5, s(1.6))))
        p.setBrush(bg)
        p.drawRoundedRect(block, s(10), s(10))
        self._draw_fetching_content(p, block, s)

    def _paint_error_state(self, p, block, s):
        """Анализ не удался — красный блок и крестик по центру."""
        bg = QColor(self._err)
        bg.setAlpha(28)
        p.setPen(QPen(self._err, max(1.5, s(1.6))))
        p.setBrush(bg)
        p.drawRoundedRect(block, s(10), s(10))
        d = s(9)
        cx, cy = block.center().x(), block.center().y()
        pen = QPen(self._err, max(2.0, s(2.4)))
        pen.setCapStyle(Qt.RoundCap)
        p.setPen(pen)
        p.drawLine(int(cx - d), int(cy - d), int(cx + d), int(cy + d))
        p.drawLine(int(cx + d), int(cy - d), int(cx - d), int(cy + d))

    def _paint_block_bg(self, p, block, s):
        """Подложка блока: полоса прогресса, заливка pending или подсветка."""
        if self._dl_t > 0.001:
            # весь блок — полоса прогресса (трек + заливка акцентом); при завершении
            # плавно затухает (alpha *= _dl_t).
            p.save()
            clip = QPainterPath()
            clip.addRoundedRect(block, s(10), s(10))
            p.setClipPath(clip)
            track = QColor(self._track); track.setAlphaF(self._dl_t)
            p.fillRect(block, track)
            fill = QColor(self._accent)
            fill.setAlphaF(0.9 * self._dl_t)
            p.fillRect(QRectF(block.left(), block.top(),
                              self._draw_frac * block.width(), block.height()), fill)
            p.restore()
        elif self._state == "pending":
            # «ещё не в истории»: лёгкая акцентная заливка + акцентная рамка
            bg = QColor(self._accent)
            bg.setAlpha(28)
            p.setPen(QPen(self._accent, max(1.5, s(1.6))))
            p.setBrush(bg)
            p.drawRoundedRect(block, s(10), s(10))
        elif self._hover_t > 0.01:
            bg = QColor(self._hover_bg)
            bg.setAlpha(int(self._hover_bg.alpha() * self._hover_t))
            p.setPen(Qt.NoPen)
            p.setBrush(bg)
            p.drawRoundedRect(block, s(10), s(10))

    def _paint_thumb(self, p, s, tx, ty, tw, th):
        """Обложка: скруглённая, кроп по центру; у поста — стопка слоёв;
        заглушка, пока файла нет."""
        rect = QRectF(tx, ty, tw, th)
        if self.is_gallery():
            t0 = _time.perf_counter()
            self._paint_gallery_stack(p, tx, ty, tw, th)
            perflog.tally("стопка", (_time.perf_counter() - t0) * 1000.0)
            rect = None
        path = QPainterPath()
        if rect is not None:
            path.addRoundedRect(rect, s(6), s(6))
        p.save()
        if rect is not None:
            p.setClipPath(path)
        scaled = (self._fit(self._pm, tw, th, blur=self._err_t > 0.0)
                  if rect is not None else None)
        if scaled is not None:
            p.drawPixmap(int(tx), int(ty), scaled)
        elif rect is not None:
            p.fillRect(rect, QColor("#26262a"))
        p.restore()
        if rect is not None and self.entry.get("is_image"):   # картинка — янтарная рамка
            p.setPen(QPen(QColor("#ffb020"), max(1.5, s(2.0))))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(rect, s(6), s(6))

    def _paint_title(self, p, s, text_x, right_norm, right_dl, res, t):
        """Заголовок. Рисуется ОДИН раз (не переанимируется): при старте загрузки
        плавно съезжает вправо, освобождая место под пилюлю разрешения."""
        title = self._plain_title()
        pill_off = (self._pill_w(res) + s(9)) if res else 0
        title_x = text_x + t * pill_off
        right_i = right_norm + (right_dl - right_norm) * t
        avail = max(s(30), right_i - title_x)
        f_url = fonts.font(s(12), "Medium")
        p.setFont(f_url)
        elide = Qt.ElideRight if self.entry.get("title") else Qt.ElideMiddle
        elided = QFontMetrics(f_url).elidedText(title, elide, int(avail))
        t0 = _time.perf_counter()
        self._draw_text_split(p, QRectF(title_x, s(14), avail, s(22)), elided,
                              self._text_col)
        perflog.tally("заголовок", (_time.perf_counter() - t0) * 1000.0)

    def _paint_pill_and_bottom(self, p, s, text_x, right_norm, right_dl, res, t):
        """Пилюля res·fps и нижний ряд: «автор · разрешение» и статистика
        загрузки кроссфейдятся между собой по _dl_t."""
        # res·fps пилюля — появляется (opacity + slide сверху).
        if t > 0.001 and res:
            self._op(p, t)
            self._draw_pill(p, text_x, s(25) + (1.0 - t) * (-s(12)), res, self._accent)
            self._op(p, 1.0)

        # нижний ряд: обычный (автор·длина) <-> статы загрузки (кроссфейд).
        if t < 0.999:
            self._op(p, 1.0 - t)
            p.setFont(fonts.font(s(10), "Regular"))
            p.setPen(self._muted)
            p.drawText(QRectF(text_x, s(38), max(s(40), right_norm - text_x), s(18)),
                       Qt.AlignVCenter | Qt.AlignLeft, self._sub)
            self._op(p, 1.0)
        if t > 0.001:
            self._draw_dl_stats(p, s, text_x, right_dl)

    def _paint_overlays(self, p, block, s, trans):
        """Поверх содержимого: пульсация после завершения, уезжающий «Fetching…»
        и скрим ошибки действия."""
        # зелёная пульсация после завершения
        if self._pulse_t >= 0.0:
            intensity = abs(math.sin(self._pulse_t * math.pi * 2))
            gc = QColor(self._ok)
            gc.setAlphaF(0.55 * intensity)
            p.setPen(QPen(gc, max(2.0, s(2.4))))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(block, s(10), s(10))

        # уезжающий вниз и гаснущий «Fetching…» поверх проявляющегося содержимого
        if trans > 0.0:
            self._op(p, trans)
            self._draw_fetching_content(p, block, s, dy=(1.0 - trans) * s(24))
            self._op(p, 1.0)

        # ошибка действия (не удалось удалить и т.п.): блок «мутнеет» (матовый
        # скрим + размытая обложка выше) + красная заливка/рамка, поверх — резкий
        # краткий текст. Контент строки остаётся, но уходит на второй план.
        if self._err_t > 0.0:
            wpath = QPainterPath()
            wpath.addRoundedRect(block, s(10), s(10))
            frost = QColor(self._track)         # матовое стекло поверх содержимого
            frost.setAlphaF(0.55 * self._err_t)
            p.fillPath(wpath, frost)
            wash = QColor(self._err)
            wash.setAlphaF(0.26 * self._err_t)
            p.fillPath(wpath, wash)
            p.setPen(QPen(self._err, max(1.5, s(1.6))))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(block, s(10), s(10))
            if self._err_text:
                self._op(p, min(1.0, self._err_t * 1.4))
                p.setFont(fonts.font(s(11), "Semibold"))
                p.setPen(self._text_col)
                p.drawText(block, Qt.AlignCenter, self._err_text)
                self._op(p, 1.0)
        self._ck("end")

    def _op(self, p, v):
        """Ставит прозрачность художнику с учётом общей прозрачности строки.

        Внутренние переходы (пилюли, спиннер) задают свою прозрачность, а
        появление строки — общую; их надо перемножать. Через _alpha строка
        проявляется БЕЗ QGraphicsOpacityEffect: эффект заставляет Qt рисовать
        виджет в отдельный буфер на каждой перерисовке, а пока окно въезжает,
        перерисовываются все строки подряд — именно это и подвешивало каскад.
        """
        p.setOpacity(v * self._alpha)

    def set_alpha(self, v):
        self._alpha = max(0.0, min(1.0, float(v)))
        self.update()

    def _fit(self, pm, w, h, blur=False):
        """Обложка, вписанная в площадку, с кэшем по (картинка, размер, блюр).

        Раньше масштабирование со SmoothTransformation считалось на КАЖДОЙ
        отрисовке. При обычной прокрутке это незаметно: Qt перебрасывает уже
        нарисованное и перерисовывает лишь открывшуюся полосу. Но пока идёт
        анимация появления, окно двигается и полупрозрачно — переброс невозможен,
        и на каждом кадре перерисовываются ВСЕ строки. Прокрутка в этот момент
        добавляла свои кадры, пересчёт масштаба шёл десятками раз в секунду на
        каждую строку, и список подвисал.
        """
        if pm is None or pm.isNull() or w <= 0 or h <= 0:
            return None
        key = (pm.cacheKey(), int(w), int(h), bool(blur))
        hit = self._fit_cache.get(key)
        if hit is not None:
            return hit
        t0 = _time.perf_counter()
        src = self._blurred(pm) if blur else pm
        out = src.scaled(int(w), int(h), Qt.KeepAspectRatioByExpanding,
                         Qt.SmoothTransformation)
        perflog.tally("масштаб обложки", (_time.perf_counter() - t0) * 1000.0)
        if len(self._fit_cache) > 12:        # строка живёт долго — не копим
            self._fit_cache.clear()
        self._fit_cache[key] = out
        return out

    @staticmethod
    def _blurred(pm):
        """Дешёвое размытие: уменьшаем в 8 раз и растягиваем обратно (smooth)."""
        w = max(1, pm.width() // 8)
        h = max(1, pm.height() // 8)
        small = pm.scaled(w, h, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
        return small.scaled(pm.width(), pm.height(), Qt.IgnoreAspectRatio,
                            Qt.SmoothTransformation)


# ------------------------------------------------------------------ #
class HistoryList(QWidget):
    """Карточка со скроллом; управляет строками и анимацией вставки."""

    trimClicked = Signal(object)
    closeTrimClicked = Signal(object)
    stopClicked = Signal(object)
    copyClicked = Signal(object)
    moreClicked = Signal(object, QPoint)

    def __init__(self, app, parent=None, allow_trim=True, draw_bg=True):
        super().__init__(parent)
        self.app = app
        self._active_path = None
        self._allow_trim = allow_trim
        self._draw_bg = draw_bg           # окно рисует историю без подложки (фон окна свой)
        s = app._s
        self._reload_theme()
        self._rows = []
        self._pad = s(6)
        self._row_h = s(72)

        self._area = QScrollArea(self)
        self._area.setWidgetResizable(False)
        self._area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._area.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self._area.setFrameShape(QFrame.NoFrame)
        self._area.viewport().setStyleSheet("background: transparent;")
        self._style_scroll_area()
        self._content = QWidget()
        self._content.setStyleSheet("background: transparent;")
        self._area.setWidget(self._content)
        self._smooth_scroll = SmoothScroll(self._area, parent=self)
        # Подсветку на время прокрутки не анимируем (см. _on_scrolled).
        self._scroll_busy = False
        self._scroll_idle = QTimer(self)
        self._scroll_idle.setSingleShot(True)
        self._scroll_idle.setInterval(140)
        self._scroll_idle.timeout.connect(self._scroll_settled)
        self._area.verticalScrollBar().valueChanged.connect(self._on_scrolled)

    def resizeEvent(self, event):
        p = self._pad
        self._area.setGeometry(p, p, self.width() - 2 * p, self.height() - 2 * p)
        cw = self._row_width()
        self._content.setFixedWidth(cw)
        for r in self._rows:
            r.set_width(cw)
        self._reflow(animate=False)

    def _row_width(self):
        # Ширину строк считаем от собственной ширины списка (она известна сразу
        # после setGeometry), а не от viewport() — тот до show() ещё не размерен,
        # из-за чего строки раскладывались по нулевой ширине.
        s = self.app._s
        return max(s(120), self.width() - 2 * self._pad - s(10))

    def _reflow(self, animate=False):
        self._content.setFixedHeight(max(self._area.viewport().height(),
                                         len(self._rows) * self._row_h))
        for i, r in enumerate(self._rows):
            target_y = i * self._row_h
            if animate and r.y() != target_y:
                a = QPropertyAnimation(r, b"pos", r)
                a.setDuration(260)
                a.setStartValue(r.pos())
                from PySide6.QtCore import QPoint as _QP
                a.setEndValue(_QP(0, target_y))
                a.setEasingCurve(QEasingCurve.OutCubic)
                a.start()
                r._pos_anim = a
            else:
                r.move(0, target_y)
        # Строки могли уехать из-под курсора — сверяем подсветку.
        QTimer.singleShot(0, self.sync_hover)

    def _make_row(self, entry, downloading=False, pending=False, fetching=False):
        with perflog.measure("создание строки истории", id=entry.get("id")):
            return self._make_row_now(entry, downloading, pending, fetching)

    def _make_row_now(self, entry, downloading=False, pending=False, fetching=False):
        r = HistoryRow(self.app, entry, self._row_width(), self._content,
                       downloading=downloading, allow_trim=self._allow_trim,
                       pending=pending, fetching=fetching)
        r.trimClicked.connect(self.trimClicked)
        r.closeTrimClicked.connect(self.closeTrimClicked)
        r.stopClicked.connect(self.stopClicked)
        r.copyClicked.connect(self.copyClicked)
        r.moreClicked.connect(self.moreClicked)
        if self._active_path and _same_file(entry.get("path"), self._active_path):
            r.set_active(True)
        r.show()
        return r

    def set_active_path(self, path):
        """Отмечает строку активного файла обрезки (её ножницы -> крестик)."""
        self._active_path = path
        for r in self._rows:
            r.set_active(bool(path) and _same_file(r.entry.get("path"), path))

    # Каскад появления строк: сколько штук анимируем и с каким шагом. Анимируем
    # только верхние — на длинной истории остальные всё равно за экраном, а
    # полсотни одновременных анимаций дали бы рывки.
    CASCADE_MAX = 8
    CASCADE_STEP_MS = 90

    def rebuild(self, entries, cascade=False):
        with perflog.measure("HistoryList.rebuild", rows=len(entries),
                             cascade=cascade):
            self._rebuild_now(entries, cascade)

    def _rebuild_now(self, entries, cascade=False):
        """Пересобирает список. cascade=True — строки появляются по очереди
        сверху вниз (fade + наезд), начиная с самой свежей."""
        # Идущий каскад добиваем: часть его строк сейчас будет удалена, и ссылки
        # на них остались бы висеть в _cascade_rows.
        self.finish_cascade()
        # Строки активных загрузок не хранятся в json — сохраняем их объекты
        # (их worker->row связи должны жить) и держим сверху.
        keep = [r for r in self._rows if r.is_downloading() or r.is_pending()
                or r.is_fetching() or r.is_error()]
        # Готовые строки переиспользуем по id: обложка и подпись уже загружены,
        # заново читать их с диска незачем. Раньше список сносился целиком, и на
        # каждом открытии окна все обложки перечитывались.
        pool = {}
        for r in self._rows:
            if r in keep:
                continue
            rid = (r.entry or {}).get("id")
            if rid and rid not in pool:
                pool[rid] = r
            else:
                r.setParent(None)
                r.deleteLater()
        self._content.setFixedWidth(self._row_width())
        made = []
        for e in entries:
            row = pool.pop(e.get("id"), None)
            if row is None:
                made.append(self._make_row(e))
            else:
                row.update_entry(e)
                made.append(row)
        for r in pool.values():          # не пригодившиеся — убираем
            r.setParent(None)
            r.deleteLater()
        self._rows = keep + made
        for r in self._rows:
            r.set_width(self._row_width())
        self._reflow(animate=False)
        if cascade and made:
            self._cascade_in(made)

    def _cascade_in(self, rows):
        """Появление пачки строк по очереди: каждая выезжает снизу и проявляется."""
        s = self.app._s
        shift = s(14)
        # Пока каскад идёт, список «занят»: восемь строк со сдвигом 90 мс плюс
        # 240 мс на каждую — почти секунда анимаций. Если в это время начать
        # прокрутку, она конкурирует с ними и список кажется подвисшим. Поэтому
        # держим список запланированных строк и обрываем каскад по первому
        # действию пользователя (см. finish_cascade).
        self._cascade_pending = []       # ещё не начавшие
        self._cascade_rows = []          # все участники (в т.ч. уже едущие)
        for i, r in enumerate(rows[:self.CASCADE_MAX]):
            base_y = r.y()
            self._cascade_pending.append((r, base_y))
            self._cascade_rows.append((r, base_y))
            r.move(0, base_y + shift)
            # Прячем до своей очереди собственной прозрачностью строки. Раньше
            # тут висел QGraphicsOpacityEffect — он рисует виджет в отдельный
            # буфер на каждой перерисовке, а во время въезда окна перерисовка
            # идёт по всем строкам каждый кадр.
            r.set_alpha(0.0)
        for i, (r, base_y) in enumerate(list(self._cascade_pending)):
            QTimer.singleShot(i * self.CASCADE_STEP_MS,
                              lambda rr=r, y=base_y: self._cascade_step(rr, y, shift))

    def finish_cascade(self):
        """Мгновенно завершает каскад: строки на местах, эффекты сняты.

        Обрывать надо и те строки, что уже едут: иначе их анимации продолжают
        тикать поверх прокрутки — а это и есть та самая «подвисшая секунда»."""
        rows = getattr(self, "_cascade_rows", None)
        if not rows:
            return
        self._cascade_pending = []
        self._cascade_rows = []
        for row, base_y in rows:
            try:
                anim.stop(row, "_cascade_anim")
                anim.stop(row, "_fade_anim")
                row.setGraphicsEffect(None)
                row.set_alpha(1.0)
                row.move(0, base_y)
            except RuntimeError:
                pass

    def _cascade_drop(self, row):
        """Убирает строку из очереди каскада (и из списка участников)."""
        self._cascade_pending = [(r, y) for r, y in
                                 getattr(self, "_cascade_pending", []) if r is not row]
        self._cascade_rows = [(r, y) for r, y in
                              getattr(self, "_cascade_rows", []) if r is not row]

    def _cascade_step(self, row, base_y, shift):
        with perflog.measure("шаг каскада"):
            self._cascade_step_now(row, base_y, shift)

    def _cascade_step_now(self, row, base_y, shift):
        # Каскад мог быть оборван (пользователь начал прокрутку) — тогда строка
        # уже на месте и трогать её не нужно.
        if not any(r is row for r, _ in getattr(self, "_cascade_pending", [])):
            return
        try:
            visible = row.isVisible()
        except RuntimeError:
            self._cascade_drop(row)
            return                              # строку успели убрать
        if not visible:
            # Свой тик строка получила раньше, чем окно успели показать.
            # Анимировать нечего, но и просто выйти нельзя: на строке висит
            # эффект прозрачности 0, и она осталась бы невидимой навсегда —
            # это и был «блок в истории не прогрузился». Ставим на место.
            row.setGraphicsEffect(None)
            row.set_alpha(1.0)
            row.move(0, int(base_y))
            self._cascade_drop(row)
            return
        # Позицию задаём здесь же: между постановкой в очередь и своим тиком
        # мог пройти пересчёт раскладки и вернуть строку на место.
        row.move(0, int(base_y + shift))
        self._cascade_pending = [(r, y) for r, y in self._cascade_pending
                                 if r is not row]
        def done(rr=row):
            self._cascade_rows = [(r, y) for r, y in
                                  getattr(self, "_cascade_rows", []) if r is not rr]

        def tick(t, rr=row, y=base_y):
            rr.move(0, int(y + shift * t))
            rr.set_alpha(1.0 - t)          # 1 -> 0 по t, значит 0 -> 1 прозрачности

        anim.animate(row, 1.0, 0.0, 240, tick,
                     easing=QEasingCurve.OutCubic, on_finished=done,
                     attr="_cascade_anim")

    def insert_new(self, entry):
        """Добавляет готовую запись сверху с анимацией наезда."""
        row = self._make_row(entry)
        self._animate_insert(row)
        return row

    def insert_downloading(self, entry):
        """Добавляет строку идущей загрузки (блок = полоса прогресса)."""
        row = self._make_row(entry, downloading=True)
        self._animate_insert(row)
        return row

    def insert_pending(self, entry):
        """Добавляет строку проанализированной, но ещё не скачиваемой ссылки
        (подсвечена иначе; ждёт нажатия Download в окне)."""
        row = self._make_row(entry, pending=True)
        self._animate_insert(row)
        return row

    def insert_fetching(self, entry):
        """Добавляет строку идущего анализа ссылки (спиннер + «Fetching…»)."""
        row = self._make_row(entry, fetching=True)
        self._animate_insert(row)
        return row

    def _animate_insert(self, row):
        self._rows.insert(0, row)
        # существующие уже стоят на своих y; расширяем контент и сдвигаем их вниз
        self._content.setFixedHeight(len(self._rows) * self._row_h)
        for i, r in enumerate(self._rows[1:], start=1):
            a = QPropertyAnimation(r, b"pos", r)
            a.setDuration(280)
            a.setStartValue(QPoint(0, (i - 1) * self._row_h))
            a.setEndValue(QPoint(0, i * self._row_h))
            a.setEasingCurve(QEasingCurve.OutCubic)
            a.start()
            r._pos_anim = a
        # новая строка: наезжает сверху (сдвиг + прозрачность)
        row.move(0, -self._row_h // 3)
        a = QPropertyAnimation(row, b"pos", row)
        a.setDuration(300)
        a.setStartValue(QPoint(0, -self._row_h // 3))
        a.setEndValue(QPoint(0, 0))
        a.setEasingCurve(QEasingCurve.OutCubic)
        a.start()
        row._pos_anim = a
        anim.fade(row, 0.0, 1.0, 300)
        self._area.verticalScrollBar().setValue(0)

    def drop_missing(self):
        """Убирает строки, чей файл удалён с диска (пока окно открыто). Строки
        идущих загрузок не трогаем. Возвращает id удалённых записей."""
        from core import history
        gone = [r for r in self._rows
                if not r.is_downloading() and not r.is_pending()
                and not r.is_fetching() and not r.is_error()
                and history.file_gone(r.entry.get("path"))]
        for r in gone:
            self.remove_row(r)
        return [r.entry.get("id") for r in gone]

    def set_entry_waveform(self, entry_id, path):
        """Прописать готовую заготовку волны в строку (для мгновенной обрезки)."""
        for r in self._rows:
            if r.entry.get("id") == entry_id:
                r.entry["waveform"] = path
                return

    def flash_error(self, entry_id, text):
        """Подсветить строку с данным id красным + текстом (действие не удалось)."""
        for r in self._rows:
            if r.entry.get("id") == entry_id:
                r.flash_error(text)
                return True
        return False

    def _style_scroll_area(self):
        """Стиль полосы прокрутки списка (цвет ручки — из палитры)."""
        pal = themes.palette(self.app.settings.get("theme", themes.DEFAULT_THEME))
        self._area.setStyleSheet(
            "QScrollArea { background: transparent; border: none; }"
            "QScrollBar:vertical { background: transparent; width: 7px; margin: 3px; }"
            f"QScrollBar::handle:vertical {{ background: {pal['muted']};"
            "  border-radius: 3px; min-height: 26px; }"
            "QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }"
            "QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }")

    def _reload_theme(self):
        pal = themes.palette(self.app.settings.get("theme", themes.DEFAULT_THEME))
        self._bg = QColor(pal["card_bg"])
        self._border = QColor(pal["border"])
        if getattr(self, "_area", None) is not None:
            self._style_scroll_area()

    def is_scrolling(self):
        """Идёт ли сейчас прокрутка (тогда подсветку не анимируем)."""
        return getattr(self, "_scroll_busy", False)

    def _paint_probe(self):
        """Сколько стоит один полный перерис списка (для perf-лога)."""
        with perflog.measure("перерисовка списка", rows=len(self._rows)):
            self.repaint()

    def _on_scrolled(self, _value=None):
        """Пока список едет, строки проезжают под неподвижным курсором и Qt
        осыпает их enter/leave. Каждое запускало анимацию подсветки на 150 мс с
        тиками каждые 8 мс — десятки анимаций поверх самой прокрутки. На время
        движения подсветку не анимируем, а после остановки сверяем один раз."""
        self.finish_cascade()          # пользователь взялся за список — не мешаем
        self._scroll_busy = True
        self._scroll_idle.start()

    def _scroll_settled(self):
        self._scroll_busy = False
        self.sync_hover()

    def retranslate(self):
        """Смена языка: обновить подписи всех строк."""
        for r in list(self._rows):
            try:
                r.retranslate()
            except RuntimeError:
                pass

    def sync_hover(self):
        """Сверяет подсветку всех строк с положением курсора."""
        for r in list(self._rows):
            try:
                r.sync_hover()
            except RuntimeError:
                pass

    def leaveEvent(self, e):
        # Курсор ушёл со списка — гасим подсветку у всех строк разом.
        self.sync_hover()
        super().leaveEvent(e)

    def apply_theme(self, pal=None):
        """Перекрашивает список и все его строки без пересоздания."""
        self._reload_theme()
        for r in self._rows:
            try:
                r.apply_theme()
            except RuntimeError:
                pass
        self.update()

    def show_copied(self, entry_id):
        """Всплывающее «Copied» над кнопкой копирования нужной строки."""
        from ui.widgets import FloatingHint
        for r in self._rows:
            if r.entry.get("id") == entry_id:
                FloatingHint.show_over(r._btn_copy)
                return True
        return False

    def remove_by_id(self, entry_id):
        """Мгновенно (визуально) убирает строку с данным id, если она есть.
        True — нашли и убрали. Тяжёлую очистку файлов/JSON вызывающий делает
        отдельно/в фоне, чтобы удаление ощущалось моментальным."""
        for r in list(self._rows):
            if r.entry.get("id") == entry_id:
                self.remove_row(r)
                return True
        return False

    def remove_row(self, row):
        """Плавно убирает строку (напр., отменённая загрузка) и подтягивает
        остальные вверх."""
        if row not in self._rows:
            return                          # уже убрана — второй раз не трогаем
        self._rows.remove(row)
        self._reflow(animate=True)          # остальные едут вверх
        row.raise_()

        def gone(r=row):
            r.setParent(None)
            r.deleteLater()
        anim.fade(row, 1.0, 0.0, 200, on_finished=gone)

    def paintEvent(self, event):
        if not self._draw_bg:
            return                       # окно: без подложки, поверх фона окна
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        s = self.app._s
        w, h = self.width(), self.height()
        p.setPen(QPen(self._border, 1))
        grad = QLinearGradient(0, 0, 0, h)      # свой вертикальный градиент истории
        grad.setColorAt(0.0, self._bg.lighter(104))
        grad.setColorAt(1.0, self._bg.darker(106))
        p.setBrush(grad)
        p.drawRoundedRect(QRectF(0.5, 0.5, w - 1, h - 1), s(18), s(18))
        p.end()
