"""
Панель обрезки Spotlight: превью видео + лента кадров (filmstrip) с двумя
жёлтыми ручками диапазона + play/pause + копировать/сохранить.

Превью и воспроизведение — через QMediaPlayer (реальный звук + видео); при
перетаскивании ручки просто перематываем плеер на нужную позицию. Видео рисует
сам Qt (QVideoWidget) — покадровая конвертация в QImage на UI-потоке съедала
память на 4K. Аудио — волна из пиков поверх QLabel. Filmstrip — ffmpeg, один раз.
Обрезка — trimmer.trim (-ss/-t -c copy).
"""

import os

from PySide6.QtCore import (
    Qt, QRectF, QUrl, QThread, Signal, QPointF, QTimer, QEasingCurve, QEvent
)
from PySide6.QtGui import (
    QPainter, QColor, QPen, QPixmap, QPolygonF, QPainterPath, QFontMetrics
)
from PySide6.QtWidgets import QWidget, QLabel
from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput
from PySide6.QtMultimediaWidgets import QVideoWidget

from core import fonts, themes, trimmer, tools
from core.i18n import tr
from ui import anim
from ui.widgets import LinkButton


def _fmt_time(sec):
    sec = max(0, int(sec or 0))
    return f"{sec // 60}:{sec % 60:02d}"


_AUDIO_EXT = {".mp3", ".wav", ".flac", ".aac", ".ogg", ".opus", ".m4a", ".wma"}


def _is_audio_file(path):
    return os.path.splitext(path or "")[1].lower() in _AUDIO_EXT


class _PeaksWorker(QThread):
    """Фоновый расчёт пиков (для записей без готового peak-файла) — чтобы открытие
    обрезки не морозило UI. Кэширует результат."""
    done = Signal(list, str)         # peaks, path (для проверки актуальности)

    def __init__(self, path, cache, parent=None):
        super().__init__(parent)
        self._path, self._cache = path, cache

    def run(self):
        try:
            pk = trimmer.audio_peaks(self._path)
            if pk and self._cache:
                trimmer.save_peaks(pk, self._cache)
        except Exception:
            pk = []
        self.done.emit(pk, self._path)


class _VolumeSlider(QWidget):
    """Громкость превью: иконка динамика + тонкий трек. Клик/перетаскивание."""

    changed = Signal(float)

    def __init__(self, app, value, parent=None):
        super().__init__(parent)
        self.app = app
        self._v = max(0.0, min(1.0, value))
        self._drag = False
        self._hover = False
        pal = themes.palette(app.settings.get("theme", themes.DEFAULT_THEME))
        self._muted = QColor(pal["muted"])
        self._accent = QColor(pal["accent"])
        self._track = QColor(pal["sel_chip"])
        self.setCursor(Qt.PointingHandCursor)
        self.setMouseTracking(True)

    def value(self):
        return self._v

    def _icon_w(self):
        return self.app._s(18)

    def _bar_rect(self):
        s = self.app._s
        x = self._icon_w() + s(4)
        # Справа оставляем место под ручку (радиус s(4)) — иначе она обрезалась.
        return QRectF(x, (self.height() - s(4)) / 2.0,
                      max(1, self.width() - x - s(6)), s(4))

    def _set_from_x(self, x):
        r = self._bar_rect()
        v = (x - r.left()) / max(1.0, r.width())
        self._v = max(0.0, min(1.0, v))
        self.update()
        self.changed.emit(self._v)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._drag = True
            self._set_from_x(e.position().x())

    def mouseMoveEvent(self, e):
        if self._drag:
            self._set_from_x(e.position().x())

    def mouseReleaseEvent(self, e):
        self._drag = False

    def enterEvent(self, e):
        self._hover = True
        self.update()

    def leaveEvent(self, e):
        self._hover = False
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        s = self.app._s
        col = QColor(self._muted)
        r = self._bar_rect()
        # Динамик: корпус + рупор; при нуле — крестик рядом.
        cy = self.height() / 2.0
        bw = s(4)
        p.setPen(Qt.NoPen)
        p.setBrush(col)
        p.drawRect(QRectF(s(2), cy - s(3), bw, s(6)))
        horn = QPolygonF([QPointF(s(2) + bw, cy - s(3)), QPointF(s(11), cy - s(7)),
                          QPointF(s(11), cy + s(7)), QPointF(s(2) + bw, cy + s(3))])
        p.drawPolygon(horn)
        if self._v <= 0.001:
            p.setPen(QPen(col, max(1.2, s(1.4))))
            p.drawLine(int(s(13)), int(cy - s(4)), int(s(17)), int(cy + s(4)))
            p.drawLine(int(s(17)), int(cy - s(4)), int(s(13)), int(cy + s(4)))
        # Трек + заполнение + ручка.
        p.setPen(Qt.NoPen)
        p.setBrush(self._track)
        p.drawRoundedRect(r, r.height() / 2, r.height() / 2)
        fill = QRectF(r.left(), r.top(), r.width() * self._v, r.height())
        p.setBrush(self._accent)
        p.drawRoundedRect(fill, r.height() / 2, r.height() / 2)
        if self._hover or self._drag:
            kx = r.left() + r.width() * self._v
            p.setBrush(self._accent)
            p.drawEllipse(QPointF(kx, r.center().y()), s(4), s(4))
        p.end()


