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
        # Скачивание прямо по горячей клавише: ссылка берётся из буфера, окно не
        # открывается, виден только вращающийся значок в трее.
        "hk_download_enabled": False,     # включён ли этот способ
        "hk_download_video": "ctrl+alt+v",  # сочетание: скачать видео
        "hk_download_audio": "ctrl+alt+a",  # сочетание: скачать аудио
        "update_notify":     True,        # уведомлять тостом о новых версиях
        "update_dismissed_version": "",   # версия, тост которой уже закрыли
        "toast_copy_file":   True,        # копировать скачанный файл в буфер (Toast)
        "autostart":         False,       # запускать Snatchr при старте Windows
        "parallel_downloads": 2,          # одновременных загрузок (1..3)
        "speed_limit_mbps": 0,            # лимит скорости, МБ/с (0 = без лимита)
        "autopaste":         False,       # вставлять ссылку из буфера при открытии окна
        "trim_volume":       0.8,         # громкость превью в панели обрезки (0..1)
        "format_order":      [],          # порядок строк селектора (пусто => по умолчанию)
        "format_hidden":     [],          # скрытые строки селектора (ключи core.formats)
        # какие сайты автовставлять (пусто в конфиге => все; None здесь = все по умолчанию)
        "autopaste_sites":   ["youtube", "instagram", "tiktok", "reddit",
                              "twitter", "vk", "soundcloud"],
    }


def _allowed():
    """Списки допустимых значений (лениво — чтобы не тянуть темы/языки при
    импорте config, который читают почти все модули)."""
    try:
        from core.constants import THEMES, LANGUAGES, DEFAULT_THEME, DEFAULT_LANGUAGE
        return THEMES, LANGUAGES, DEFAULT_THEME, DEFAULT_LANGUAGE
    except Exception:
        return None, None, "Glass", "English"


def _num_in(v, lo, hi, fallback, integer=True):
    """
    Число в диапазоне [lo, hi] или fallback (bool числом не считаем).

    Целое, записанное как float (3.0), приводим к int, а не сбрасываем: так
    настройка юзера переживает и запись из JSON, и деление где-то в коде.
    """
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return fallback
    if integer:
        if float(v) != int(v):
            return fallback
        v = int(v)
    return v if lo <= v <= hi else fallback


def validate(data):
    """
    Приводит настройки к рабочему виду: неверное значение заменяется дефолтом.

    Битый или устаревший config.json не должен мешать запуску — раньше значения
    подставлялись как есть, и, например, отрицательный лимит скорости ронял все
    загрузки, а имя удалённой темы — само построение окна.
    """
    d = defaults()
    themes, languages, def_theme, def_lang = _allowed()

    # 1. Общее правило: тип не совпал с типом дефолта -> берём дефолт.
    #    (Закрывает большинство случаев без отдельного валидатора на каждый ключ.)
    for key, dv in d.items():
        if key not in data:
            continue
        v = data[key]
        if isinstance(dv, bool):
            ok = isinstance(v, bool)
        elif isinstance(dv, (int, float)):
            ok = isinstance(v, (int, float)) and not isinstance(v, bool)
        else:
            ok = isinstance(v, type(dv))
        data[key] = v if ok else dv

    # 2. Точечные проверки там, где верный тип ещё не означает верное значение.
    data["parallel_downloads"] = _num_in(data.get("parallel_downloads"), 1, 3, 2)
    data["speed_limit_mbps"]   = _num_in(data.get("speed_limit_mbps"), 0, 1000, 0)
    data["trim_volume"] = _num_in(data.get("trim_volume"), 0.0, 1.0, 0.8, integer=False)
    data["ytdlp_updated"] = _num_in(data.get("ytdlp_updated"), 0, 1 << 40, 0)

    if themes and data.get("theme") not in themes:
        data["theme"] = def_theme
    if languages and data.get("language") not in languages:
        data["language"] = def_lang
    if data.get("usage_mode") not in ("toggle", "focus"):
        data["usage_mode"] = "focus"
    if data.get("toast_position") not in ("corner", "cursor"):
        data["toast_position"] = "corner"
    if data.get("spotlight_dismiss") not in ("focus", "manual"):
        data["spotlight_dismiss"] = "focus"
    if data.get("ytdlp_channel") not in ("stable", "nightly"):
        data["ytdlp_channel"] = "stable"

    # Списки должны содержать только строки (по ним итерируются селекторы).
    for key in ("format_order", "format_hidden", "autopaste_sites"):
        v = data.get(key)
        data[key] = [x for x in v if isinstance(x, str)] if isinstance(v, list) else d[key]

    # Пустой путь загрузки сломал бы сохранение файла; несуществующий оставляем
    # (диск может быть временно отключён — пусть ошибка всплывёт при загрузке).
    if not str(data.get("download_path") or "").strip():
        data["download_path"] = d["download_path"]
    # Пустое сочетание означало бы «хоткей есть, но никакой» — берём дефолт.
    for key in ("spotlight_combo", "hk_download_video", "hk_download_audio"):
        if not str(data.get(key) or "").strip():
            data[key] = d[key]
    # Одинаковые сочетания у видео и аудио: сработало бы что-то одно, и какое
    # именно — непредсказуемо. Возвращаем аудио к дефолту.
    if data["hk_download_video"] == data["hk_download_audio"]:
        data["hk_download_audio"] = d["hk_download_audio"]
        if data["hk_download_video"] == data["hk_download_audio"]:
            data["hk_download_video"] = d["hk_download_video"]

    return data


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

    # Миграция переименованной темы (до валидации: старое имя в THEMES не входит).
    if data.get("theme") == "Rose Negative":
        data["theme"] = "White Rose"

    return validate(data)


def save(settings):
    """Сохраняет настройки на диск (тихо, без падений на ошибках ФС)."""
    try:
        os.makedirs(APP_DIR, exist_ok=True)
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(settings, f, indent=2, ensure_ascii=False)
    except OSError:
        pass
