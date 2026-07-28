"""
Ember — второй движок извлечения/скачивания рядом с yt-dlp.

Роли:
  * Twitter/X — ОСНОВНОЙ путь (yt-dlp там часто не справляется).
  * Остальные поддерживаемые сервисы — ЗАПАСНОЙ путь, когда yt-dlp исчерпал
    все свои повторы (куки, impersonation, streamlink).

Наружу отдаём данные в том же виде, что и downloader: info-словарь для карточки
и опции селектора, прогресс через hooks.on_progress с теми же ключами. Поэтому
UI не отличает движки — кроме флага `_ember`, по которому мы пропускаем
конвертацию (Ember отдаёт готовый H.264, перекодировать нечего).
"""

import os
import time

try:
    import ember
    HAVE = True
except Exception:                     # библиотека не установлена — молча живём без неё
    ember = None
    HAVE = False

from core import tools
from core.i18n import tr

# Сервисы, для которых Ember — основной движок (не запасной).


def available():
    return HAVE


def can_handle(url):
    """Умеет ли Ember эту ссылку."""
    if not HAVE or not url:
        return False
    try:
        return bool(ember.can_extract(url))
    except Exception:
        return False


def is_primary(url):
    """Ссылка из сервиса, где Ember идёт ПЕРЕД yt-dlp (Twitter/X)."""
    if not can_handle(url):
        return False
    low = (url or "").lower()
    return ("twitter.com" in low or "//x.com" in low or ".x.com" in low)


def _browser(settings):
    """Браузер для кук: настройка приложения ('auto' -> системный по умолчанию)."""
    b = (settings or {}).get("cookies_browser") or "auto"
    if b == "auto":
        b = tools.default_browser()
    return b or None


def _cookies_from_file(settings, url=""):
    """Куки из своего cookies.txt (формат Netscape) -> {name: value}.

    yt-dlp такой файл принимает как есть (--cookies), Ember — только словарём,
    поэтому разбираем сами. Фильтруем по домену ссылки, чтобы не отдавать
    сервису куки всех остальных сайтов."""
    path = (settings or {}).get("cookies_file") or ""
    if not path or not os.path.isfile(path):
        return None
    host = ""
    try:
        from urllib.parse import urlparse
        host = (urlparse(url or "").netloc or "").lower().lstrip("www.")
    except Exception:
        pass
    out = {}
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split("\t")
                if len(parts) < 7:
                    continue
                domain, name, value = parts[0].lower().lstrip("."), parts[5], parts[6]
                if host and host not in domain and domain not in host:
                    continue           # куки другого сайта — не отдаём
                out[name] = value
    except OSError:
        return None
    return out or None


def extract(url, settings=None, timeout=25.0):
    """Result Ember. Бросает ember.EmberError при неудаче.

    Куки — те же, что у yt-dlp: сначала свой cookies.txt (если задан в
    настройках), иначе браузер. Если с куками не вышло — пробуем без них:
    публичные ссылки достанутся и так (та же логика, что у yt-dlp)."""
    if not HAVE:
        raise RuntimeError("ember is not available")
    return _call(ember.extract, url, settings, timeout=timeout)


def _is_cookie_problem(exc):
    """Ошибка ЧТЕНИЯ кук, а не самой ссылки.

    Ember сообщает про заблокированную базу браузера тем же EmberError, что и
    про недоступный пост. Раньше любой EmberError на этапе кук прерывал всю
    цепочку, и до анонимной попытки дело не доходило."""
    low = str(exc or "").lower()
    return any(k in low for k in (
        "cookie", "locks its cookie", "could not read", "database is locked",
        "keyring", "decrypt"))