class _PreviewPlayhead(QWidget):
    """Прозрачный оверлей поверх превью: вертикальный плейхед, синхронный с лентой;
    перетаскивается (seek). Активен для аудио (waveform-превью)."""

    def __init__(self, trim):
        super().__init__(trim)
        self._trim = trim
        self._drag = False

    def _active(self):
        return self._trim._is_audio and self._trim._bar._dur > 0

    def _view(self):
        """Видимый диапазон превью (сек). При отсутствии зума — вся дорожка."""
        t = self._trim
        vs, ve = t._view_start, t._view_end
        if ve <= vs:
            return 0.0, t._bar._dur
        return vs, ve

    def _seek(self, x):
        t = self._trim
        if t._bar._dur <= 0:
            return
        vs, ve = self._view()
        frac = max(0.0, min(1.0, x / max(1, self.width())))
        sec = vs + frac * (ve - vs)
        t._bar.set_play_pos(sec)
        self.update()
        t._on_scrub(sec)

    def mousePressEvent(self, e):
        if self._active():
            self._drag = True
            self._seek(e.position().x())

    def mouseMoveEvent(self, e):
        if self._drag:
            self._seek(e.position().x())

    def mouseReleaseEvent(self, e):
        self._drag = False

    def paintEvent(self, event):
        if not self._active():
            return
        t = self._trim
        vs, ve = self._view()
        if ve <= vs:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        s = t.app._s
        w = self.width()
        h = self.height()
        span = ve - vs
        # Клип по скруглению превью — волна/затемнение не заезжают за углы.
        clip = QPainterPath()
        clip.addRoundedRect(QRectF(0, 0, w, h), s(10), s(10))
        p.setClipPath(clip)
        # Волна из пиков для видимого диапазона — чётко на любом зуме (без ffmpeg).
        # _sync сдвигает ВОЛНУ ВПРАВО (не ползунок): пик встаёт под звук/ползунок.
        # Так ползунок остаётся честным (без скачка при старте, точный seek).
        if t._peaks and t._bar._dur > 0:
            ln = len(t._peaks)
            i0 = int(max(0.0, vs - t._sync) / t._bar._dur * ln)
            i1 = int(max(0.0, ve - t._sync) / t._bar._dur * ln)
            t._paint_peaks(p, QRectF(0, 0, w, h), i0, i1)
        # Затемняем обрезаемые части (вне выбранного [start, end]).
        sx = int((t._bar._start - vs) / span * w)
        ex = int((t._bar._end - vs) / span * w)
        dark = QColor(0, 0, 0, 120)
        if sx > 0:
            p.fillRect(0, 0, min(sx, w), h, dark)
        if ex < w:
            p.fillRect(max(0, ex), 0, w - max(0, ex), h, dark)
        # Плейхед (тёмный контур + белая линия) — честная позиция плеера.
        x = int((t._bar._play - vs) / span * w)
        if 0 <= x <= self.width():
            p.setPen(QPen(QColor(0, 0, 0, 150), max(3.0, s(3))))
            p.drawLine(x, 0, x, self.height())
            p.setPen(QPen(QColor("#ffffff"), max(1.5, s(2))))
            p.drawLine(x, 0, x, self.height())
        p.end()


