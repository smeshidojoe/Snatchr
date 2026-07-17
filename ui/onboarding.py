"""
Подсказка первого запуска: пузырёк со стрелкой, указывающей на иконку в трее
(«окно программы живёт в трее»). Показывается один раз — при первом старте,
когда ещё нет папки %APPDATA%/Snatchr (см. config.IS_FIRST_RUN).

Отдельное безрамочное окно поверх всех: рисуем скруглённый прямоугольник,
треугольную стрелку к трею и кнопку OK. Направление стрелки — от того, где
панель задач: панель снизу -> пузырёк над треем (стрелка вниз), панель сверху ->
под треем (стрелка вверх).
"""

from PySide6.QtCore import Qt, QRectF, QPoint, Signal, QEasingCurve
from PySide6.QtGui import QPainter, QColor, QPen, QPolygonF, QPainterPath
from PySide6.QtCore import QPointF
from PySide6.QtWidgets import QWidget

from core import fonts, themes
from core.i18n import tr
from ui import anim
from ui.widgets import LinkButton


class TrayHint(QWidget):
    """Пузырёк-подсказка со стрелкой на трей и кнопкой OK."""

    closed = Signal()

    ARROW_W = 18
    ARROW_H = 9

    def __init__(self, app, anchor, edge):
        """anchor — точка на иконке трея (глобальные координаты);
        edge — где панель задач: 'top' | 'bottom' | 'left' | 'right'."""
        super().__init__(None)
        self.app = app
        self._edge = edge
        s = app._s
        pal = themes.palette(app.settings.get("theme", themes.DEFAULT_THEME))
        self._bg = QColor(pal["card_bg"])
        self._border = QColor(pal["separator"])
        self._title_col = QColor(pal["title"])
        self._text_col = QColor(pal["muted"])

        self.setWindowFlags(Qt.Tool | Qt.FramelessWindowHint
                            | Qt.WindowStaysOnTopHint | Qt.NoDropShadowWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)

        self._title = tr("Snatchr lives in the tray")
        self._body = tr("Click the tray icon to open the main window.")
        self._font_t = fonts.font(s(12), "Semibold")
        self._font_b = fonts.font(s(11), "Regular")

        self._w = s(270)
        self._h = s(112)
        self._arrow_h = max(6, s(self.ARROW_H))

        self._btn_ok = LinkButton(
            self, tr("OK"), fonts.font(s(11), "Semibold"),
            pal["on_accent"], pal["on_accent"], self._dismiss,
            hover_bg=pal["accent_hover"], radius=s(7), base_bg=pal["accent"])
        self._btn_ok.setFixedSize(s(84), s(28))

        self._place(anchor)

    # --- геометрия ------------------------------------------------------ #
    def _arrow_up(self):
        """Стрелка смотрит ВВЕРХ (пузырёк под треем) — только если панель сверху."""
        return self._edge == "top"

    def _place(self, anchor):
        s = self.app._s
        total_h = self._h + self._arrow_h
        x = anchor.x() - self._w // 2
        if self._arrow_up():
            y = anchor.y() + s(6)
        else:
            y = anchor.y() - total_h - s(6)
        # не вылезаем за экран
        from PySide6.QtGui import QGuiApplication
        scr = (QGuiApplication.screenAt(anchor)
               or QGuiApplication.primaryScreen()).availableGeometry()
        x = max(scr.left() + s(8), min(x, scr.right() - self._w - s(8)))
        y = max(scr.top() + s(8), min(y, scr.bottom() - total_h - s(8)))
        self.setGeometry(x, y, self._w, total_h)
        self._anchor_x = max(s(16), min(anchor.x() - x, self._w - s(16)))
        # кнопка OK — снизу по центру карточки
        card_top = self._arrow_h if self._arrow_up() else 0
        self._btn_ok.move((self._w - self._btn_ok.width()) // 2,
                          card_top + self._h - self._btn_ok.height() - s(10))

    def _card_rect(self):
        top = self._arrow_h if self._arrow_up() else 0
        return QRectF(0.5, top + 0.5, self._w - 1, self._h - 1)

    # --- показ/скрытие --------------------------------------------------- #
    def show_hint(self):
        """Появление: пузырёк выезжает ОТ трея и проявляется."""
        s = self.app._s
        final = self.pos()
        rise = s(14)
        # Стартуем ближе к трею: пузырёк под треем — чуть выше, над треем — ниже.
        start_y = final.y() + (-rise if self._arrow_up() else rise)
        self.move(final.x(), start_y)
        self.show()
        self.raise_()
        anim.animate(self, 0.0, 1.0, 340,
                     lambda v: self.move(final.x(),
                                         int(start_y + (final.y() - start_y) * v)),
                     easing=QEasingCurve.OutCubic, attr="_slide_anim")
        anim.fade(self, 0.0, 1.0, 260)

    def _dismiss(self):
        """Закрытие: уезжает обратно к трею и гаснет."""
        s = self.app._s
        cur = self.pos()
        drop = s(10)
        end_y = cur.y() + (-drop if self._arrow_up() else drop)
        anim.animate(self, 0.0, 1.0, 200,
                     lambda v: self.move(cur.x(), int(cur.y() + (end_y - cur.y()) * v)),
                     easing=QEasingCurve.InCubic, attr="_slide_anim")
        anim.fade(self, 1.0, 0.0, 190, on_finished=self._finish)

    def _finish(self):
        self.hide()
        self.closed.emit()
        self.deleteLater()

    # --- отрисовка ------------------------------------------------------- #
    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        s = self.app._s
        card = self._card_rect()

        # Карточка + стрелка одним контуром — общая обводка без шва.
        path = QPainterPath()
        path.addRoundedRect(card, s(12), s(12))
        aw = max(10, s(self.ARROW_W))
        ax = self._anchor_x
        tri = QPolygonF()
        if self._arrow_up():
            tri.append(QPointF(ax, 0))
            tri.append(QPointF(ax - aw / 2, card.top() + 1))
            tri.append(QPointF(ax + aw / 2, card.top() + 1))
        else:
            tri.append(QPointF(ax, self.height()))
            tri.append(QPointF(ax - aw / 2, card.bottom() - 1))
            tri.append(QPointF(ax + aw / 2, card.bottom() - 1))
        tp = QPainterPath()
        tp.addPolygon(tri)
        path = path.united(tp)

        p.setPen(QPen(self._border, 1))
        p.setBrush(self._bg)
        p.drawPath(path)

        pad = s(14)
        p.setFont(self._font_t)
        p.setPen(self._title_col)
        p.drawText(QRectF(card.left() + pad, card.top() + s(12),
                          card.width() - 2 * pad, s(18)),
                   Qt.AlignLeft | Qt.AlignVCenter, self._title)
        p.setFont(self._font_b)
        p.setPen(self._text_col)
        p.drawText(QRectF(card.left() + pad, card.top() + s(32),
                          card.width() - 2 * pad, s(34)),
                   Qt.AlignLeft | Qt.AlignTop | Qt.TextWordWrap, self._body)
        p.end()


