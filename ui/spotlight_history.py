"""
Список истории Spotlight: карточка со скроллом; строки = обложка + ссылка
(усечённая посередине) + площадка + кнопки (ножницы / копировать / …).

Новая загрузка «наезжает» сверху (position + opacity), остальные строки плавно
сдвигаются вниз. Размер карточки статичен — переполнение уходит в скролл.
"""

import os
import math

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
        pal = themes.palette(app.settings.get("theme", themes.DEFAULT_THEME))
        self._fg = QColor(pal["muted"])
        self._fg_h = QColor(pal["text"])
        self._base_bg = QColor(pal["sel_chip"])
        self._hover_bg = QColor(pal["sel_chip"]).lighter(140)
        self._hover_t = 0.0
        self._pressed = False
        isz = s(19) if glyph == "scissors" else s(16)   # ножницы чуть крупнее
        f = _GLYPH_ICON.get(glyph)
        theme = app.settings.get("theme", themes.DEFAULT_THEME)
        self._pm = themed_pixmap(theme, f, pal["muted"], isz) if f else None
        self._pm_h = themed_pixmap(theme, f, pal["text"], isz) if f else None

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
            self.clicked.emit()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setRenderHint(QPainter.SmoothPixmapTransform, True)
        s = self.app._s
        w, h = self.width(), self.height()
        t = self._hover_t
        # постоянная подложка (меньше габаритов кнопки; ярче при наведении)
        inset = s(4)
        p.setPen(Qt.NoPen)
        p.setBrush(_blend(self._base_bg, self._hover_bg, t))
        p.drawRoundedRect(QRectF(inset, inset, w - 2 * inset, h - 2 * inset), s(7), s(7))

        col = _blend(self._fg, self._fg_h, t)
        cx, cy = w / 2, h / 2
        # «close» — крестик (кнопка обрезки активного файла закрывает панель)
        if self._glyph == "close":
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
            p.setOpacity(1.0 - t)
            p.drawPixmap(x0, y0, self._pm)
            if self._pm_h is not None and not self._pm_h.isNull():
                p.setOpacity(t)
                p.drawPixmap(x0, y0, self._pm_h)
            p.setOpacity(1.0)
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
        self.resize(width, self._h)
        pal = themes.palette(app.settings.get("theme", themes.DEFAULT_THEME))
        self._text_col = QColor(pal["title"])
        self._muted = QColor(pal["muted"])
        self._accent = QColor(pal["accent"])
        self._track = QColor(pal["field_bg"])
        self._ok = QColor(pal["ok"])
        self._err = QColor(pal["error"])
        self._hover_bg = QColor(pal["sel_chip"]); self._hover_bg.setAlpha(150)
        self._hover = False
        self._pm = self._load_thumb()
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

    def _make_sub(self):
        """Подпись под заголовком: автор (если известен) ИНАЧЕ площадка, затем
        разрешение/длительность. Автора и площадку вместе не пишем — не влезает."""
        parts = []
        primary = self.entry.get("uploader") or self.entry.get("host", "")
        if primary:
            parts.append(primary)
        h = self.entry.get("height") or 0
        if h:
            parts.append(res_label(h))
        else:
            d = self.entry.get("duration")
            if d:
                parts.append(self._fmt_dur(d))
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

    def set_progress(self, frac):
        self._frac = max(0.0, min(1.0, frac or 0.0))
        if not self._prog_timer.isActive():
            self._prog_timer.start()

    def _prog_tick(self):
        self._draw_frac += (self._frac - self._draw_frac) * 0.2
        if abs(self._draw_frac - self._frac) < 0.003:
            self._draw_frac = self._frac
            if self._state != "downloading":
                self._prog_timer.stop()
        self.update()

    def set_preview(self, pm):
        """Раннее превью (обложка из yt-dlp) для pending/идущей строки."""
        if pm is not None and not pm.isNull() and self._state in ("downloading", "pending"):
            self._pm = pm
            self.update()

    def finish(self, entry, pulse=True):
        """Загрузка завершена: строка становится обычной (обложка + кнопки),
        опционально с зелёной пульсацией."""
        self.entry = entry
        self._state = "normal"
        self._frac = 1.0
        self._draw_frac = 1.0
        self._prog_timer.stop()
        self._pm = self._load_thumb()
        self._sub = self._make_sub()     # теперь известно разрешение
        self._apply_state()
        self._layout()
        self.update()
        if pulse:
            self.start_pulse()

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
        for b in (self._btn_more, self._btn_copy):
            if b is not None:
                b.setVisible(not dl and not no_btn)    # у pending/fetching кнопок нет
        if self._btn_trim is not None:                 # обложку не режем
            self._btn_trim.setVisible(not dl and not no_btn
                                      and not self.entry.get("is_image"))
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
        """Переход pending -> downloading (нажали Download в окне)."""
        self._state = "downloading"
        self._frac = 0.0
        self._draw_frac = 0.0
        self._apply_state()
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

    def _load_thumb(self):
        thumb = self.entry.get("thumb") or ""
        if thumb and os.path.isfile(thumb):
            pm = QPixmap(thumb)
            if not pm.isNull():
                return pm
        return None

    def enterEvent(self, e):
        self._hover = True
        self._animate_hover(1.0)

    def leaveEvent(self, e):
        self._hover = False
        self._animate_hover(0.0)

    def _animate_hover(self, to):
        anim.animate(self, self._hover_t, to, 160, self._hover_tick,
                     easing=QEasingCurve.OutCubic, attr="_hover_anim")

    def _hover_tick(self, v):
        self._hover_t = v
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setRenderHint(QPainter.SmoothPixmapTransform, True)
        s = self.app._s
        w = self.width()
        downloading = self._state == "downloading"
        block = QRectF(s(4), s(4), w - s(8), self._h - s(8))

        if self._state == "fetching":
            # подсвеченный блок + по центру вращающийся спиннер и «Fetching…»
            bg = QColor(self._accent)
            bg.setAlpha(28)
            p.setPen(QPen(self._accent, max(1.5, s(1.6))))
            p.setBrush(bg)
            p.drawRoundedRect(block, s(10), s(10))
            self._draw_fetching_content(p, block, s)
            p.end()
            return

        if self._state == "error":
            # анализ не удался — красный блок и крестик по центру
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
            p.end()
            return

        # Переход fetching->pending: обычное содержимое проявляется (opacity),
        # поверх — «Fetching…» уезжает вниз и гаснет.
        trans = self._transition_t
        if trans > 0.0:
            p.setOpacity(1.0 - trans)

        if downloading:
            # весь блок — полоса прогресса (трек + плавная заливка акцентом)
            p.save()
            clip = QPainterPath()
            clip.addRoundedRect(block, s(10), s(10))
            p.setClipPath(clip)
            p.fillRect(block, self._track)
            fill = QColor(self._accent)
            fill.setAlphaF(0.9)
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

        # обложка (скруглённая, кроп по центру; заглушка, пока файла нет)
        tw, th = s(self.THUMB_W), s(self.THUMB_H)
        tx, ty = s(12), (self._h - th) // 2
        rect = QRectF(tx, ty, tw, th)
        path = QPainterPath()
        path.addRoundedRect(rect, s(6), s(6))
        p.save()
        p.setClipPath(path)
        if self._pm is not None:
            scaled = self._pm.scaled(int(tw), int(th), Qt.KeepAspectRatioByExpanding,
                                     Qt.SmoothTransformation)
            p.drawPixmap(int(tx), int(ty), scaled)
        else:
            p.fillRect(rect, QColor("#26262a"))
        p.restore()
        if self.entry.get("is_image"):     # скачанная картинка — янтарная рамка обложки
            p.setPen(QPen(QColor("#ffb020"), max(1.5, s(2.0))))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(rect, s(6), s(6))

        # текст: ссылка (усечена посередине) + площадка
        text_x = tx + tw + s(14)
        if downloading:
            stop_left = w - s(12) - s(34)           # левый край кнопки стоп (см. _layout)
            right = stop_left - s(10)                # текст обрывается перед кнопкой стоп
        else:
            right = self._text_right
        avail = max(s(40), right - text_x)
        # Заголовок — название видео (если известно), иначе ссылка.
        title = self.entry.get("title") or self.entry.get("url", "")
        elide = Qt.ElideRight if self.entry.get("title") else Qt.ElideMiddle

        f_url = fonts.font(s(12), "Medium")
        p.setFont(f_url)
        fm = QFontMetrics(f_url)
        elided = fm.elidedText(title, elide, int(avail))
        p.setPen(self._text_col)
        p.drawText(QRectF(text_x, s(14), avail, s(22)),
                   Qt.AlignVCenter | Qt.AlignLeft, elided)

        f_host = fonts.font(s(10), "Regular")
        p.setFont(f_host)
        p.setPen(self._muted)
        p.drawText(QRectF(text_x, s(38), avail, s(18)),
                   Qt.AlignVCenter | Qt.AlignLeft, self._sub)

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
            p.setOpacity(trans)
            self._draw_fetching_content(p, block, s, dy=(1.0 - trans) * s(24))
            p.setOpacity(1.0)

        # ошибка действия (не удалось удалить и т.п.): лёгкая красная заливка +
        # рамка + краткий текст по центру. Контент остаётся под ней.
        if self._err_t > 0.0:
            wash = QColor(self._err)
            wash.setAlphaF(0.30 * self._err_t)
            wpath = QPainterPath()
            wpath.addRoundedRect(block, s(10), s(10))
            p.fillPath(wpath, wash)
            p.setPen(QPen(self._err, max(1.5, s(1.6))))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(block, s(10), s(10))
            if self._err_text:
                p.setOpacity(min(1.0, self._err_t * 1.4))
                p.setFont(fonts.font(s(11), "Semibold"))
                p.setPen(self._text_col)
                p.drawText(block, Qt.AlignCenter, self._err_text)
                p.setOpacity(1.0)
        p.end()


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
        pal = themes.palette(app.settings.get("theme", themes.DEFAULT_THEME))
        self._bg = QColor(pal["card_bg"])
        self._border = QColor(pal["border"])
        self._rows = []
        self._pad = s(6)
        self._row_h = s(72)

        self._area = QScrollArea(self)
        self._area.setWidgetResizable(False)
        self._area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._area.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self._area.setFrameShape(QFrame.NoFrame)
        self._area.viewport().setStyleSheet("background: transparent;")
        self._area.setStyleSheet(
            "QScrollArea { background: transparent; border: none; }"
            "QScrollBar:vertical { background: transparent; width: 7px; margin: 3px; }"
            f"QScrollBar::handle:vertical {{ background: {pal['muted']};"
            "  border-radius: 3px; min-height: 26px; }"
            "QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }"
            "QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }")
        self._content = QWidget()
        self._content.setStyleSheet("background: transparent;")
        self._area.setWidget(self._content)
        self._smooth_scroll = SmoothScroll(self._area, parent=self)

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

    def _make_row(self, entry, downloading=False, pending=False, fetching=False):
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

    def rebuild(self, entries):
        # Строки активных загрузок не хранятся в json — сохраняем их объекты
        # (их worker->row связи должны жить) и держим сверху.
        keep = [r for r in self._rows if r.is_downloading() or r.is_pending()
                or r.is_fetching() or r.is_error()]
        for r in self._rows:
            if r not in keep:
                r.setParent(None)
                r.deleteLater()
        self._content.setFixedWidth(self._row_width())
        made = [self._make_row(e) for e in entries]
        self._rows = keep + made
        for r in self._rows:
            r.set_width(self._row_width())
        self._reflow(animate=False)

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
        gone = [r for r in self._rows
                if not r.is_downloading() and not r.is_pending()
                and not r.is_fetching() and not r.is_error()
                and not (r.entry.get("path") and os.path.isfile(r.entry["path"]))]
        for r in gone:
            self.remove_row(r)
        return [r.entry.get("id") for r in gone]

    def flash_error(self, entry_id, text):
        """Подсветить строку с данным id красным + текстом (действие не удалось)."""
        for r in self._rows:
            if r.entry.get("id") == entry_id:
                r.flash_error(text)
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