# ------------------------------------------------------------------ #
# ------------------------------------------------------------------ #
class FilmstripBar(QWidget):
    """Лента кадров с жёлтой рамкой диапазона и двумя ручками (||).
    Значения start/end — в секундах; движок сообщает наружу сигналами."""

    rangeChanged = Signal(float, float)   # (start, end) — при отпускании ручки
    scrub = Signal(float)                 # позиция плейхеда (перетаскивание/клик)

    YELLOW = QColor("#FFCC00")

    def __init__(self, app, parent=None):
        super().__init__(parent)
        self.app = app
        s = app._s
        self._strip = None            # QPixmap ленты кадров
        self._dur = 0.0
        self._start = 0.0
        self._end = 0.0
        self._play = 0.0              # позиция плейхеда
        self._hpad = s(12)           # ширина ручки
        self._drag = None            # 'l' | 'r' | None
        self._strip_alpha = 1.0      # плавное проявление ленты кадров
        # Видимый в превью диапазон (сек) — мини-карта зума. Пусто => зума нет.
        self._view_start = 0.0
        self._view_end = 0.0
        self.setMouseTracking(True)
        self.setMinimumHeight(s(56))

    def set_view(self, vs, ve):
        """Какой участок дорожки сейчас видно в превью (для рамки-мини-карты)."""
        if (vs, ve) != (self._view_start, self._view_end):
            self._view_start, self._view_end = vs, ve
            self.update()

    def _zoomed(self):
        span = self._view_end - self._view_start
        return 0 < span < self._dur - 1e-3

    def set_video(self, dur, strip_pixmap=None):
        self._dur = max(0.0, float(dur or 0.0))
        self._start = 0.0
        self._end = self._dur
        self._play = 0.0
        # Новый файл — зум прошлого не наследуем (иначе на видео оставалась
        # рамка мини-карты от зума, сделанного до этого на аудио).
        self._view_start = self._view_end = 0.0
        if strip_pixmap is not None:
            self._strip = strip_pixmap
        self.update()

    def set_strip(self, pixmap):
        self._strip = pixmap
        self._strip_alpha = 0.0
        anim.animate(self, 0.0, 1.0, 320, self._strip_fade,
                     easing=QEasingCurve.OutCubic, attr="_strip_anim")

    def _strip_fade(self, v):
        self._strip_alpha = v
        self.update()

    def set_play_pos(self, sec):
        self._play = max(0.0, min(float(sec or 0.0), self._dur))
        self.update()

    def range(self):
        return self._start, self._end

    # --- геометрия ----------------------------------------------------- #
    def _track_rect(self):
        return QRectF(self._hpad, 0, max(1, self.width() - 2 * self._hpad),
                      self.height())

    def _x_for(self, sec):
        t = self._track_rect()
        if self._dur <= 0:
            return t.left()
        return t.left() + (sec / self._dur) * t.width()

    def _sec_for(self, x):
        t = self._track_rect()
        if t.width() <= 0 or self._dur <= 0:
            return 0.0
        return max(0.0, min(self._dur, (x - t.left()) / t.width() * self._dur))

    # --- мышь ---------------------------------------------------------- #
    def mousePressEvent(self, e):
        x = e.position().x()
        lx, rx = self._x_for(self._start), self._x_for(self._end)
        grab = self.app._s(16)
        if abs(x - lx) <= grab:
            self._drag = "l"
        elif abs(x - rx) <= grab:
            self._drag = "r"
        else:
            # клик/перетаскивание по ленте двигает плейхед (превью)
            self._drag = "seek"
            self._play = min(max(self._sec_for(x), self._start), self._end)
            self.scrub.emit(self._play)
            self.update()

    def mouseMoveEvent(self, e):
        if self._drag is None:
            x = e.position().x()
            near = (abs(x - self._x_for(self._start)) <= self.app._s(16)
                    or abs(x - self._x_for(self._end)) <= self.app._s(16))
            self.setCursor(Qt.SizeHorCursor if near else Qt.PointingHandCursor)
            return
        if self._drag == "seek":
            self._play = min(max(self._sec_for(e.position().x()),
                                 self._start), self._end)
            self.scrub.emit(self._play)
            self.update()
            return
        sec = self._sec_for(e.position().x())
        min_gap = max(0.2, self._dur * 0.01)
        # Ручка не трогает независимый плейхед, ПОКА не наедет на него: тогда
        # толкает его дальше (плейхед всегда внутри [start, end]).
        if self._drag == "l":
            self._start = max(0.0, min(sec, self._end - min_gap))
            if self._play < self._start:
                self._play = self._start
                self.scrub.emit(self._play)      # толкнули — перемотать плеер
        else:
            self._end = min(self._dur, max(sec, self._start + min_gap))
            if self._play > self._end:
                self._play = self._end
                self.scrub.emit(self._play)
        self.rangeChanged.emit(self._start, self._end)   # живьём — обновить затемнение
        self.update()

    def mouseReleaseEvent(self, e):
        if self._drag in ("l", "r"):
            self.rangeChanged.emit(self._start, self._end)
        self._drag = None

    # --- отрисовка ----------------------------------------------------- #
    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        s = self.app._s
        t = self._track_rect()
        r = s(8)

        # 1. Лента кадров (или тёмная заглушка), скруглённая.
        path_rect = QRectF(t)
        p.save()
        clip = QRectF(path_rect)
        from PySide6.QtGui import QPainterPath
        pp = QPainterPath()
        pp.addRoundedRect(clip, r, r)
        p.setClipPath(pp)
        p.fillRect(clip, QColor("#1b1b1d"))         # фон-заглушка всегда
        if self._strip is not None and not self._strip.isNull():
            scaled = self._strip.scaled(int(t.width()), int(t.height()),
                                        Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
            p.setOpacity(self._strip_alpha)         # плавное проявление кадров
            p.drawPixmap(int(t.left()), int(t.top()), scaled)
            p.setOpacity(1.0)
        # затемняем области вне выбранного диапазона
        lx, rx = self._x_for(self._start), self._x_for(self._end)
        dim = QColor(0, 0, 0, 130)
        p.fillRect(QRectF(t.left(), t.top(), lx - t.left(), t.height()), dim)
        p.fillRect(QRectF(rx, t.top(), t.right() - rx, t.height()), dim)
        # Мини-карта зума: какой участок дорожки сейчас видно в превью. Без зума
        # (виден весь трек) рамку не рисуем.
        if self._zoomed():
            vx0, vx1 = self._x_for(self._view_start), self._x_for(self._view_end)
            vr = QRectF(vx0, t.top() + s(2), max(s(2), vx1 - vx0), t.height() - s(4))
            p.fillRect(vr, QColor(255, 255, 255, 40))
            p.setPen(QPen(QColor(255, 255, 255, 200), max(1.0, s(1.4))))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(vr, s(3), s(3))
        p.restore()

        # 2. Жёлтая рамка вокруг выбранного диапазона.
        sel = QRectF(lx, 0.5, rx - lx, self.height() - 1)
        pen = QPen(self.YELLOW, s(3))
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(sel, r, r)

        # 3. Плейхед (белая линия) внутри диапазона.
        if self._end > self._start:
            px = self._x_for(self._play)
            p.setPen(QPen(QColor(255, 255, 255, 220), s(2)))
            p.drawLine(QPointF(px, s(4)), QPointF(px, self.height() - s(4)))

        # 4. Ручки (жёлтые прямоугольники с насечкой ||).
        for hx in (lx, rx):
            hr = QRectF(hx - self._hpad / 2, 0, self._hpad, self.height())
            p.setPen(Qt.NoPen)
            p.setBrush(self.YELLOW)
            p.drawRoundedRect(hr, s(3), s(3))
            p.setPen(QPen(QColor(40, 30, 0), max(1, s(1.4))))
            gx = hr.center().x()
            gy0, gy1 = hr.center().y() - s(6), hr.center().y() + s(6)
            p.drawLine(QPointF(gx - s(2), gy0), QPointF(gx - s(2), gy1))
            p.drawLine(QPointF(gx + s(2), gy0), QPointF(gx + s(2), gy1))
        p.end()


# ------------------------------------------------------------------ #
class _CtrlButton(QWidget):
    """Небольшая круглая/скруглённая кнопка с рисуемым глифом (play/pause/copy/save)."""
    clicked = Signal()

    def __init__(self, app, glyph, parent=None, accent=False):
        super().__init__(parent)
        self.app = app
        self._glyph = glyph          # 'play' | 'pause' | 'copy' | 'save'
        self._accent = accent
        self._hover = False
        self._pressed = False
        s = app._s
        self.setFixedSize(s(34), s(34))
        self.setCursor(Qt.PointingHandCursor)
        pal = themes.palette(app.settings.get("theme", themes.DEFAULT_THEME))
        self._fg = QColor(pal["text"])
        self._hover_bg = QColor(pal["sel_chip"]).lighter(140)
        self._accent_col = QColor(pal["accent"])
        # копирование — та же иконка, что в истории (copy.png), перекрашенная.
        self._pm = None
        if glyph == "copy":
            from core.icons import themed_pixmap
            self._pm = themed_pixmap(app.settings.get("theme", themes.DEFAULT_THEME),
                                     "copy.png", pal["text"], s(16))

    def set_glyph(self, g):
        self._glyph = g
        self.update()

    def enterEvent(self, e):
        self._hover = True
        self.update()

    def leaveEvent(self, e):
        self._hover = False
        self.update()

    def mousePressEvent(self, e):
        # Клик засчитываем только если было нажатие ИМЕННО на этой кнопке —
        # иначе случайный release (напр., кнопка «подъехала» под курсор при
        # анимации) мог бы ложно запустить сохранение/копирование.
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
        s = self.app._s
        w, h = self.width(), self.height()
        if self._hover or self._accent:
            inset = s(3)                 # подложка чуть меньше габаритов кнопки
            p.setPen(Qt.NoPen)
            p.setBrush(self._accent_col if self._accent else self._hover_bg)
            p.drawRoundedRect(QRectF(inset, inset, w - 2 * inset, h - 2 * inset),
                              s(8), s(8))
        col = QColor("#ffffff") if self._accent else self._fg
        # копирование — иконкой из assets (как в истории)
        if self._glyph == "copy" and self._pm is not None and not self._pm.isNull():
            p.drawPixmap(int((w - self._pm.width()) / 2),
                         int((h - self._pm.height()) / 2), self._pm)
            p.end()
            return
        p.setPen(QPen(col, max(1.6, s(1.8))))
        cx, cy = w / 2, h / 2
        g = self._glyph
        if g == "play":
            from PySide6.QtGui import QPolygonF
            p.setPen(Qt.NoPen)
            p.setBrush(col)
            tri = QPolygonF([QPointF(cx - s(5), cy - s(7)),
                             QPointF(cx - s(5), cy + s(7)),
                             QPointF(cx + s(8), cy)])
            p.drawPolygon(tri)
        elif g == "pause":
            p.setPen(Qt.NoPen)
            p.setBrush(col)
            p.drawRoundedRect(QRectF(cx - s(6), cy - s(7), s(4), s(14)), s(1.5), s(1.5))
            p.drawRoundedRect(QRectF(cx + s(2), cy - s(7), s(4), s(14)), s(1.5), s(1.5))
        elif g == "copy":
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(QRectF(cx - s(7), cy - s(4), s(10), s(11)), s(2), s(2))
            p.drawRoundedRect(QRectF(cx - s(3), cy - s(8), s(10), s(11)), s(2), s(2))
        elif g == "save":
            # стрелка вниз в «лоток»
            p.drawLine(QPointF(cx, cy - s(8)), QPointF(cx, cy + s(3)))
            p.drawLine(QPointF(cx - s(4), cy - s(1)), QPointF(cx, cy + s(3)))
            p.drawLine(QPointF(cx + s(4), cy - s(1)), QPointF(cx, cy + s(3)))
            p.drawLine(QPointF(cx - s(7), cy + s(7)), QPointF(cx + s(7), cy + s(7)))
        p.end()


# ------------------------------------------------------------------ #
class _ConfirmOverlay(QWidget):
    """Подтверждение поверх панели обрезки: затемнение + карточка с вопросом и
    кнопками «Отмена» / «Сбросить». Используется при смене файла, если ползунки
    обрезки уже двигали."""

    def __init__(self, app, parent):
        super().__init__(parent)
        self.app = app
        self.hide()
        s = app._s
        pal = themes.palette(app.settings.get("theme", themes.DEFAULT_THEME))
        self._card_bg = QColor(pal["field_bg"])
        self._border = QColor(pal["border"])
        self._title_col = QColor(pal["title"])
        self._title = ""
        self._on_yes = None
        self._card = QRectF()
        self._btn_cancel = LinkButton(
            self, tr("Cancel"), fonts.font(s(11), "Semibold"),
            pal["muted"], pal["text"], self._cancel,
            hover_bg=pal["choose_bg_h"], radius=s(7), base_bg=pal["sel_chip"])
        self._btn_yes = LinkButton(
            self, tr("Discard"), fonts.font(s(11), "Semibold"),
            pal["on_accent"], pal["on_accent"], self._yes,
            hover_bg=pal["accent_hover"], radius=s(7), base_bg=pal["accent"])

    def ask(self, title, on_yes):
        self._title = title
        self._on_yes = on_yes
        self.setGeometry(0, 0, self.parent().width(), self.parent().height())
        self._layout()
        self.show()
        self.raise_()
        anim.fade(self, 0.0, 1.0, 180)

    def _layout(self):
        s = self.app._s
        w, h = self.width(), self.height()
        cw, ch = s(264), s(122)
        cx, cy = (w - cw) // 2, (h - ch) // 2
        self._card = QRectF(cx, cy, cw, ch)
        bw, bh, gap = s(100), s(30), s(10)
        by = cy + ch - bh - s(16)
        self._btn_cancel.setGeometry(int(cx + cw / 2 - bw - gap / 2), int(by), bw, bh)
        self._btn_yes.setGeometry(int(cx + cw / 2 + gap / 2), int(by), bw, bh)

    def _cancel(self):
        self._dismiss()

    def _yes(self):
        cb = self._on_yes
        self._dismiss()
        if cb:
            cb()

    def _dismiss(self):
        anim.fade(self, 1.0, 0.0, 140, on_finished=self.hide)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        s = self.app._s
        p.fillRect(self.rect(), QColor(0, 0, 0, 150))
        p.setPen(QPen(self._border, 1))
        p.setBrush(self._card_bg)
        p.drawRoundedRect(self._card, s(14), s(14))
        p.setFont(fonts.font(s(12), "Semibold"))
        p.setPen(self._title_col)
        p.drawText(QRectF(self._card.left(), self._card.top() + s(24),
                          self._card.width(), s(44)),
                   Qt.AlignHCenter | Qt.AlignTop, self._title)
        p.end()


# ------------------------------------------------------------------ #
class TrimPanel(QWidget):
    """Превью + лента обрезки + управление. Открывается для конкретного файла."""

    closed = Signal()
    saved = Signal(str)          # путь сохранённого фрагмента
    copied = Signal(str)         # путь скопированного во временный файл фрагмента

    def __init__(self, app, parent=None):
        super().__init__(parent)
        self.app = app
        s = app._s
        pal = themes.palette(app.settings.get("theme", themes.DEFAULT_THEME))
        self._bg = QColor(pal["card_bg"])
        self._border = QColor(pal["border"])
        self._muted = QColor(pal["muted"])
        self._path = None
        self._dur = 0.0
        self._trim_worker = None
        self._busy = False
        self._abandoned = False          # обрезку бросили (закрыли панель) -> игнор
        self._dirty = False              # двигали ли ползунки обрезки
        self._is_audio = False           # аудиофайл — превью/лента = waveform
        self._custom_strip = None        # готовая лента (waveform) вместо кадров
        self._view_start = 0.0           # видимый диапазон превью (сек) — зум/пан
        self._view_end = 0.0
        self._peaks = []                 # пики амплитуды (рисуем волну без ffmpeg)
        self._peaks_worker = None
        self._sync = 0.0                # сдвиг ВОЛНЫ влево (сек), чтобы пик встал
        #                                  под слышимый звук/ползунок — подстроить

        # --- виджеты ------------------------------------------------------
        # Видео рендерит сам Qt (QVideoWidget): без покадровых QImage/QPixmap на
        # UI-потоке. Раньше 4K-кадры конвертировались вручную (33 МБ на кадр),
        # UI не успевал, очередь videoFrameChanged росла и съедала всю память.
        self._video = QVideoWidget(self)
        self._video.setStyleSheet("background: #000000;")
        self._video.hide()
        self._anim_busy = False          # идёт анимация панели -> видео скрыто
        self._preview = QLabel(self)         # аудио: фон под волну (и заглушка)
        self._preview.setAlignment(Qt.AlignCenter)
        self._preview.setStyleSheet("background: #000000; border-radius: %dpx;" % s(10))

        # --- плеер (звук + видео через QVideoWidget) ----------------------
        self._player = self._audio = None
        self._volume = float(app.settings.get("trim_volume", 0.8))
        self._build_player()
        self._vol = _VolumeSlider(app, self._volume, self)
        self._vol.changed.connect(self._on_volume)
        self._ph = _PreviewPlayhead(self)    # плейхед поверх превью (аудио-waveform)
        self._ph.hide()
        # Alt+колесо над превью-волной = зум. Фильтр на уровне приложения ловит
        # событие ДО любого перехвата скроллом/окном (Qt авто-снимет при удалении).
        from PySide6.QtWidgets import QApplication
        QApplication.instance().installEventFilter(self)

        self._btn_play = _CtrlButton(app, "play", self)
        self._btn_play.clicked.connect(self._toggle_play)
        self._btn_copy = _CtrlButton(app, "copy", self)
        self._btn_copy.clicked.connect(lambda: self._export(copy=True))
        self._btn_save = _CtrlButton(app, "save", self, accent=True)
        self._btn_save.clicked.connect(lambda: self._export(copy=False))

        # In/Out — двигают край диапазона к плейхеду.
        from PySide6.QtWidgets import QPushButton
        chip = QColor(pal["sel_chip"])

        def _mini(text, cb):
            b = QPushButton(text, self)
            b.setCursor(Qt.PointingHandCursor)
            b.setFocusPolicy(Qt.NoFocus)
            b.setFont(fonts.font(s(11), "Semibold"))
            b.setStyleSheet(
                "QPushButton { background: %s; border: none; border-radius: %dpx; "
                "color: %s; } QPushButton:hover { background: %s; }"
                % (chip.name(), s(7), pal["text"], chip.lighter(130).name()))
            b.clicked.connect(cb)
            return b
        self._btn_in = _mini("In", self.set_in)
        self._btn_out = _mini("Out", self.set_out)

        self._bar = FilmstripBar(app, self)
        self._bar.scrub.connect(self._on_scrub)
        self._bar.rangeChanged.connect(self._on_range)   # двинули ползунок -> «грязно»
        # Перемотку плеера при скрабе троттлим — иначе быстрые setPosition
        # исчерпывают пул кадров аппаратного декодера (vp9 get_buffer failed).
        self._scrub_pending = None
        self._scrub_timer = QTimer(self)
        self._scrub_timer.setSingleShot(True)
        self._scrub_timer.setInterval(45)
        self._scrub_timer.timeout.connect(self._do_scrub)

        self._time_lbl = QLabel("", self)
        self._time_lbl.setFont(fonts.font(s(10), "Medium"))
        self._time_lbl.setStyleSheet(f"color: {pal['muted']}; background: transparent;")

        self._confirm = _ConfirmOverlay(app, self)

    def set_in(self):
        b = self._bar
        gap = max(0.2, b._dur * 0.01)
        b._start = max(0.0, min(b._play, b._end - gap))
        b._play = b._start
        b.update()
        b.rangeChanged.emit(b._start, b._end)

    def set_out(self):
        b = self._bar
        gap = max(0.2, b._dur * 0.01)
        b._end = min(b._dur, max(b._play, b._start + gap))
        b.update()
        b.rangeChanged.emit(b._start, b._end)

    # --- зум/пан waveform в превью (как таймлайн Premiere) -------------- #
    def _update_wave_view(self):
        """Превью для аудио рисует _ph из пиков — просто перерисовываем.
        Заодно отдаём ленте видимый диапазон (рамка-мини-карта зума)."""
        if self._is_audio:
            self._bar.set_view(self._view_start, self._view_end)
            self._ph.update()

    def _paint_peaks(self, p, rect, i0, i1):
        """Рисует ГЛАДКУЮ огибающую волны из пиков self._peaks для среза [i0, i1].
        Даунсэмпл: пиксель шире пика -> max по срезу (огибающая); пиксель уже пика
        -> линейная интерполяция (сглаживание при зуме)."""
        peaks = self._peaks
        n = i1 - i0
        if n <= 0 or not peaks:
            return
        cy = rect.center().y()
        half = rect.height() / 2.0 * 0.92
        w = max(1, int(rect.width()))
        left = rect.left()
        ln = len(peaks)
        top, bot = [], []
        for px in range(w + 1):
            f0 = i0 + px / w * n
            f1 = i0 + (px + 1) / w * n
            a0, a1 = int(f0), int(f1)
            if a1 > a0:                       # пиксель покрывает несколько пиков
                seg = peaks[max(0, a0):min(ln, a1 + 1)]
                v = max(seg) if seg else 0.0
            else:                             # зум — интерполяция между соседями
                i = min(ln - 1, max(0, a0))
                fr = f0 - a0
                v = peaks[i] * (1 - fr) + peaks[min(ln - 1, i + 1)] * fr
            x = left + px
            top.append(QPointF(x, cy - v * half))
            bot.append(QPointF(x, cy + v * half))
        poly = QPolygonF(top + list(reversed(bot)))
        col = QColor("#8ab4f8"); col.setAlpha(235)
        p.setPen(Qt.NoPen)
        p.setBrush(col)
        p.drawPolygon(poly)

    def _on_peaks_ready(self, peaks, path):
        if path != self._path or not peaks:      # волна нужна и видео (лента)
            return
        self._peaks = peaks
        self._custom_strip = self._peaks_pixmap(1400, 200)
        if self._bar._dur > 0:
            self._bar.set_strip(self._custom_strip)   # лента-обзор
        self._ph.update()

    def _peaks_pixmap(self, w, h):
        pm = QPixmap(int(w), int(h))
        pm.fill(QColor("#000000"))
        if self._peaks:
            p = QPainter(pm)
            p.setRenderHint(QPainter.Antialiasing, True)
            self._paint_peaks(p, QRectF(0, 0, w, h), 0, len(self._peaks))
            p.end()
        return pm

    def _zoom_wave(self, delta, frac_x):
        dur = self._bar._dur
        if dur <= 0:
            return
        span = (self._view_end - self._view_start) or dur
        center = self._view_start + frac_x * span
        factor = 0.8 if delta > 0 else 1.25          # вперёд = приблизить
        min_span = min(dur, max(1.0, dur * 0.01))
        new_span = max(min_span, min(dur, span * factor))
        self._view_start = max(0.0, center - frac_x * new_span)
        self._view_end = min(dur, self._view_start + new_span)
        self._view_start = max(0.0, self._view_end - new_span)
        self._update_wave_view()
        self._ph.update()

    def _pan_wave(self, delta):
        """Пан видимой области при зуме: колесо вверх = влево, вниз = вправо."""
        dur = self._bar._dur
        span = self._view_end - self._view_start
        if dur <= 0 or span <= 0 or span >= dur - 1e-3:
            return
        step = span * 0.15
        ns = self._view_start - step if delta > 0 else self._view_start + step
        ns = max(0.0, min(dur - span, ns))
        self._view_start = ns
        self._view_end = ns + span
        self._update_wave_view()
        self._ph.update()

    def _follow_playhead(self):
        """Плейхед вышел за видимую область — перелистываем её «страницей».

        Плейхед НЕ тянет зону за собой у края: дошёл до правого края — зона
        перескакивает так, что он оказывается у левого (и наоборот при движении
        назад). Так волна не ползёт непрерывно под неподвижным ползунком."""
        if not self._is_audio:
            return
        dur = self._bar._dur
        if dur <= 0 or self._view_end <= self._view_start:
            return
        span = self._view_end - self._view_start
        if span >= dur - 1e-3:
            return                                   # полный вид — не скроллим
        pos = self._bar._play
        if pos > self._view_end:
            start = min(pos, max(0.0, dur - span))   # плейхед -> левый край
        elif pos < self._view_start:
            start = max(0.0, pos - span)             # плейхед -> правый край
        else:
            return
        self._view_start = start
        self._view_end = min(dur, start + span)
        self._view_start = max(0.0, self._view_end - span)
        self._update_wave_view()

    # --- защита при смене файла обрезки -------------------------------- #
    def current_path(self):
        return self._path

    def is_dirty(self):
        return self._dirty

    def _on_range(self, a, b):
        self._dirty = True
        self._update_time()                 # таймкоды меняются под ползунки вживую
        if self._is_audio:
            self._ph.update()               # перерисовать затемнение обрезаемых зон

    def confirm_switch(self, title, on_yes):
        self._confirm.ask(title, on_yes)

    # --- открытие/закрытие --------------------------------------------- #
    def target_height(self):
        s = self.app._s
        # +s(12) — компенсация увеличенного отступа (см. _layout), чтобы превью
        # не потеряло высоту из-за ухода от краёв.
        return s(280) + s(56) + s(44) + s(30) + s(12)

    def open_for(self, path, waveform=None):
        # Тот же файл, что открывали в прошлый раз: НЕ чистим превью и filmstrip —
        # прошлый кадр/полоса остаются на месте, а новый кадр 0 идентичен, поэтому
        # реоткрытие без черноты и моргания «как будто перезапустилась обрезка».
        # (Файл при закрытии выгружался, тут снова загружаем — блокировки нет.)
        same = bool(path and self._path
                    and os.path.normpath(path) == os.path.normpath(self._path))
        self._path = path
        self._busy = False
        self._abandoned = False
        self._dirty = False              # новый файл — изменений ещё нет
        self._is_audio = _is_audio_file(path)
        self._custom_strip = None
        self._btn_play.set_glyph("play")
        # Видео рендерит QVideoWidget, аудио — волна на _preview/_ph. Во время
        # анимации раскрытия видео держим скрытым (см. begin_anim).
        self._video.setVisible(not self._is_audio and not self._anim_busy)
        self._preview.setVisible(self._is_audio)
        if not same:
            self._preview.clear()
            self._bar._strip = None
            self._bar.set_video(0.0, None)
        # Волна нужна и видео (лента = звуковая дорожка вместо кадров): ffmpeg
        # достаёт аудиодорожку из любого контейнера.
        self._view_start = self._view_end = 0.0   # сброс зума
        if self._is_audio:
            self._preview.clear()            # превью рисует _ph из пиков
        self._peaks = (trimmer.load_peaks(waveform)
                       if (waveform and os.path.isfile(waveform)) else [])
        if not self._peaks:              # нет готового — пробуем кэш, иначе фон
            import tempfile
            import hashlib
            # md5 пути — стабильный ключ (hash() солится per-process,
            # кэш не переживал бы перезапуск -> опять 5с чёрного экрана).
            key = hashlib.md5(
                os.path.normpath(path or "").encode("utf-8", "replace")).hexdigest()
            cache = os.path.join(
                tempfile.gettempdir(), "snatchr_pk_%s.peaks" % key[:16])
            if os.path.isfile(cache):
                self._peaks = trimmer.load_peaks(cache)
            if not self._peaks and path:  # считаем в фоне — UI не морозим
                self._peaks_worker = _PeaksWorker(path, cache, self)
                self._peaks_worker.done.connect(self._on_peaks_ready)
                self._peaks_worker.start()
        self._custom_strip = (self._peaks_pixmap(1400, 200)
                              if self._peaks else None)
        self._ph.update()
        if path and os.path.isfile(path):
            self._player.setSource(QUrl.fromLocalFile(os.path.abspath(path)))
        self._player.setPosition(0)
        self._player.pause()
        self._layout()


    def begin_anim(self):
        """Панель раскрывается/схлопывается: прячем видеовиджет.

        QVideoWidget — нативное окно: родитель не может ни обрезать его по своей
        высоте, ни применить к нему анимацию. Поэтому во время анимации он
        появлялся сразу целиком (и пропадал последним). Прячем на это время."""
        self._anim_busy = True
        self._video.hide()

    def end_anim(self):
        """Анимация закончилась — возвращаем видеовиджет (если файл видео)."""
        self._anim_busy = False
        self._video.setVisible(not self._is_audio and bool(self._path))

    def _build_player(self):
        """(Пере)создаёт QMediaPlayer + звук + видеовыход и подключает сигналы."""
        self._player = QMediaPlayer(self)
        self._audio = QAudioOutput(self)
        self._audio.setVolume(self._volume)
        self._player.setAudioOutput(self._audio)
        self._player.setVideoOutput(self._video)
        self._player.durationChanged.connect(self._on_duration)
        self._player.positionChanged.connect(self._on_position)
        self._player.playbackStateChanged.connect(self._on_state)

    def _reset_player(self):
        """Уничтожает плеер/звук и создаёт заново — гарантированно роняет
        файловый хендл (Qt FFmpeg-backend на Windows держит демуксер открытым до
        УНИЧТОЖЕНИЯ QMediaPlayer; setSource(QUrl()) не всегда отпускает файл).
        Сам QVideoWidget переживает пересоздание — его переподключаем."""
        for obj in (self._player, self._audio):
            try:
                obj.setParent(None)
                obj.deleteLater()
            except Exception:
                pass
        self._build_player()

    def hard_release(self):
        self.stop()               # stop уже пересоздаёт плеер и отпускает файл

    def stop(self):
        # Закрыли обрезку — ВЫГРУЖАЕМ файл (снимаем блокировку, чтобы его можно было
        # удалить/переместить). Превью не чистим: последний кадр остаётся, и повторное
        # открытие того же файла проходит бесшовно.
        try:
            self._player.stop()
            self._player.setSource(QUrl())
        except Exception:
            pass
        # Незавершённую обрезку отменяем (убиваем ffmpeg) и помечаем как брошенную,
        # чтобы её результат не «дописался» и не попал в историю после закрытия.
        # НЕ ждём воркеры на UI-потоке (это подвешивало окно при закрытии) — только
        # сигналим стоп/убиваем процесс; потоки завершатся в фоне, а _abandoned
        # гарантирует, что их результат уже не применится.
        self._abandoned = True
        tw = self._trim_worker
        if tw is not None:
            try:
                tw.stop()          # kill_tree(ffmpeg) — процесс умирает, поток выйдет сам
            except Exception:
                pass
        # Сразу освобождаем файл (не дожидаясь удаления) — пересоздаём плеер.
        self._reset_player()

    def _layout(self):
        s = self.app._s
        w = self.width()
        # Отступ должен быть больше «выступа» скругления фона (радиус s(16)):
        # при s(4) прямые углы превью/ленты вылезали на скруглённые края панели.
        pad = s(10)
        prev_h = s(280)
        self._preview.setGeometry(pad, pad, w - 2 * pad, prev_h)
        self._video.setGeometry(pad, pad, w - 2 * pad, prev_h)
        self._ph.setGeometry(pad, pad, w - 2 * pad, prev_h)
        self._ph.setVisible(self._is_audio)
        self._ph.raise_()
        # аудио — waveform в превью с учётом зума/пана.
        if self._is_audio:
            self._update_wave_view()
        # ряд управления над лентой: play слева, In/Out по центру, copy/save справа.
        ctrl_y = pad + prev_h + s(6)
        left_pad = pad + s(10)
        right_pad = pad + s(12)
        self._btn_play.move(left_pad, ctrl_y)
        # Ширину таймкода резервируем по максимуму ДЛЯ ЭТОГО файла (обе метки =
        # его длительность): внутри файла таймкод длиннее не станет, поэтому блок
        # звука не ездит, а резерв не раздут (как было бы под «000:00 – 000:00»).
        tc_x = left_pad + s(38)
        longest = "%s – %s" % (_fmt_time(self._dur), _fmt_time(self._dur))
        tc_w = QFontMetrics(self._time_lbl.font()).horizontalAdvance(longest) + s(2)
        self._time_lbl.setGeometry(tc_x, ctrl_y, tc_w, s(34))
        self._btn_save.move(w - right_pad - s(34), ctrl_y)
        self._btn_copy.move(w - right_pad - s(34) * 2 - s(8), ctrl_y)
        ob = s(46)
        out_x = w - right_pad - s(34) * 2 - s(8) - s(12) - ob
        self._btn_out.setGeometry(out_x, ctrl_y + s(2), ob, s(30))
        in_x = out_x - s(6) - ob
        self._btn_in.setGeometry(in_x, ctrl_y + s(2), ob, s(30))
        # Громкость — фиксированной ширины и прижата к In: длина и позиция не
        # зависят от таймкода, иначе на каждом файле блок был бы своей длины.
        vol_w = s(150)
        vol_x = in_x - s(20) - vol_w
        # Показываем, только если между таймкодом и блоком остаётся воздух.
        fits = vol_x >= tc_x + tc_w + s(10)
        self._vol.setVisible(fits)
        if fits:
            self._vol.setGeometry(vol_x, ctrl_y + s(2), vol_w, s(30))
        bar_y = ctrl_y + s(34) + s(8)
        self._bar.setGeometry(pad, bar_y, w - 2 * pad, s(56))
        if self._confirm is not None and self._confirm.isVisible():
            self._confirm.setGeometry(0, 0, w, self.height())
            self._confirm._layout()

    def eventFilter(self, obj, ev):
        # Пробел управляет превью, даже если фокус ушёл в поле ввода ссылки
        # (вставил ссылку при открытой обрезке — пробел всё равно play/pause).
        if (ev.type() == QEvent.KeyPress and self.isVisible()
                and ev.key() == Qt.Key_Space and not ev.modifiers()
                and not ev.isAutoRepeat()):
            self._toggle_play()
            return True
        if (ev.type() == QEvent.Wheel and self._is_audio and self.isVisible()
                and self._preview.isVisible()):
            lp = self._preview.mapFromGlobal(ev.globalPosition().toPoint())
            if self._preview.rect().contains(lp):
                delta = ev.angleDelta().y() or ev.angleDelta().x()
                if ev.modifiers() & Qt.AltModifier:
                    # Alt + колесо — зум к позиции курсора (как в Premiere).
                    fx = lp.x() / max(1, self._preview.width())
                    self._zoom_wave(delta, fx)
                    return True
                # Просто колесо при зуме — пан таймлайна (вверх=влево, вниз=вправо).
                span = self._view_end - self._view_start
                if 0 < span < self._bar._dur - 1e-3:
                    self._pan_wave(delta)
                    return True
        return super().eventFilter(obj, ev)

    def resizeEvent(self, event):
        self._layout()

    # --- сигналы плеера ------------------------------------------------ #
    def _on_duration(self, ms):
        self._dur = max(0.0, ms / 1000.0)
        if self._dur <= 0:
            return
        self._bar.set_video(self._dur, None)
        self._update_time()
        self._layout()          # резерв под таймкод считается от длительности
        # Лента — звуковая волна (и для видео тоже: читается лучше, чем кадры).
        self._view_start, self._view_end = 0.0, self._dur
        if self._custom_strip is not None:
            self._bar.set_strip(self._custom_strip)   # лента-обзор
        self._update_wave_view()                 # полный вид -> мини-карты нет
        self._player.setPosition(0)

    def _on_volume(self, v):
        """Громкость превью — запоминаем между открытиями/пересозданием плеера."""
        self._volume = v
        if self._audio is not None:
            self._audio.setVolume(v)
        self.app.settings["trim_volume"] = round(v, 3)
        self.app.save_settings()

    def _on_position(self, ms):
        sec = ms / 1000.0
        self._bar.set_play_pos(sec)
        self._follow_playhead()              # превью едет за плейхедом (если зумнуто)
        self._ph.update()                    # синхронный плейхед на превью (waveform)
        self._update_time()
        start, end = self._bar.range()
        if self._player.playbackState() == QMediaPlayer.PlayingState and sec >= end:
            self._player.pause()
            self._player.setPosition(int(start * 1000))

    def _on_state(self, state):
        playing = state == QMediaPlayer.PlayingState
        self._btn_play.set_glyph("pause" if playing else "play")

    def _update_time(self):
        start, end = self._bar.range()
        self._time_lbl.setText(f"{_fmt_time(start)} – {_fmt_time(end)}")

    # --- управление ---------------------------------------------------- #
    def _toggle_play(self):
        if self._player.playbackState() == QMediaPlayer.PlayingState:
            self._player.pause()
        else:
            start, end = self._bar.range()
            if self._player.position() / 1000.0 >= end - 0.05:
                self._player.setPosition(int(start * 1000))
            self._player.play()

    def _on_scrub(self, sec):
        # Плейхед двигается сразу, а перемотку плеера откладываем (троттлинг).
        if self._player.playbackState() == QMediaPlayer.PlayingState:
            self._player.pause()
        self._scrub_pending = sec
        self._update_time()
        if not self._scrub_timer.isActive():
            self._scrub_timer.start()

    def _do_scrub(self):
        if self._scrub_pending is not None:
            self._player.setPosition(int(self._scrub_pending * 1000))
            self._scrub_pending = None

    def _is_full_range(self):
        """Маркеры на краях (не двигали или вернули обратно) — выбран весь файл."""
        dur = self._bar._dur
        if dur <= 0:
            return False
        start, end = self._bar.range()
        tol = max(0.05, dur * 0.002)
        return start <= tol and end >= dur - tol

    def _export(self, copy):
        if self._busy or not self._path or not self.isVisible():
            return
        start, end = self._bar.range()
        if end - start < 0.15:
            return
        # Выбран весь файл — копируем ОРИГИНАЛ: гонять его через ffmpeg незачем
        # (лишний ремукс и временный файл ради байт-в-байт того же содержимого).
        if copy and self._is_full_range():
            self.copied.emit(self._path)
            return
        self._busy = True
        self._abandoned = False
        self._player.pause()
        if copy:
            out = os.path.join(os.environ.get("TEMP", "."),
                               "snatchr_trim_%s" % os.path.basename(self._path))
        else:
            out = trimmer.trim_dest(self._path)
        # Обрезка быстрая (-c copy) — делаем в мелком потоке, чтобы не морозить UI.
        self._trim_worker = _TrimWorker(self._path, start, end, out, self)
        self._trim_worker.done.connect(lambda ok, o=out, c=copy: self._on_trimmed(ok, o, c))
        self._trim_worker.start()

    def _on_trimmed(self, ok, out, copy):
        self._busy = False
        # Панель успели закрыть/сменить файл — результат не нужен: удаляем частичный
        # файл и ничего не записываем в историю (иначе «фантомная» повторная обрезка).
        if self._abandoned:
            try:
                if out and os.path.isfile(out):
                    os.remove(out)
            except OSError:
                pass
            return
        if not ok:
            return
        if copy:
            self.copied.emit(out)
        else:
            self.saved.emit(out)

    # --- фон -----------------------------------------------------------
    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        s = self.app._s
        w, h = self.width(), self.height()
        p.setPen(QPen(self._border, 1))
        p.setBrush(self._bg)
        p.drawRoundedRect(QRectF(0.5, 0.5, w - 1, h - 1), s(16), s(16))
        p.end()


class _TrimWorker(QThread):
    done = Signal(bool)

    def __init__(self, path, start, end, out, parent=None):
        super().__init__(parent)
        self._path, self._start, self._end, self._out = path, start, end, out
        self._proc = None
        self._stopped = False

    def run(self):
        import subprocess
        try:
            args = trimmer.trim_args(self._path, self._start, self._end, self._out)
            if not args:
                self.done.emit(False)
                return
            self._proc = subprocess.Popen(
                args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=tools.CREATE_NO_WINDOW, env=tools._utf8_env())
            self._proc.wait()
            ok = (not self._stopped and self._proc.returncode == 0
                  and os.path.isfile(self._out) and os.path.getsize(self._out) > 0)
            self.done.emit(bool(ok))
        except Exception:
            self.done.emit(False)

    def stop(self):
        self._stopped = True
        tools.kill_tree(self._proc)