def tray_anchor(app):
    """Точка на иконке трея + где панель задач: (QPoint, 'top'|'bottom'|...).
    None — если трей найти не удалось (не Windows/нестандартная оболочка)."""
    try:
        import win32gui
        from PySide6.QtGui import QGuiApplication
        taskbar = win32gui.FindWindow("Shell_TrayWnd", None)
        if not taskbar:
            return None
        tb = win32gui.GetWindowRect(taskbar)
        tray_hwnd = win32gui.FindWindowEx(taskbar, 0, "TrayNotifyWnd", None)
        toolbar = win32gui.FindWindowEx(tray_hwnd, 0, "ToolbarWindow32", None)
        r = win32gui.GetWindowRect(toolbar or tray_hwnd)
        scr = QGuiApplication.primaryScreen().geometry()
        tb_w, tb_h = tb[2] - tb[0], tb[3] - tb[1]
        if tb_h < tb_w:
            edge = "top" if tb[1] < scr.height() // 2 else "bottom"
            anchor = QPoint((r[0] + r[2]) // 2, r[3] if edge == "top" else r[1])
        else:
            edge = "left" if tb[0] < scr.width() // 2 else "right"
            anchor = QPoint((r[0] + r[2]) // 2, r[1])
        return anchor, edge
    except Exception:
        return None
