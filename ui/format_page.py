"""
Экран «Format Priority»: список строк селектора качества — глаз слева
(показать/скрыть) и ручка справа (перетащить, изменить порядок).

Порядок и видимость сохраняются в settings (format_order / format_hidden) и
применяются к селектору через core.formats.apply().
"""

from PySide6.QtCore import Qt, QRectF, Signal, QEasingCurve
from PySide6.QtGui import QPainter, QPen, QColor
from PySide6.QtWidgets import QWidget, QLabel, QFrame, QScrollArea

from core import fonts, formats, themes
from core.i18n import tr
from ui import anim
from ui.widgets import WindowDragMixin, SmoothScroll


class _FormatRow(QWidget):
    """Строка списка: [глаз] Название .......... [ручка]."""

    toggled = Signal(str, bool)          # (key, visible)
    dragStarted = Signal(object, int)    # (self, grab_y) — схватили за ручку

    def __init__(self, page, key, visible, parent=None):
        super().__init__(parent)
        self.page = page
        self.key = key
        self.visible_ = visible
        self.app = page.app
        s = self.app._s
        self._pal = page._pal
        self.setFixedHeight(s(38))
        self.setMouseTracking(True)
        self._hover_eye = False
        self._hover_grip = False
        self._font = fonts.font(s(11), "Regular")

    # --- геометрия зон -------------------------------------------------- #
    def _eye_rect(self):
        s = self.app._s
        return QRectF(s(4), (self.height() - s(20)) / 2, s(24), s(20))

    def _grip_rect(self):
        s = self.app._s
        return QRectF(self.width() - s(28), (self.height() - s(16)) / 2,
                      s(20), s(16))

    # --- мышь ----------------------------------------------------------- #
    def mouseMoveEvent(self, e):
        p = e.position()
        he = self._eye_rect().adjusted(-4, -4, 4, 4).contains(p)
        hg = self._grip_rect().adjusted(-6, -6, 6, 6).contains(p)
        if (he, hg) != (self._hover_eye, self._hover_grip):
            self._hover_eye, self._hover_grip = he, hg
            self.setCursor(Qt.PointingHandCursor if he else
                           Qt.SizeVerCursor if hg else Qt.ArrowCursor)
            self.update()

    def leaveEvent(self, e):
        self._hover_eye = self._hover_grip = False
        self.update()

    def mousePressEvent(self, e):
        if e.button() != Qt.LeftButton:
            return
        p = e.position()
        if self._eye_rect().adjusted(-4, -4, 4, 4).contains(p):
            self.visible_ = not self.visible_
            self.toggled.emit(self.key, self.visible_)
            self.update()
            return
        if self._grip_rect().adjusted(-6, -6, 6, 6).contains(p):
            self.dragStarted.emit(self, int(p.y()))

    # --- отрисовка ------------------------------------------------------ #
    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        s = self.app._s
        w, h = self.width(), self.height()
        muted = QColor(self._pal["muted"])
        text = QColor(self._pal["text"])
        if not self.visible_:
            text.setAlphaF(0.45)

        self._draw_eye(p, self._eye_rect(), muted if not self._hover_eye
                       else QColor(self._pal["text"]))
        p.setFont(self._font)
        p.setPen(text)
        p.drawText(QRectF(s(34), 0, w - s(70), h),
                   Qt.AlignVCenter | Qt.AlignLeft, formats.label_for(self.key))
        self._draw_grip(p, self._grip_rect(),
                        QColor(self._pal["text"]) if self._hover_grip else muted)
        p.end()

    def _draw_eye(self, p, r, col):
        """Глаз; если строка скрыта — перечёркнут."""
        p.setPen(QPen(col, max(1.2, self.app._s(1.4))))
        p.setBrush(Qt.NoBrush)
        cx, cy = r.center().x(), r.center().y()
        rw, rh = r.width() / 2.0, r.height() / 2.4
        path_r = QRectF(cx - rw, cy - rh, rw * 2, rh * 2)
        p.drawArc(path_r, 0, 180 * 16)          # верхнее веко
        p.drawArc(path_r, 180 * 16, 180 * 16)   # нижнее веко
        p.drawEllipse(QRectF(cx - rh / 2, cy - rh / 2, rh, rh))   # зрачок
        if not self.visible_:
            p.drawLine(int(cx - rw), int(cy + rh), int(cx + rw), int(cy - rh))

    def _draw_grip(self, p, r, col):
        """Две тонкие полоски рядом — за них перетаскиваем."""
        p.setPen(QPen(col, max(1.2, self.app._s(1.4))))
        gap = max(3.0, self.app._s(4))
        cy = r.center().y()
        for y in (cy - gap / 2.0, cy + gap / 2.0):
            p.drawLine(int(r.left()), int(y), int(r.right()), int(y))