def _call(fn, url, settings, **kwargs):
    """Вызов ember-функции с куками (файл -> браузер -> без кук).

    Возвращает результат первой удачной попытки. Ошибку доступа к посту
    пробрасываем сразу — перебирать источники кук после неё бессмысленно."""
    jar = _cookies_from_file(settings, url)
    if jar:
        try:
            return fn(url, cookies=jar, **kwargs)
        except ember.EmberError as exc:
            if not _is_cookie_problem(exc):
                raise                  # ошибка самой ссылки — пробрасываем
        except Exception:
            pass
    br = _browser(settings)
    if br:
        try:
            return fn(url, cookies_from_browser=br, **kwargs)
        except ember.EmberError as exc:
            if not _is_cookie_problem(exc):
                raise
        except Exception:
            pass                       # не смогли с куками -> пробуем без них
    return fn(url, **kwargs)


# --- плейлисты и авторские ленты ----------------------------------- #
def supports_playlist(url):
    """Умеет ли Ember разобрать эту ссылку как набор (напр., сет SoundCloud)."""
    if not HAVE or not url:
        return False
    try:
        return bool(ember.supports_playlist(url))
    except Exception:
        return False


def supports_timeline(url):
    """Умеет ли Ember перечислить последние посты автора (профиль/канал)."""
    if not HAVE or not url:
        return False
    try:
        return bool(ember.supports_timeline(url))
    except Exception:
        return False


def is_collection(url):
    """Ссылка — НАБОР (сет/профиль/канал), а не одиночный пост.

    Осторожно: supports_playlist() у Ember True и для одиночного трека
    (одиночная ссылка отдаётся «плейлистом из одной записи»), поэтому набором
    считаем только ленту автора либо явный сет в адресе."""
    if not HAVE or not url:
        return False
    low = str(url).lower()
    if supports_timeline(low):
        return True
    return "/sets/" in low and supports_playlist(low)


def playlist_entries(url, settings=None, limit=30, timeout=25.0):
    """Записи набора/ленты в формате downloader.playlist_entries.

    Сначала пробуем набор (плейлист/сет), затем — ленту автора. Возвращаем
    список [{url, title, duration, thumbnail, uploader}] или [] — тогда
    вызывающий откатывается на yt-dlp."""
    if not HAVE or not url:
        return []
    pl = None
    try:
        if supports_playlist(url):
            pl = _call(ember.extract_playlist, url, settings, timeout=timeout)
        elif supports_timeline(url):
            pl = _call(ember.extract_timeline, url, settings,
                       limit=limit, timeout=timeout)
    except Exception:
        return []
    if pl is None:
        return []
    out = []
    for r in (getattr(pl, "entries", None) or []):
        src = getattr(r, "source_url", "") or ""
        if not src:
            continue
        out.append({"url": src,
                    "title": r.title or "Unknown",
                    "duration": r.duration,
                    "thumbnail": r.thumbnail or "",
                    "uploader": r.author or getattr(pl, "author", "") or ""})
    return out


# ------------------------------------------------------------------ #
def _heights(result):
    """Доступные высоты (по убыванию) из вариантов/HLS-мастера, или []."""
    hs = set()
    for m in (result.media or []):
        if m.kind != "video":
            continue
        for v in (m.variants or []):
            if v.height:
                hs.add(int(v.height))
        if not hs:
            try:
                for h in (ember.available_qualities(m) or []):
                    if h:
                        hs.add(int(h))
            except Exception:
                pass
    return sorted(hs, reverse=True)


def to_info(result):
    """Result -> info-словарь в формате, который ждёт UI (карточка + история)."""
    heights = _heights(result)
    return {
        "title": result.title or "",
        "uploader": result.author or "",
        "duration": result.duration or 0,
        "thumbnail": result.thumbnail or "",
        "height": heights[0] if heights else 0,
        "_ember": True,                       # метка движка (см. модуль-docstring)
        "_ember_heights": heights,
        "_ember_kind": result.kind,
        # Галерея (карусель фото/видео): элементов больше одного, качаем ВСЕ в
        # подпапку. Число нужно селектору («Media (N)») и строке истории.
        "_ember_count": len(getattr(result, "media", None) or []),
        "_ember_media_kinds": [getattr(m, "kind", "") for m in
                               (getattr(result, "media", None) or [])],
        "formats": [],                        # селектор строим из _ember_heights
    }


