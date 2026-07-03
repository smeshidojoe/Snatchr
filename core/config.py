import os
import json

# Данные приложения храним в отдельной папке в %APPDATA% (Windows),
# с запасным вариантом для других ОС.
_BASE = os.environ.get("APPDATA") or os.path.join(
    os.path.expanduser("~"), ".config")
APP_DIR     = os.path.join(_BASE, "Snatchr")
CONFIG_PATH = os.path.join(APP_DIR, "config.json")

# Старое расположение (для разовой миграции существующего конфига).
OLD_CONFIG_PATH = os.path.join(os.path.expanduser("~"), ".snatchr", "config.json")


def defaults():
    return {
        "download_path":   os.path.join(os.path.expanduser("~"), "Downloads"),
        "embed_thumbnail": False,
        "convert_yt":      False,         # по умолчанию выключена
        "tray_icon":       "",            # имя файла в icons/ ("" => иконка по умолчанию)
        "theme":           "Glass",       # стартовая тема для нового пользователя
        "language":        "English",     # язык интерфейса
        "usage_mode":      "toggle",      # "toggle" (Pinned) | "focus" (Auto-hide)
        "allow_dragging":  False,         # разрешить перетаскивание окна
        "ytdlp_updated":   0,             # когда в последний раз обновляли yt-dlp (epoch)
        "ytdlp_channel":   "stable",      # канал yt-dlp: "stable" | "nightly"
        "clipboard_watch": False,         # следить за буфером и предлагать скачивание
        "toast_position":  "corner",      # тост: "corner" (угол) | "cursor" (у мыши)
        "cookies_browser": "auto",        # браузер для cookies ("auto" | chrome | …)
        "cookies_file":    "",            # путь к своему cookies.txt (приоритетнее браузера)
        "spotlight_enabled": True,        # включён ли Spotlight (глобальный хоткей)
        "spotlight_combo":   "ctrl+shift+d",  # сочетание вызова Spotlight
        "spotlight_dismiss": "focus",     # "focus" (скрывать при потере фокуса) | "manual"
    }


def load():
    """Читает настройки с диска, дополняя отсутствующие ключи дефолтами.
    При первом запуске переносит конфиг из старого расположения."""
    data = defaults()

    source = CONFIG_PATH
    migrated = False
    if not os.path.exists(CONFIG_PATH) and os.path.exists(OLD_CONFIG_PATH):
        source = OLD_CONFIG_PATH
        migrated = True

    try:
        with open(source, "r", encoding="utf-8") as f:
            saved = json.load(f)
        if isinstance(saved, dict):
            data.update(saved)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass

    # Миграция переименованной темы.
    if data.get("theme") == "Rose Negative":
        data["theme"] = "White Rose"

    if migrated:
        save(data)   # сразу сохраняем в новое расположение

    return data


def save(settings):
    """Сохраняет настройки на диск (тихо, без падений на ошибках ФС)."""
    try:
        os.makedirs(APP_DIR, exist_ok=True)
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(settings, f, indent=2, ensure_ascii=False)
    except OSError:
        pass
