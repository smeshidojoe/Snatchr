"""
Лёгкая система локализации.

Ключ перевода — это английская строка (она же fallback). Любую видимую надпись
в UI оборачиваем в tr("English text"). Незнакомые/непереведённые строки
возвращаются как есть (английский), поэтому покрытие может быть частичным.
"""

from core.constants import DEFAULT_LANGUAGE

# Переводы: { язык: { english_key: translation } }. English — passthrough.
_RU = {
    # --- Кнопки / разделы / общие ---
    "Settings": "Настройки",
    "About": "О программе",
    "Download Folder": "Папка загрузки",
    "Post-Processing": "Постобработка",
    "Processing": "Обработка",
    "Usage": "Режим работы",
    "Embed Thumbnail": "Встроить обложку",
    "Embed Metadata": "Встроить метаданные",
    "Convert Youtube Videos": "Конвертировать видео с YouTube",
    "Pinned": "Закреплено",
    "Auto-hide": "Автоскрытие",
    "Allow Dragging": "Перетаскивание окна",
    "Choose": "Выбрать",
    "Update yt-dlp": "Обновить yt-dlp",
    "Update ffmpeg": "Обновить ffmpeg",
    "Exit": "Выход",
    "Check for Updates": "Проверить обновления",
    "Version": "Версия",
    "Menu Bar Icon": "Иконка в трее",
    "Theme": "Тема",
    "Language": "Язык",
    "2026 Developed by ": "2026 Разработано ",

    # --- Главный экран ---
    "Download": "Скачать",
    "Analyze": "Анализ",
    "Stop": "Стоп",
    "Video": "Видео",
    "Audio": "Аудио",
    "Multiple Links": "Несколько ссылок",
    "Best Quality": "Лучшее качество",
    "Best Compatibility (1080p)": "Лучшая совместимость (1080p)",
    "Paste video link here...": "Вставьте ссылку на видео...",
    "Paste video links (one per line)…": "Вставьте ссылки на видео (по одной в строке)…",
    "Select All": "Выбрать все",
    "Deselect All": "Снять все",
    "Playlist": "Плейлист",
    "Unknown": "Неизвестно",

    # --- Статусы / сообщения ---
    "Fetching info…": "Получение данных…",
    "Fetching playlist…": "Получение плейлиста…",
    "Could not read this link.": "Не удалось прочитать ссылку.",
    "Could not read any of the links.": "Не удалось прочитать ни одну ссылку.",
    "Downloading required libraries…": "Скачивание необходимых компонентов…",
    "Downloading Libraries…": "Скачивание компонентов…",
    "Failed to download libraries.": "Не удалось скачать компоненты.",
    "Converting…": "Конвертация…",
    "Trying streamlink…": "Пробуем streamlink…",
    "Stopped": "Остановлено",
    "Download failed": "Ошибка загрузки",
    "Saved with errors": "Сохранено с ошибками",
    "All downloads failed": "Все загрузки не удались",
    "Saved to": "Сохранено в",
    "File Size ~": "Размер файла ~",
    "Setup failed": "Ошибка установки",
    "Checking…": "Проверка…",
    "Update available": "Доступно обновление",
    "You're up to date": "Установлена последняя версия",
    "Check failed — try later": "Не удалось проверить — попробуйте позже",
    "Update & Restart": "Обновить и перезапустить",
    "Downloading update…": "Скачивание обновления…",

    # --- Подсказки (tooltip) ---
    "Re-encode YouTube videos into an editor-friendly format\n"
    "(SDR → H.264, HDR → HEVC 10-bit, mp4) so they import\n"
    "cleanly into video editing software. Uses the GPU when\n"
    "available, with a CPU fallback.":
        "Перекодировать видео с YouTube в удобный для монтажа формат\n"
        "(SDR → H.264, HDR → HEVC 10-bit, mp4), чтобы они без проблем\n"
        "импортировались в видеоредакторы. Использует GPU, если доступен,\n"
        "с откатом на процессор.",
    "Pinned: the tray icon opens and closes the window.\n"
    "Auto-hide: the tray icon opens the window; it closes\n"
    "on Esc or when you click outside it.":
        "Закреплено: иконка в трее открывает и закрывает окно.\n"
        "Автоскрытие: иконка открывает окно; оно закрывается\n"
        "по Esc или при клике вне него.",
    "Drag the window by holding an empty area at the top.\n"
    "The position resets the next time the window is shown.":
        "Перетаскивайте окно за пустую область сверху.\n"
        "Позиция сбрасывается при следующем открытии окна.",
}

_TRANSLATIONS = {
    "English": {},
    "Русский": _RU,
}

_current = DEFAULT_LANGUAGE


def set_language(lang):
    global _current
    _current = lang if lang in _TRANSLATIONS else DEFAULT_LANGUAGE


def language():
    return _current


def available():
    return list(_TRANSLATIONS.keys())


def tr(key):
    """Перевод строки на текущий язык (с откатом на английский / сам ключ)."""
    table = _TRANSLATIONS.get(_current, {})
    return table.get(key) or key
