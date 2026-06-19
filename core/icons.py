import os

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap, QImage, QPainter, QColor, QIcon

from core.constants import theme_dir, DEFAULT_THEME
from core import themes

# Цвет иконок нижней панели / инфо (обычный и при наведении).
# Берём из палитры темы по умолчанию (UI постепенно переводится на palette()).
ICON_COLOR = themes.color(DEFAULT_THEME, "icon")
ICON_HOVER = themes.color(DEFAULT_THEME, "icon_hover")


def _resolve_path(theme, filename):
    path = os.path.join(theme_dir(themes.assets_name(theme)), filename)
    if not os.path.isfile(path):
        # Тема без нужного ассета -> берём иконку из темы по умолчанию.
        path = os.path.join(theme_dir(themes.assets_name(DEFAULT_THEME)), filename)
    if not os.path.isfile(path):
        return None
    return path


def themed_pixmap(theme, filename, color, size):
    """
    Загружает чёрную глиф-иконку из assets/<theme>/<filename> и
    перекрашивает её в указанный цвет (через альфа-канал).
    Возвращает QPixmap нужного размера или None, если файла нет.
    """
    path = _resolve_path(theme, filename)
    if path is None:
        return None

    src = QImage(path).convertToFormat(QImage.Format_ARGB32)
    src = src.scaled(size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation)

    out = QImage(src.size(), QImage.Format_ARGB32)
    out.fill(Qt.transparent)

    painter = QPainter(out)
    painter.drawImage(0, 0, src)
    # Заливаем непрозрачные пиксели сплошным цветом, сохраняя альфу глифа.
    painter.setCompositionMode(QPainter.CompositionMode_SourceIn)
    painter.fillRect(out.rect(), QColor(color))
    painter.end()

    return QPixmap.fromImage(out)


def themed_icon(theme, filename, color, size):
    """То же, что themed_pixmap, но как QIcon (для QPushButton)."""
    pm = themed_pixmap(theme, filename, color, size)
    if pm is None:
        return None
    return QIcon(pm)
