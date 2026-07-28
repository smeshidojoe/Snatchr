import os

from PySide6.QtGui import QFontDatabase, QFont

from core.constants import FONTS_DIR

FAMILY = "SF Pro Display"
MONO   = "Consolas"   # моноширинный (для пути загрузки), как в референсе

# Совместимость со старым кодом (family-имена).

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

# Кеш готовых QFont по (размер, стиль). QFontDatabase.font() каждый раз ищет
# шрифт в базе, а при построении окна он зовётся под сотню раз — на старте это
# заметная доля времени. Ключей мало (десяток размеров × 7 стилей).
_font_cache = {}


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
    # Всё, что успели запросить до регистрации, попало в кеш запасным шрифтом —
    # сбрасываем, иначе неверный шрифт закрепится на всю сессию.
    _font_cache.clear()


def font(size, style="Regular"):
    """
    QFont нужного стиля «SF Pro Display».
    style: Thin | Light | Regular | Medium | Semibold | Bold | Heavy

    Результат кешируется; наружу отдаём копию, чтобы вызывающий код не мог
    изменить запись в кеше (QFont — значение, но объект общий).
    """
    key = (size, style)
    f = _font_cache.get(key)
    if f is None:
        f = QFontDatabase.font(FAMILY, style, size)
        if f.family() != FAMILY:    # на случай, если стиль не подхватился
            f = QFont(FAMILY, size)
        _font_cache[key] = f
    return QFont(f)


def mono(size):
    """Моноширинный шрифт (для пути загрузки)."""
    key = (size, "__mono__")
    f = _font_cache.get(key)
    if f is None:
        f = QFont(MONO, size)
        f.setStyleHint(QFont.Monospace)
        _font_cache[key] = f
    return QFont(f)
