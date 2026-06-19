import os

from PySide6.QtCore import Qt, QPointF
from PySide6.QtGui import QIcon, QPixmap, QImage, QPainter, QColor, QBrush, QPolygonF
from PySide6.QtWidgets import QSystemTrayIcon, QMenu

from core.constants import ICONS_DIR


class TrayIcon:
    def __init__(self, app):
        self.app = app
        self.icon = None
        self._build_icon()

    def _default_icon(self):
        img = QImage(64, 64, QImage.Format_ARGB32)
        img.fill(Qt.transparent)
        painter = QPainter(img)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QBrush(QColor("#3B82F6")))
        painter.drawEllipse(4, 4, 56, 56)
        painter.setBrush(QBrush(QColor("white")))
        tri = QPolygonF([QPointF(22, 20), QPointF(22, 44), QPointF(46, 32)])
        painter.drawPolygon(tri)
        painter.end()
        return QIcon(QPixmap.fromImage(img))

    def _resolve_icon(self):
        """Иконка из icons/<tray_icon>.png, либо иконка по умолчанию."""
        name = self.app.settings.get("tray_icon", "")
        if name:
            path = os.path.join(ICONS_DIR, name + ".png")
            if os.path.isfile(path):
                pm = QPixmap(path)
                if not pm.isNull():
                    return QIcon(pm)
        return self._default_icon()

    def _build_icon(self):
        self.icon = QSystemTrayIcon(self._resolve_icon(), self.app)
        self.icon.setToolTip("Snatchr")

        menu = QMenu()
        act_open = menu.addAction("Открыть")
        act_open.triggered.connect(self._show_app)
        act_quit = menu.addAction("Выход")
        act_quit.triggered.connect(self._quit_app)
        self.icon.setContextMenu(menu)
        self._menu = menu

        self.icon.activated.connect(self._on_activated)

    def set_icon(self, name):
        """Сменить иконку трея на лету (name — имя файла без расширения)."""
        self.app.settings["tray_icon"] = name or ""
        if self.icon is not None:
            self.icon.setIcon(self._resolve_icon())

    def _on_activated(self, reason):
        # Левый клик. Быстрый повторный клик во время анимации Windows отдаёт
        # как DoubleClick — его тоже считаем кликом, иначе второй клик теряется.
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            self._toggle_app()

    def _toggle_app(self):
        self.app.toggle_window()

    def _show_app(self):
        self.app.show_near_tray()

    def _quit_app(self):
        from PySide6.QtWidgets import QApplication
        self.icon.hide()
        QApplication.instance().quit()

    def run(self):
        self.icon.show()