class FormatPage(WindowDragMixin, QWidget):
    """Страница Format Priority: заголовок + колонка со списком форматов."""

    def __init__(self, parent, app, settings, width, height):
        super().__init__(parent)
        self.app = app
        self.settings = settings
        self.width_ = width
        self.height_ = height
        self._pal = themes.palette(settings.get("theme", themes.DEFAULT_THEME))
        self.init_window_drag(app)
        self.resize(width, height)
        self._rows = []
        self._drag_row = None
        self._build()

    # ------------------------------------------------------------------ #
    def _build(self):
        s = self.app._s
        pad = s(16)
        lbl = QLabel(tr("Format Priority"), self)
        lbl.setFont(fonts.font(s(14), "Semibold"))
        lbl.setStyleSheet("color: %s; background: transparent;" % self._pal["title"])
        lbl.move(pad, s(12))
        lbl.adjustSize()

        top = s(42)
        area = QScrollArea(self)
        area.setWidgetResizable(False)
        area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        area.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        area.setFrameShape(QFrame.NoFrame)
        area.viewport().setStyleSheet("background: transparent;")
        area.setStyleSheet(
            "QScrollArea { background: transparent; border: none; }"
            "QScrollBar:vertical { background: transparent; width: 7px; margin: 2px; }"
            "QScrollBar::handle:vertical { background: %s;"
            "  border-radius: 3px; min-height: 24px; }" % self._pal["muted"] +
            "QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }"
            "QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical"
            " { background: transparent; }")
        area.setGeometry(0, top, self.width_, self.height_ - top)
        self._scroll_area = area
        self._smooth_scroll = SmoothScroll(area, parent=self)

        content = QWidget()
        content.setStyleSheet("background: transparent;")
        self._content = content
        self._pad = pad
        area.setWidget(content)
        self.reload()

    def reload(self):
        """(Пере)создаёт строки списка по текущему порядку из настроек."""
        for r in self._rows:
            r.setParent(None)
            r.deleteLater()
        for ln in getattr(self, "_lines", []):
            ln.setParent(None)
            ln.deleteLater()
        self._rows, self._lines = [], []
        hidden = formats.hidden(self.settings)
        for key in formats.order(self.settings):
            row = _FormatRow(self, key, key not in hidden, self._content)
            row.toggled.connect(self._on_toggle)
            row.dragStarted.connect(self._on_drag_start)
            self._rows.append(row)
        self._relayout_rows()

    def _row_h(self):
        """Шаг слота: высота строки + разделительная линия."""
        return (self._rows[0].height() + 1) if self._rows else 1

    def _slot_y(self, i):
        return i * self._row_h()

    def _relayout_rows(self):
        """Расставляет строки по слотам мгновенно и (пере)создаёт разделители.

        Разделители привязаны к слотам, а не к строкам, поэтому при перетаскивании
        они остаются на месте — двигаются только сами строки."""
        s = self.app._s
        pad = self._pad
        w = self.width_ - 2 * pad
        for ln in getattr(self, "_lines", []):
            ln.setParent(None)
            ln.deleteLater()
        self._lines = []
        for i, row in enumerate(self._rows):
            row.setGeometry(pad, self._slot_y(i), w, row.height())
            row.show()
        rh = self._row_h()
        for i in range(len(self._rows) - 1):
            ln = QFrame(self._content)
            ln.setGeometry(pad, self._slot_y(i) + rh - 1, w, 1)
            ln.setStyleSheet("background: %s; border: none;"
                             % self._pal["separator"])
            ln.show()
            self._lines.append(ln)
        self._content.resize(self.width_, self._slot_y(len(self._rows)) + s(12))

    def _animate_to_slots(self, skip=None):
        """Плавно съезжают на свои слоты все строки, кроме перетаскиваемой."""
        for i, row in enumerate(self._rows):
            if row is skip:
                continue
            ty = self._slot_y(i)
            if abs(row.y() - ty) < 1:
                continue
            anim.animate(row, float(row.y()), float(ty), 170,
                         lambda v, r=row: r.move(self._pad, int(round(v))),
                         easing=QEasingCurve.OutCubic, attr="_y_anim")

    # --- изменения ------------------------------------------------------ #
    def _on_toggle(self, key, visible):
        hidden = formats.hidden(self.settings)
        if visible:
            hidden.discard(key)
        else:
            hidden.add(key)
        self.settings["format_hidden"] = sorted(hidden)
        self.app.save_settings()

    def _on_drag_start(self, row, grab_y):
        self._drag_row = row
        self._drag_dy = grab_y            # где внутри строки схватили — без рывка
        row.raise_()
        self.grabMouse()

    def mouseMoveEvent(self, e):
        """Перетаскивание за ручку: строка следует за курсором, а вытесняемые
        соседи плавно съезжают на освободившийся слот (взяли 3-й, тащим вверх —
        2-й уезжает на место 3-го, и так далее)."""
        if self._drag_row is None:
            return super().mouseMoveEvent(e)
        pos = self._content.mapFrom(self, e.position().toPoint())
        y = pos.y() - self._drag_dy
        limit = self._slot_y(len(self._rows) - 1)
        y = max(0, min(limit, y))
        self._drag_row.move(self._pad, y)          # тащим напрямую, без анимации
        idx = self._rows.index(self._drag_row)
        new_idx = max(0, min(len(self._rows) - 1,
                             int(round(y / float(self._row_h())))))
        if new_idx != idx:
            self._rows.insert(new_idx, self._rows.pop(idx))
            self._animate_to_slots(skip=self._drag_row)
            self._save_order()

    def mouseReleaseEvent(self, e):
        if self._drag_row is None:
            return super().mouseReleaseEvent(e)
        self._drag_row = None
        self.releaseMouse()
        self._animate_to_slots()          # отпущенная строка доезжает до слота

    def _save_order(self):
        self.settings["format_order"] = [r.key for r in self._rows]
        self.app.save_settings()
