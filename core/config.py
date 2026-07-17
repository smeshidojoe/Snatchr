import os
import json

# Данные приложения храним в отдельной папке в %APPDATA% (Windows),
# с запасным вариантом для других ОС.
_BASE = os.environ.get("APPDATA") or os.path.join(
    os.path.expanduser("~"), ".config")
APP_DIR     = os.path.join(_BASE, "Snatchr")
CONFIG_PATH = os.path.join(APP_DIR, "config.json")

# Первый ли это запуск — снимаем СРАЗУ при импорте, до того как что-либо создаст
# APP_DIR (иначе проверка «папки нет» уже не сработает).
IS_FIRST_RUN = not os.path.isdir(APP_DIR)


def defaults():
    return {
        "download_path":   os.path.join(os.path.expanduser("~"), "Downloads"),
        "embed_thumbnail": False,
        "convert_yt":      True,          # конвертация VP9 -> H.264 (по умолчанию вкл)
        "tray_icon":       "",            # имя файла в icons/ ("" => иконка по умолчанию)
        "theme":           "Glass",       # стартовая тема для нового пользователя
        "language":        "English",     # язык интерфейса
        "usage_mode":      "focus",       # "toggle" (Pinned) | "focus" (Auto-hide)
        "allow_dragging":  False,         # разрешить перетаскивание окна
        "ytdlp_updated":   0,             # когда в последний раз обновляли yt-dlp (epoch)
        "ytdlp_channel":   "stable",      # канал yt-dlp: "stable" | "nightly"
        "clipboard_watch": False,         # следить за буфером и предлагать скачивание
        "toast_position":  "corner",      # тост: "corner" (угол) | "cursor" (у мыши)
        "cookies_browser": "auto",        # браузер для cookies ("auto" | chrome | …)
        "cookies_file":    "",            # путь к своему cookies.txt (приоритетнее браузера)
        "spotlight_enabled": True,        # включён ли Spotlight (глобальный хоткей)
        "spotlight_combo":   "ctrl+e",    # сочетание вызова Spotlight
        "spotlight_dismiss": "focus",     # "focus" (скрывать при потере фокуса) | "manual"
        "update_notify":     True,        # уведомлять тостом о новых версиях
        "update_dismissed_version": "",   # версия, тост которой уже закрыли
        "toast_copy_file":   True,        # копировать скачанный файл в буфер (Toast)
        "autostart":         False,       # запускать Snatchr при старте Windows
        "parallel_downloads": 2,          # одновременных загрузок (1..3)
        "autopaste":         False,       # вставлять ссылку из буфера при открытии окна
        "trim_volume":       0.8,         # громкость превью в панели обрезки (0..1)
        "format_order":      [],          # порядок строк селектора (пусто => по умолчанию)
        "format_hidden":     [],          # скрытые строки селектора (ключи core.formats)
        # какие сайты автовставлять (пусто в конфиге => все; None здесь = все по умолчанию)
        "autopaste_sites":   ["youtube", "instagram", "tiktok", "reddit",
                              "twitter", "vk", "soundcloud"],
    }


def load():
    """Читает настройки с диска, дополняя отсутствующие ключи дефолтами."""
    data = defaults()
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            saved = json.load(f)
        if isinstance(saved, dict):
            data.update(saved)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass

    # Миграция переименованной темы.
    if data.get("theme") == "Rose Negative":
        data["theme"] = "White Rose"

    return data


def save(settings):
    """Сохраняет настройки на диск (тихо, без падений на ошибках ФС)."""
    try:
        os.makedirs(APP_DIR, exist_ok=True)
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(settings, f, indent=2, ensure_ascii=False)
    except OSError:
        pass
