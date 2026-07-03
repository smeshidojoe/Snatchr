"""
Панель обрезки Spotlight: превью видео + лента кадров (filmstrip) с двумя
жёлтыми ручками диапазона + play/pause + копировать/сохранить.

Превью и воспроизведение — через QMediaPlayer (реальный звук + видео); при
перетаскивании ручки просто перематываем плеер на нужную позицию (кадр
показывается через QVideoSink). Filmstrip рисуем один раз через ffmpeg.
Обрезка — trimmer.trim (-ss/-t -c copy).
"""

import os

from PySide6.QtCore import (
    Qt, QRectF, QUrl, QThread, Signal, QPointF, QTimer, QEasingCurve
)
from PySide6.QtGui import QPainter, QColor, QPen, QPixmap
from PySide6.QtWidgets import QWidget, QLabel
from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput, QVideoSink

from core import fonts, themes, trimmer, tools
from core.i18n import tr
from ui import anim
from ui.widgets import LinkButton


def _fmt_time(sec):
    sec = max(0, int(sec or 0))
    return f"{sec // 60}:{sec % 60:02d}"


# ------------------------------------------------------------------ #
class _FilmstripWorker(QThread):
    """Готовит горизонтальную ленту кадров (ffmpeg) в фоне."""
    done = Signal(str)          # путь к jpg (или "")

    def __init__(self, path, out_path, count, frame_w, dur, parent=None):
        super().__init__(parent)
        self._path, self._out = path, out_path
        self._count, self._fw, self._dur = count, frame_w, dur

    def run(self):
        try:
            r = trimmer.filmstrip(self._path, self._out, count=self._count,
                                  frame_w=self._fw, dur=self._dur)
            self.done.emit(r or "")
        except Exception:
            self.done.emit("")


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
        self.setMouseTracking(True)
        self.setMinimumHeight(s(56))

    def set_video(self, dur, strip_pixmap=None):
        self._dur = max(0.0, float(dur or 0.0))
        self._start = 0.0
        self._end = self._dur
        self._play = 0.0
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
        if self._drag == "l":
            self._start = max(0.0, min(sec, self._end - min_gap))
            self._play = self._start
        else:
            self._end = min(self._dur, max(sec, self._start + min_gap))
            self._play = self._end
        self.scrub.emit(self._play)
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
        self._strip_worker = None
        self._trim_worker = None
        self._busy = False
        self._abandoned = False          # обрезку бросили (закрыли панель) -> игнор
        self._dirty = False              # двигали ли ползунки обрезки

        # --- плеер (реальный звук+видео через QVideoSink -> QLabel) ------
        self._player = QMediaPlayer(self)
        self._audio = QAudioOutput(self)
        self._player.setAudioOutput(self._audio)
        self._sink = QVideoSink(self)
        self._player.setVideoSink(self._sink)
        self._sink.videoFrameChanged.connect(self._on_frame)
        self._player.durationChanged.connect(self._on_duration)
        self._player.positionChanged.connect(self._on_position)
        self._player.playbackStateChanged.connect(self._on_state)

        # --- виджеты ------------------------------------------------------
        self._preview = QLabel(self)
        self._preview.setAlignment(Qt.AlignCenter)
        self._preview.setStyleSheet("background: #000000; border-radius: %dpx;" % s(10))

        self._btn_play = _CtrlButton(app, "play", self)
        self._btn_play.clicked.connect(self._toggle_play)
        self._btn_copy = _CtrlButton(app, "copy", self)
        self._btn_copy.clicked.connect(lambda: self._export(copy=True))
        self._btn_save = _CtrlButton(app, "save", self, accent=True)
        self._btn_save.clicked.connect(lambda: self._export(copy=False))

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

    # --- защита при смене файла обрезки -------------------------------- #
    def current_path(self):
        return self._path

    def is_dirty(self):
        return self._dirty

    def _on_range(self, a, b):
        self._dirty = True

    def confirm_switch(self, title, on_yes):
        self._confirm.ask(title, on_yes)

    # --- открытие/закрытие --------------------------------------------- #
    def target_height(self):
        s = self.app._s
        return s(280) + s(56) + s(44) + s(30)

    def open_for(self, path):
        self._path = path
        self._busy = False
        self._abandoned = False
        self._dirty = False              # новый файл — изменений ещё нет
        self._btn_play.set_glyph("play")
        self._preview.clear()
        self._bar.set_video(0.0, None)
        if path and os.path.isfile(path):
            self._player.setSource(QUrl.fromLocalFile(os.path.abspath(path)))
            self._player.pause()
        self._layout()

    def stop(self):
        try:
            self._player.stop()
            self._player.setSource(QUrl())
        except Exception:
            pass
        # Незавершённую обрезку отменяем (убиваем ffmpeg) и помечаем как брошенную,
        # чтобы её результат не «дописался» и не попал в историю после закрытия.
        self._abandoned = True
        tw = self._trim_worker
        if tw is not None:
            try:
                tw.stop()
                if tw.isRunning():
                    tw.wait(2000)
            except Exception:
                pass
        sw = self._strip_worker
        if sw is not None:
            try:
                if sw.isRunning():
                    sw.wait(1500)
            except Exception:
                pass

    def _layout(self):
        s = self.app._s
        w = self.width()
        pad = s(4)
        prev_h = s(280)
        self._preview.setGeometry(pad, pad, w - 2 * pad, prev_h)
        # ряд управления над лентой: play слева, copy/save справа (с отступами
        # от краёв — кнопки не липнут к границам).
        ctrl_y = pad + prev_h + s(6)
        left_pad = pad + s(10)
        right_pad = pad + s(12)
        self._btn_play.move(left_pad, ctrl_y)
        self._time_lbl.setGeometry(left_pad + s(42), ctrl_y, s(120), s(34))
        self._btn_save.move(w - right_pad - s(34), ctrl_y)
        self._btn_copy.move(w - right_pad - s(34) * 2 - s(8), ctrl_y)
        bar_y = ctrl_y + s(34) + s(8)
        self._bar.setGeometry(pad, bar_y, w - 2 * pad, s(56))
        if self._confirm is not None and self._confirm.isVisible():
            self._confirm.setGeometry(0, 0, w, self.height())
            self._confirm._layout()

    def resizeEvent(self, event):
        self._layout()

    # --- сигналы плеера ------------------------------------------------ #
    def _on_duration(self, ms):
        self._dur = max(0.0, ms / 1000.0)
        if self._dur <= 0:
            return
        self._bar.set_video(self._dur, None)
        self._update_time()
        # Лента кадров — в фоне (ffmpeg), не блокируем UI.
        self._build_filmstrip()
        self._player.setPosition(0)

    def _build_filmstrip(self):
        if not self._path:
            return
        out = os.path.join(os.environ.get("TEMP", "."),
                           "snatchr_strip_%d.jpg" % id(self))
        count = 14
        fw = max(60, int(self._bar.width() / count) + 20)
        self._strip_worker = _FilmstripWorker(self._path, out, count, fw,
                                              self._dur, self)
        self._strip_worker.done.connect(self._on_strip)
        self._strip_worker.start()

    def _on_strip(self, path):
        if path and os.path.isfile(path):
            pm = QPixmap(path)
            if not pm.isNull():
                self._bar.set_strip(pm)

    def _on_frame(self, frame):
        if not frame.isValid():
            return
        img = frame.toImage()
        if img.isNull():
            return
        self._show_image(QPixmap.fromImage(img))

    def _show_image(self, pm):
        if pm.isNull() or self._preview.width() <= 0:
            return
        scaled = pm.scaled(self._preview.width(), self._preview.height(),
                           Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self._preview.setPixmap(scaled)

    def _on_position(self, ms):
        sec = ms / 1000.0
        self._bar.set_play_pos(sec)
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

    def _export(self, copy):
        if self._busy or not self._path or not self.isVisible():
            return
        start, end = self._bar.range()
        if end - start < 0.15:
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
