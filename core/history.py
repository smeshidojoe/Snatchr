"""
История скачиваний для окна Spotlight: единый список файлов, скачанных из окна
программы, из трея (Paste/тост) и из самого Spotlight.

Хранение — рядом с конфигом: %APPDATA%/Snatchr/history.json + папка thumbnails/
с обложками (по одному jpg на запись). Обложку снимаем из готового файла через
ffmpeg — единообразно для любого источника, без сети.

Формат записи:
  {id, url, host, title, path, thumb, ts}
"""

import os
import json
import time
import uuid
from urllib.parse import urlparse

from core.config import APP_DIR
from core import trimmer

HISTORY_PATH = os.path.join(APP_DIR, "history.json")
THUMBS_DIR = os.path.join(APP_DIR, "thumbnails")
WAVEFORMS_DIR = os.path.join(APP_DIR, "waveforms")   # заготовки волн (аудио)

MAX_ITEMS = 200        # старые записи подрезаем, чтобы список не рос бесконечно

_AUDIO_EXT = (".mp3", ".wav", ".flac", ".aac", ".ogg", ".opus", ".m4a", ".wma")


def waveform_path(entry_id):
    return os.path.join(WAVEFORMS_DIR, entry_id + ".peaks")


def set_waveform(entry_id, path):
    """Записывает путь готовой заготовки волны в запись истории."""
    items = load()
    changed = False
    for it in items:
        if it.get("id") == entry_id:
            it["waveform"] = path or ""
            changed = True
            break
    if changed:
        _save(items)

# Человекочитаемое имя площадки по хосту ссылки (подпись под URL в списке).
_HOST_NAMES = {
    "youtube.com": "YouTube", "youtu.be": "YouTube",
    "instagram.com": "Instagram", "tiktok.com": "TikTok",
    "reddit.com": "Reddit", "redd.it": "Reddit",
    "pornhub.com": "Pornhub", "vimeo.com": "Vimeo",
    "twitch.tv": "Twitch", "x.com": "X", "twitter.com": "X",
    "facebook.com": "Facebook", "fb.watch": "Facebook",
    "soundcloud.com": "SoundCloud",
    "vk.com": "VK", "vkvideo.ru": "VK", "ok.ru": "OK", "rutube.ru": "RuTube",
}


def host_label(url):
    """Название площадки по ссылке ('Instagram', 'YouTube', …) или домен."""
    try:
        host = (urlparse(url or "").netloc or "").split("@")[-1].split(":")[0].lower()
    except Exception:
        return ""
    if host.startswith("www."):
        host = host[4:]
    for h, name in _HOST_NAMES.items():
        if host == h or host.endswith("." + h):
            return name
    return host or ""


