import os

from PySide6.QtGui import QFontDatabase, QFont

from core.constants import FONTS_DIR

FAMILY = "SF Pro Display"
MONO   = "Consolas"   # моноширинный (для пути загрузки), как в референсе

# Совместимость со старым кодом (family-имена).
FONT_REGULAR = FAMILY
FONT_LIGHT   = "SF Pro Display Light"
FONT_THIN    = "SF Pro Display Thin"

# Все веса «SF Pro Display» регистрируются под одним family с разными стилями.
_FILES = [
    "SF-Pro-Display-Thin.otf",
    "SF-Pro-Display-Light.otf",
    "SF-Pro-Display-Regular.otf",
    "SF-Pro-Display-Medium.otf",
    "SF-Pro-Display-Semibold.otf",
    "SF-Pro-Display-Bold.otf",
    "SF-Pro-Display-Heavy.otf",
    # Отдельные family для Light/Thin (старый код).
    "SFProDisplay-Light-named.otf",
    "SFProDisplay-Thin-named.otf",
]

_loaded = False


def load():
    """Регистрирует шрифты приложения (один раз за сессию)."""
    global _loaded
    if _loaded:
        return
    for fname in _FILES:
        path = os.path.join(FONTS_DIR, fname)
        if os.path.isfile(path):
            try:
                QFontDatabase.addApplicationFont(path)
            except Exception:
                pass
    _loaded = True


def font(size, style="Regular"):
    """
    QFont нужного стиля «SF Pro Display».
    style: Thin | Light | Regular | Medium | Semibold | Bold | Heavy
    """
    f = QFontDatabase.font(FAMILY, style, size)
    if f.family() != FAMILY:        # на случай, если стиль не подхватился
        f = QFont(FAMILY, size)
    return f


def mono(size):
    """Моноширинный шрифт (для пути загрузки)."""
    f = QFont(MONO, size)
    f.setStyleHint(QFont.Monospace)
    return f
