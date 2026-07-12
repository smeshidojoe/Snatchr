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
    "General": "Общее",
    "Advanced": "Дополнительно",
    "Cookies": "Cookies",
    "Browser for cookies": "Браузер для cookies",
    "Use cookies file…": "Указать файл cookies…",
    "Choose cookies file": "Выберите файл cookies",
    "Retrying with browser cookies…": "Повтор с cookies браузера…",
    "Embed Thumbnail": "Встроить обложку",
    "Notify about updates": "Уведомлять об обновлениях",
    "Channel links aren't supported — paste a video link.":
        "Ссылки на канал не поддерживаются — вставьте ссылку на видео.",
    "Copy downloaded file to clipboard": "Копировать скачанный файл в буфер",
    "Launch at startup": "Запускать при старте системы",
    "Parallel Downloads": "Одновременных загрузок",
    "Reset Settings": "Сбросить настройки",
    "Open Logs Folder": "Открыть папку логов",
    "Paste link on open": "Вставлять ссылку при открытии",
    "Reset all settings?": "Сбросить все настройки?",
    "This deletes the config and restarts Snatchr.":
        "Это удалит конфиг и перезапустит Snatchr.",
    "Embed Metadata": "Встроить метаданные",
    "Convert Youtube Videos": "Конвертировать видео с YouTube",
    "Watch clipboard for links": "Следить за буфером обмена",
    "Download this?": "Скачать это?",
    "Downloaded": "Скачано",
    "Toast": "Тост",
    "Corner": "В углу",
    "At cursor": "У курсора",
    "Pinned": "Закреплено",
    "Auto-hide": "Автоскрытие",
    "Allow Dragging": "Перетаскивание окна",
    "Choose": "Выбрать",
    "Update yt-dlp": "Обновить yt-dlp",
    "Update ffmpeg": "Обновить ffmpeg",
    "Clear Cache": "Очистить кэш",
    "Cache cleared": "Кэш очищен",
    "Paste": "Вставить",
    "Open": "Открыть",
    "Exit": "Выход",
    "Check for Updates": "Проверить обновления",
    "Version": "Версия",
    "Paste URL Here": "Вставьте ссылку сюда",
    "Copy link": "Копировать ссылку",
    "Remove": "Удалить",
    "Remove From List": "Убрать из списка",
    "Delete": "Удалить файл",
    "Couldn't delete — file in use": "Не удалось удалить — файл занят",
    "Confirm": "Подтвердить",
    "Spotlight": "Spotlight",
    "Enable Spotlight": "Включить Spotlight",
    "Shortcut": "Сочетание",
    "Press keys…": "Нажмите клавиши…",
    "Discard current trim?": "Сбросить текущую обрезку?",
    "Discard": "Сбросить",
    "Cancel": "Отмена",
    "Playlists: open the link in the main window":
        "Плейлисты — открой ссылку в окне программы",
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
    "From": "С",
    "To": "До",
    "Invalid time range": "Неверный интервал времени",
    "Best Quality": "Лучшее качество",
    "Best Compatibility (1080p)": "Лучшая совместимость (1080p)",
    "Thumbnail": "Обложка",
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
    "Paste a video link first": "Сначала скопируйте ссылку на видео",
    "A download is already running": "Загрузка уже выполняется",

    # --- Тексты ошибок yt-dlp (friendly_error), короткие ---
    "Downloaded, but conversion failed.": "Скачано, но без конвертации.",
    "Bot check — try cookies or later.": "Бот-чек — нужны cookies или позже.",
    "Age-restricted — sign-in needed.": "Возрастное — нужен вход.",
    "Sign-in required (401).": "Нужен вход (401).",
    "Access denied (403).": "Доступ запрещён (403).",
    "Not found (404).": "Не найдено (404).",
    "Too many requests — try later.": "Много запросов — попробуйте позже.",
    "Video is private.": "Видео приватное.",
    "Members-only content.": "Только для участников.",
    "Video unavailable.": "Видео недоступно.",
    "Not available in your region.": "Недоступно в регионе.",
    "X/Twitter: sign-in failed (yt-dlp limitation).": "X/Twitter: вход не удался (ограничение yt-dlp).",
    "No downloadable video found.": "Видео для скачивания не найдено.",
    "Browser cookies locked (Chrome encryption).": "Куки браузера недоступны (шифрование Chrome).",
    "Close the browser and retry (cookies busy).": "Закройте браузер и повторите (куки заняты).",
    "Couldn't read — site may have changed.": "Не прочитать — сайт изменился?",
    "Link not supported.": "Ссылка не поддерживается.",
    "Processing failed (ffmpeg).": "Ошибка обработки (ffmpeg).",
    "Not enough disk space.": "Нет места на диске.",
    "Download failed.": "Ошибка загрузки.",
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
    "Download Update": "Скачать обновление",
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
    "When you copy a link from a supported site, a toast\n"
    "appears offering to download it in the background.":
        "Когда вы копируете ссылку с поддерживаемого сайта,\n"
        "появляется тост с предложением скачать её в фоне.",
    "When you open the window, a freshly copied link is\n"
    "pasted into the field automatically — for the sites\n"
    "you tick below.":
        "При открытии окна недавно скопированная ссылка\n"
        "автоматически вставляется в поле — для сайтов,\n"
        "отмеченных ниже.",
    "A quick launcher (global shortcut) to paste a link, download\n"
    "it, and trim clips. Auto-hide closes it when it loses focus;\n"
    "Pinned keeps it open until you press the shortcut again.":
        "Быстрый вызов (глобальное сочетание): вставить ссылку, скачать\n"
        "и обрезать ролик. Автоскрытие закрывает окно при потере фокуса;\n"
        "Закреплено — держит открытым, пока не нажмёте сочетание снова.",
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