def load():
    """Список записей, новые сверху. Тихо возвращает [] при любой ошибке."""
    try:
        with open(HISTORY_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return data
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass
    return []


def _save(items):
    try:
        os.makedirs(APP_DIR, exist_ok=True)
        with open(HISTORY_PATH, "w", encoding="utf-8") as f:
            json.dump(items, f, indent=2, ensure_ascii=False)
    except OSError:
        pass


def _thumb_path(entry_id):
    return os.path.join(THUMBS_DIR, entry_id + ".jpg")


def _download_thumb(thumb_url, out_path):
    """Скачивает картинку-обложку по URL в out_path. Путь или None."""
    if not thumb_url or thumb_url.upper() == "NA":
        return None
    try:
        import urllib.request
        req = urllib.request.Request(thumb_url, headers={"User-Agent": "Snatchr"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = resp.read()
        if not data:
            return None
        with open(out_path, "wb") as f:
            f.write(data)
        return out_path
    except Exception:
        return None


def add(path, url, title=None, thumb_bytes=None, thumb_url=None, uploader=None):
    """Добавляет запись о скачанном файле и возвращает её. None — если файла нет.

    Обложка: приоритет — постер сайта из yt-dlp (готовые байты thumb_bytes или
    thumb_url), т.к. это официальная картинка, а не случайный кадр. Если её нет —
    запасной вариант: кадр из самого файла через ffmpeg."""
    if not path or not os.path.isfile(path):
        return None
    entry_id = uuid.uuid4().hex[:12]
    thumb = None
    try:
        os.makedirs(THUMBS_DIR, exist_ok=True)
        tp = _thumb_path(entry_id)
        if thumb_bytes:
            try:
                with open(tp, "wb") as f:
                    f.write(thumb_bytes)
                thumb = tp
            except OSError:
                thumb = None
        elif thumb_url:
            thumb = _download_thumb(thumb_url, tp)
        if not thumb:
            thumb = trimmer.thumbnail(path, tp, width=320)   # фолбэк — кадр из видео
    except Exception:
        thumb = None
    # Разрешение/длительность снимаем с готового файла (для Paste/Toast/Spotlight,
    # где анализа не было) — как «данные о видео» в окне программы.
    try:
        media = trimmer.probe_media(path)
    except Exception:
        media = {}

    entry = {
        "id": entry_id,
        "url": (url or "").strip(),
        "host": host_label(url),
        "title": title or os.path.splitext(os.path.basename(path))[0],
        "uploader": uploader or "",
        "path": path,
        "thumb": thumb or "",
        "height": media.get("height") or 0,
        "duration": media.get("duration") or 0,
        "is_image": os.path.splitext(path)[1].lower() in (
            ".jpg", ".jpeg", ".png", ".webp", ".gif"),
        "is_audio": os.path.splitext(path)[1].lower() in _AUDIO_EXT,
        "waveform": "",              # заготовка волны (генерится в фоне для аудио)
        "ts": int(time.time()),
    }
    items = load()
    items.insert(0, entry)
    # Подрезаем хвост, удаляя обложки/волны выпавших записей.
    for old in items[MAX_ITEMS:]:
        _remove_thumb(old.get("thumb"))
        _remove_waveform(old.get("waveform"))
    items = items[:MAX_ITEMS]
    _save(items)
    return entry


def _remove_waveform(wpath):
    try:
        if wpath and os.path.isfile(wpath):
            os.remove(wpath)
    except OSError:
        pass


def _remove_thumb(thumb):
    try:
        if thumb and os.path.isfile(thumb):
            os.remove(thumb)
    except OSError:
        pass


def remove(entry_id):
    """Удаляет запись (и её обложку) из истории."""
    items = load()
    kept = []
    for it in items:
        if it.get("id") == entry_id:
            _remove_thumb(it.get("thumb"))
            _remove_waveform(it.get("waveform"))
        else:
            kept.append(it)
    _save(kept)
    return kept


def file_gone(path):
    """Файл ДЕЙСТВИТЕЛЬНО удалён? True только если файла нет, НО его папка
    доступна. Если недоступна сама папка (диск/сеть/OneDrive не смонтированы,
    нестандартный CWD сразу после самообновления) — файл просто временно
    недоступен: возвращаем False, чтобы не стереть историю по разовому сбою."""
    if not path:
        return True
    try:
        if os.path.isfile(path):
            return False
        parent = os.path.dirname(path) or "."
        if not os.path.isdir(parent):
            return False           # папки нет/недоступна — запись НЕ трогаем
        return True                # папка есть, а файла нет — реально удалён
    except OSError:
        return False


def prune_missing():
    """Убирает записи, чей файл реально удалён с диска (+ их обложки). Временную
    недоступность папки (см. file_gone) НЕ считаем удалением, поэтому разовый сбой
    доступа больше не затирает историю. Возвращает актуальный список."""
    items = load()
    kept, dropped = [], []
    for it in items:
        if file_gone(it.get("path")):
            dropped.append(it)
        else:
            kept.append(it)
    if dropped:
        for it in dropped:
            _remove_thumb(it.get("thumb"))
            _remove_waveform(it.get("waveform"))
        _save(kept)
    return kept