def is_gallery(info):
    """Пост, который качаем целиком в подпапку и показываем стопкой.

    Это карусель (несколько файлов) ИЛИ пост из одних картинок — даже
    единственное фото идёт тем же путём: выбирать качество не из чего, а блок
    в истории выглядит одинаково.
    """
    i = info or {}
    if i.get("_ember_kind") == "gallery":
        return True
    if int(i.get("_ember_count") or 0) > 1:
        return True
    kinds = [str(k or "").lower() for k in (i.get("_ember_media_kinds") or [])]
    return bool(kinds) and all(k in ("photo", "image", "gif") for k in kinds)


def media_count(info):
    return int((info or {}).get("_ember_count") or 0)


def format_options(info, settings=None):
    """Опции селектора качества для Ember-ссылки (аналог downloader.video_formats).

    Ember сам склеивает лучшее качество, поэтому «Best Quality» — без ограничения
    высоты, а остальные строки просто задают потолок (max_height).

    settings — те же настройки, что и у yt-dlp-ветки: Format Priority скрывает
    и переупорядочивает строки. Ключи здесь того же вида («480_H.264»), так что
    прячется всё ровно как у yt-dlp. Без settings список отдаётся как есть."""
    from core import formats
    from core.trimmer import res_label
    # Галерея: качество выбирать не из чего — берём весь пост целиком.
    if is_gallery(info):
        n = media_count(info)
        return [{"label": "%s (%d)" % (tr("Media"), n), "mp3": False,
                 "key": "gallery", "ember": True, "gallery": True,
                 "height": 0, "count": n}]
    opts = [{"label": tr("Best Quality"), "mp3": False, "key": "best",
             "ember": True, "height": 0}]
    for h in (info or {}).get("_ember_heights") or []:
        opts.append({"label": res_label(h), "mp3": False,
                     "key": "%d_H.264" % h, "ember": True, "height": int(h)})
    opts.append({"label": tr("Thumbnail"), "thumbnail": True, "mp3": False,
                 "key": "thumbnail", "ember": True, "height": 0})
    return formats.apply(opts, settings) if settings else opts


# ------------------------------------------------------------------ #
def _fmt_size(n):
    """Байты -> «12.3MiB» (как в строках прогресса yt-dlp)."""
    if not n or n <= 0:
        return ""
    mib = n / (1024.0 * 1024.0)
    if mib >= 1024:
        return "%.2fGiB" % (mib / 1024.0)
    return "%.2fMiB" % mib


