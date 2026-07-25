import os
import sys

APP_NAME    = "Snatchr"
APP_VERSION = "0.9.3"

# Репозиторий для проверки обновлений (релизы на GitHub).
# TODO: указать реальный "owner/repo".
GITHUB_REPO = "SmeshidoJoe/Snatchr"

# Ссылка, открывающаяся по клику на «SmeshidoJoe» в окне About.
DEVELOPER_URL = "https://github.com/SmeshidoJoe"

# Кнопки поддержки в окне About. Ссылки — заглушки, заполнить реальными.
# (URL пуст -> кнопка не открывает ничего; заполнишь позже.)
KOFI_URL           = ""
BOOSTY_URL         = ""
DONATIONALERTS_URL = ""
CLOUDTIPS_URL      = "https://pay.cloudtips.ru/p/b044728c"

# Стиль каждой кнопки: (ключ, подпись, цвет фона, цвет текста, url-константа,
# имя PNG-иконки в assets/icons без расширения — если файла нет, рисуем без неё).
# Цвета — фирменные: Ko-fi бежевый, Boosty оранжевый, DonationAlerts красный,
# CloudTips белый с синим текстом.
DONATE_BUTTONS = [
    ("kofi",           "Buy me a coffee", "#cbb79f", "#2a2320", KOFI_URL,           "kofi"),
    ("boosty",         "Boosty",          "#f15f2c", "#ffffff", BOOSTY_URL,         "boosty"),
    ("donationalerts", "DonationAlerts",  "#f57d07", "#ffffff", DONATIONALERTS_URL, "donationalerts"),
    ("cloudtips",      "CloudTips",       "#ffffff", "#1b3b6f", CLOUDTIPS_URL,      "cloudtips"),
]

# В сборке PyInstaller ресурсы лежат во временной папке _MEIPASS; в разработке —
# в корне проекта. (Папка установки exe берётся отдельно, см. core/updater.py.)
if getattr(sys, "frozen", False):
    BASE_DIR = sys._MEIPASS
else:
    BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ASSETS_DIR  = os.path.join(BASE_DIR, "assets")
ICONS_DIR   = os.path.join(ASSETS_DIR, "icons")     # иконки трея (общие)
FONTS_DIR   = os.path.join(ASSETS_DIR, "fonts")
THEMES_DIR  = os.path.join(ASSETS_DIR, "Themes")    # ассеты по темам
PROFILE_IMG = os.path.join(ASSETS_DIR, "profile.png")

DEFAULT_THEME = "Glass"


def theme_dir(theme):
    """Папка ассетов конкретной темы (assets/Themes/<theme>)."""
    return os.path.join(THEMES_DIR, theme)


# Список тем берём из реестра тем (см. core/themes.py).
def _theme_names():
    try:
        from core.themes import enabled_themes
        return enabled_themes()
    except Exception:
        return [DEFAULT_THEME]


THEMES = _theme_names()

# Доступные языки интерфейса (см. core/i18n.py).
LANGUAGES = ["English", "Русский"]
DEFAULT_LANGUAGE = "English"
