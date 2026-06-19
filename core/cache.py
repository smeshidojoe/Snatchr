"""
Кэш результатов анализа ссылок (рядом с конфигом, cache.json).
Если ссылка уже анализировалась — отдаём сохранённую info без повторного
запроса к yt-dlp. LRU-вытеснение по размеру.
"""

import os
import json

from core.config import APP_DIR

CACHE_PATH = os.path.join(APP_DIR, "cache.json")
_MAX = 80
_data = None


def _load():
    global _data
    if _data is not None:
        return _data
    try:
        with open(CACHE_PATH, "r", encoding="utf-8") as f:
            _data = json.load(f)
        if not isinstance(_data, dict):
            _data = {}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        _data = {}
    return _data


def get(url):
    return _load().get(url)


def put(url, info):
    if not url or info is None:
        return
    d = _load()
    d.pop(url, None)          # переносим в конец (LRU)
    d[url] = info
    while len(d) > _MAX:
        del d[next(iter(d))]  # вытесняем самый старый
    _save()


def _save():
    try:
        os.makedirs(APP_DIR, exist_ok=True)
        with open(CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump(_data, f, ensure_ascii=False)
    except OSError:
        pass