def _fmt_eta(secs):
    secs = max(0, int(secs))
    h, rem = divmod(secs, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _progress_cb(hooks, started):
    """Мост DownloadProgress -> hooks.on_progress (ключи как у yt-dlp).

    Скорость и ETA Ember не считает — меряем сами по таймеру (как в его CLI)."""
    def cb(p):
        if hooks is None or not getattr(hooks, "on_progress", None):
            return
        if getattr(hooks, "is_stopped", None) and hooks.is_stopped():
            return
        frac = p.fraction              # None, если размер неизвестен (HLS)
        info = {}
        if p.stage and p.stage != "download":
            # Ember склеивает/пишет метаданные — это наша «постобработка».
            info["stage"] = "post"
            hooks.on_progress(info)
            return
        elapsed = max(0.001, time.time() - started[0])
        speed = p.downloaded / elapsed
        if frac is not None:
            info["frac"] = max(0.0, min(1.0, frac))
            info["percent_str"] = "%.1f%%" % (info["frac"] * 100)
            if speed > 0 and p.total:
                info["eta"] = _fmt_eta((p.total - p.downloaded) / speed)
        elif p.segments_total:         # HLS без размера — ведём по сегментам
            frac = p.segments_done / float(p.segments_total)
            info["frac"] = max(0.0, min(1.0, frac))
            info["percent_str"] = "%.1f%%" % (info["frac"] * 100)
        info["speed"] = "%.2fMiB/s" % (speed / (1024.0 * 1024.0))
        info["downloaded"] = _fmt_size(p.downloaded)
        info["size"] = _fmt_size(p.total)
        hooks.on_progress(info)
    return cb


def download_all(result, out_dir, hooks=None):
    """Скачивает ВСЕ файлы поста в out_dir. Возвращает список путей."""
    if not HAVE:
        return []
    started = [time.time()]
    total = max(1, len(getattr(result, "media", None) or []))
    paths = ember.download(result, out_dir=out_dir,
                           on_progress=_progress_cb_multi(hooks, started, total),
                           concurrency=6)
    return [p for p in (paths or []) if p and os.path.isfile(p)]


def _progress_cb_multi(hooks, started, total):
    """Прогресс по ВСЕМУ посту, а не по каждому файлу отдельно.

    Ember сообщает долю для текущего файла и каждый раз начинает с нуля —
    полоса дёргалась бы назад на каждом элементе. Считаем сквозную долю:
    завершённые файлы плюс доля текущего, поделённые на общее число.
    """
    inner = _progress_cb(hooks, started)
    seen = []                      # пути в порядке появления — это и есть индекс

    def cb(p):
        path = getattr(p, "path", None)
        if path and path not in seen:
            seen.append(path)
        done = max(0, len(seen) - 1)          # сколько файлов уже позади
        frac = getattr(p, "fraction", None)
        if frac is None:
            segs = getattr(p, "segments_total", 0) or 0
            frac = (getattr(p, "segments_done", 0) / float(segs)) if segs else 0.0
        overall = min(1.0, (done + max(0.0, min(1.0, frac))) / float(total))

        class _P:                              # подменяем долю на сквозную
            pass
        q = _P()
        for a in ("stage", "downloaded", "total", "segments_done",
                  "segments_total", "path", "eta", "speed", "elapsed"):
            setattr(q, a, getattr(p, a, None))
        q.fraction = overall
        q.total = None                         # ETA по одному файлу тут врала бы
        inner(q)

    return cb


def post_folder_name(result, url):
    """Имя подпапки поста: «<автор> - <id поста>»."""
    author = _safe_name(getattr(result, "author", None) or "") or "post"
    pid = ""
    try:
        from urllib.parse import urlparse
        parts = [s for s in (urlparse(str(url or "")).path or "").split("/") if s]
        if parts:
            pid = parts[-1]
    except Exception:
        pid = ""
    pid = _safe_name(pid) if pid else ""
    return ("%s - %s" % (author, pid)) if pid else author


def download(result, out_dir, option=None, hooks=None, title=None):
    """Скачивает Result в out_dir. Возвращает путь к файлу или ''.

    option — опция селектора (см. format_options): 'height' задаёт потолок
    качества, 'mp3'/'audio' — только звук."""
    if not HAVE:
        return ""
    o = option or {}
    started = [time.time()]
    kwargs = {
        "out_dir": out_dir,
        "on_progress": _progress_cb(hooks, started),
        "concurrency": 6,              # параллельные HLS-сегменты
        "audio_only": bool(o.get("mp3") or o.get("audio")),
        "thumbnail": bool(o.get("thumbnail")),
    }
    if o.get("height"):
        kwargs["max_height"] = int(o["height"])
    if title:
        kwargs["filename"] = _safe_name(title)
    paths = ember.download(result, **kwargs)
    if not paths:
        return ""
    # Обложка (thumbnail=True) кладётся отдельным файлом — при запросе только
    # обложки отдаём картинку, иначе самый большой медиафайл.
    if o.get("thumbnail"):
        imgs = [p for p in paths if os.path.splitext(p)[1].lower()
                in (".jpg", ".jpeg", ".png", ".webp")]
        if imgs:
            return imgs[0]
    return max(paths, key=lambda p: os.path.getsize(p) if os.path.isfile(p) else 0)


def _safe_name(title):
    """Имя файла без запрещённых символов (Ember сам расширение подставит)."""
    bad = '<>:"/\\|?*'
    name = "".join((" " if c in bad else c) for c in (title or "")).strip()
    return name[:120] or None
