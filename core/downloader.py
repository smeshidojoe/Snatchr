"""
Движок загрузки (Фаза 1: одиночная ссылка).

probe(url)            — извлечь информацию о видео через `yt-dlp -J`.
video_formats(info)   — список вариантов для режима Video (Best + разрешения).
audio_formats(info)   — список вариантов для режима Audio (Best=mp3 + форматы).

Каждый вариант: {"label": str, "fmt": <-f選selector>, "mp3": bool}.
Команда скачивания собирается отдельно (всегда с --merge-output-format mp4).
Парсеры — чистые функции (тестируются на синтетическом info без сети).
"""

import os
import re
import json
import time
import uuid
import shutil
import unicodedata

from core import tools
from core import logbook
from core.i18n import tr
from core.config import APP_DIR

TMP_ROOT = os.path.join(APP_DIR, "tmp")   # временные папки загрузок рядом с конфигом

SEP = " · "          # разделитель блоков в подписи
MIN_HEIGHT = 480     # ниже 480p не показываем

# Best Quality: максимальное РАЗРЕШЕНИЕ среди не-AV1 кодеков. При равном
# разрешении yt-dlp сам предпочтёт VP9 кодеку H.264 (штатный порядок сортировки
# vcodec: av1 > vp9 > h264), поэтому явный VP9-first здесь не нужен и вреден.
# Раньше стоял "bv*[vcodec~='vp0?9']+ba" первым: он брал ЛУЧШИЙ VP9 ЛЮБОГО
# разрешения — и когда YouTube (обычно с куками) отдаёт VP9 только в 360p, а
# H.264 в 1080p, скачивалось 360p. AV1 исключаем — тяжело декодируется.
BEST_VIDEO_FMT = "bv*[vcodec!^=av01]+ba/b"
# Совместимость: максимальное разрешение с кодеком AVC (обычно до 1080p).
AVC_VIDEO_FMT = "bv*[vcodec^=avc]+ba/b"
PROGRESS_TAG = "@@SN@@"    # префикс строки прогресса
DEST_TAG = "@@DEST@@"      # префикс строки с итоговым путём файла


COOKIE_BROWSERS = ["chrome", "edge", "firefox", "brave", "opera", "vivaldi", "chromium"]


def file_cookie_args(settings):
    """--cookies <file>, если в настройках задан существующий файл кук."""
    f = (settings or {}).get("cookies_file") or ""
    return ["--cookies", f] if f and os.path.isfile(f) else []


def browser_cookie_args(settings):
    """--cookies-from-browser <browser> (выбранный в настройках или авто) или []."""
    b = (settings or {}).get("cookies_browser") or "auto"
    if b == "auto":
        b = tools.default_browser()
    return ["--cookies-from-browser", b] if b else []


def _del_cookie_copy(args):
    """Удаляет приватную копию кук (use_*.txt) из args сразу после запуска —
    она одноразовая (нужна только на один запуск yt-dlp)."""
    try:
        for i, a in enumerate(args):
            if a == "--cookies" and i + 1 < len(args):
                p = args[i + 1]
                if os.path.basename(p).startswith("use_"):
                    try:
                        os.remove(p)
                    except OSError:
                        pass
                return
    except Exception:
        pass


def cookie_args(settings, url=""):
    """Куки, которые применяем ВСЕГДА: свой файл (если задан), иначе — свежие из
    браузера. Кэширование кук в файл отключено — для всех сайтов (в т.ч. YouTube)
    куки тянутся заново каждый раз (надёжнее для сессионной авторизации)."""
    return file_cookie_args(settings) or browser_cookie_args(settings)


# ------------------------------------------------------------------ #
def probe(url, no_playlist=True, timeout=60, cookies=None):
    """Информация о ссылке (dict). Бросает RuntimeError при ошибке."""
    args = [tools.YTDLP_EXE, "-J", "--no-warnings"]
    args += tools.pot_ytdlp_args(url)   # PO-токен провайдер (только YouTube)
    if cookies:
        args += cookies
    if no_playlist:
        args.append("--no-playlist")
    args.append(url)
    try:
        r = tools.run(args, timeout=timeout)
    finally:
        _del_cookie_copy(args)          # одноразовую копию кук — сразу убрать
    if r.returncode != 0 or not r.stdout.strip():
        raise RuntimeError((r.stdout or r.stderr or "yt-dlp failed").strip())
    return json.loads(r.stdout)


# ------------------------------------------------------------------ #
def _res_label(height):
    if height >= 4320:
        return "8K"
    if height >= 2160:
        return "4K"
    if height >= 1440:
        return "1440p"
    return f"{height}p"


def _codec_label(vcodec):
    v = (vcodec or "").lower()
    if v.startswith(("av01", "av1")):
        return "AV1"
    if v.startswith(("vp9", "vp09")):
        return "VP9"
    if v.startswith(("avc1", "h264")):
        return "H.264"
    if v.startswith(("hev1", "hvc1", "h265", "hevc")):
        return "HEVC"
    return v.split(".")[0].upper() if v else "?"


def _bitrate_str(tbr):
    # >= 1 Мбит/с -> «~Nмбит», иначе «Nkbit».
    if not tbr:
        return None
    if tbr >= 1000:
        return f"~{round(tbr / 1000.0)}мбит"
    return f"{round(tbr)}kbit"


def _kbit(abr):
    if not abr:
        return None
    return int(round(abr))


def _fmt_for(f):
    """Селектор -f для конкретного формата."""
    if f.get("acodec") not in (None, "none"):
        return f["format_id"]            # прогрессивный — уже со звуком
    return f"{f['format_id']}+ba/b"      # video-only — добиваем bestaudio


def video_formats(info, youtube=True):
    """Best Quality + список разрешений по убыванию (см. спецификацию).

    YouTube: Best Quality = максимальное разрешение в VP9, плюс отдельная строка
    «Best Compatibility (1080p)» (максимальное доступное разрешение с кодеком AVC).
    Прочие сайты: Best Quality = максимальное разрешение (любой кодек); строки
    разрешений показываем, только если они реально доступны. AV1 не показываем.
    """
    if youtube:
        options = [
            {"label": tr("Best Quality"), "fmt": BEST_VIDEO_FMT, "mp3": False},
            {"label": tr("Best Compatibility (1080p)"),
             "fmt": AVC_VIDEO_FMT, "mp3": False},
        ]
    else:
        options = [{"label": tr("Best Quality"), "fmt": BEST_VIDEO_FMT, "mp3": False}]

    vids = []
    for f in info.get("formats", []):
        if f.get("vcodec") in (None, "none"):
            continue
        # AV1 полностью исключаем из селектора.
        if _codec_label(f.get("vcodec")) == "AV1":
            continue
        # На YouTube берём только video-only (звук добьём bestaudio); на прочих
        # сайтах допускаем прогрессивные форматы (уже со звуком).
        if youtube and f.get("acodec") not in (None, "none"):
            continue
        h = f.get("height") or 0
        if h < MIN_HEIGHT:
            continue
        vids.append(f)

    # Показываем тип (HDR/SDR), только если у ссылки есть оба типа.
    dranges = {(f.get("dynamic_range") or "SDR").upper() for f in vids}
    show_dr = len(dranges) > 1

    # Лучший битрейт на (разрешение, кодек, тип).
    best = {}
    for f in vids:
        h = f.get("height") or 0
        codec = _codec_label(f.get("vcodec"))
        dr = (f.get("dynamic_range") or "SDR").upper()
        key = (h, codec, dr)
        tbr = f.get("tbr") or f.get("vbr") or 0
        if key not in best or tbr > (best[key].get("tbr") or 0):
            best[key] = f

    items = list(best.items())
    items.sort(key=lambda kv: (kv[0][0], kv[1].get("tbr") or 0), reverse=True)

    for (h, codec, dr), f in items:
        parts = [_res_label(h), codec]
        if show_dr:
            parts.append(dr)
        br = _bitrate_str(f.get("tbr") or f.get("vbr"))
        if br:
            parts.append(br)
        options.append({
            "label": SEP.join(parts),
            "fmt": _fmt_for(f),
            "mp3": False,
        })
    options.append({"label": tr("Thumbnail"), "thumbnail": True, "mp3": False})
    return options


def audio_formats(info):
    """Best Quality (=mp3 лучшего качества) + реальные аудио-форматы источника."""
    options = [{"label": tr("Best Quality"), "fmt": "ba/b", "mp3": True}]

    auds = []
    for f in info.get("formats", []):
        if f.get("acodec") in (None, "none"):
            continue
        if f.get("vcodec") not in (None, "none"):
            continue  # только audio-only
        auds.append(f)

    seen = {}
    for f in auds:
        codec = (f.get("acodec") or "").split(".")[0]
        codec = {"mp4a": "m4a", "opus": "opus", "vorbis": "vorbis"}.get(codec, codec or "?")
        abr = f.get("abr") or f.get("tbr") or 0
        key = codec
        if key not in seen or abr > (seen[key].get("abr") or seen[key].get("tbr") or 0):
            seen[key] = f

    rows = sorted(seen.items(),
                  key=lambda kv: (kv[1].get("abr") or kv[1].get("tbr") or 0),
                  reverse=True)
    for codec, f in rows:
        kb = _kbit(f.get("abr") or f.get("tbr"))
        label = codec + (f"{SEP}~{kb}k" if kb else "")
        # audio=True + реальное расширение контейнера: чтобы уникальность имени и
        # expected_path считались по фактическому ext (.m4a/.webm/.opus), а не .mp4.
        options.append({"label": label, "fmt": f["format_id"], "mp3": False,
                        "audio": True, "ext": (f.get("ext") or codec)})
    return options


# ------------------------------------------------------------------ #
#  Сборка команды скачивания и парсинг прогресса
# ------------------------------------------------------------------ #
# Безопасная пунктуация в именах файлов (эмодзи/символы отсекаем, буквы любого
# языка, цифры и пробелы оставляем).
_SAFE_PUNCT = set(" .,!'’‘()[]{}—–-_#@&+=~%")


def _sanitize_name(name):
    """Чистое имя файла для ВСЕХ режимов скачивания: убираем эмодзи, спецсимволы
    и запрещённые в Windows символы (<>:\"/\\|?*), оставляя буквы (в т.ч.
    кириллицу), цифры, пробелы и безопасную пунктуацию."""
    out = []
    for ch in name or "":
        if ch in '<>:"/\\|?*':
            out.append(" ")                 # запрещено в именах файлов Windows
            continue
        cat = unicodedata.category(ch)
        if cat and (cat[0] in ("L", "N", "M") or cat == "Zs" or ch in _SAFE_PUNCT):
            out.append(ch)
        # прочее (эмодзи So/Sk, управляющие Cc/Cf, разделители и т.п.) выкидываем
    cleaned = " ".join("".join(out).split())    # схлопываем повторные пробелы
    cleaned = cleaned.strip().strip(".")
    return cleaned or "video"


def _unique_base(out_dir, base, ext):
    """Уникальная основа имени: base, base (1), base (2)… (если файл существует)."""
    cand, n = base, 1
    while os.path.exists(os.path.join(out_dir, cand + "." + ext)):
        cand = f"{base} ({n})"
        n += 1
    return cand


def _final_ext(option):
    """Расширение итогового файла (для проверки уникальности имени)."""
    o = option or {}
    if o.get("thumbnail"):
        return "jpg"
    if o.get("mp3"):
        return "mp3"
    if o.get("audio"):                 # аудио-исходник (m4a/opus/webm…)
        return o.get("ext") or "m4a"
    return "mp4"


def _merge_ext(option, url, settings):
    """Контейнер для склейки. Если дальше будет конвертация — пакуем в MKV
    (он без проблем держит VP9+opus), иначе сразу в MP4. Для аудио — свой ext."""
    o = option or {}
    if o.get("mp3"):
        return "mp3"
    if o.get("audio"):
        return o.get("ext") or "m4a"   # аудио не склеивается, ext = как у источника
    return "mkv" if should_convert(option, url, settings) else "mp4"


def _name_template(out_dir, option, title):
    if not title:
        return "%(title)s.%(ext)s"
    base = _unique_base(out_dir, _sanitize_name(title), _final_ext(option))
    return base + ".%(ext)s"


def _expected_path(option, url, settings, title, out_dir):
    """Детерминированный путь файла сразу после загрузки/склейки (в out_dir).
    None — если заголовок неизвестен (тогда полагаемся на распарсенный путь)."""
    if not title:
        return None
    base = _unique_base(out_dir, _sanitize_name(title), _final_ext(option))
    return os.path.join(out_dir, base + "." + _merge_ext(option, url, settings))


def build_download_args(option, url, settings, title=None, out_dir=None,
                        cookies=True, impersonate=False, info_json=None):
    """Аргументы yt-dlp для скачивания одиночной ссылки по выбранному варианту.
    cookies=False — команда БЕЗ кук (ретрай при сбое извлечения кук).
    impersonate=True — притворяться браузером через curl_cffi (ретрай на 403).
    info_json=<файл> — качать из готовой info (--load-info-json), без повторного
    извлечения (форматы/PO-токен уже получены на анализе)."""
    if out_dir is None:
        out_dir = settings.get("download_path") or os.path.join(
            os.path.expanduser("~"), "Downloads")

    # ВНИМАНИЕ: --print включает --quiet и глушит прогресс. Поэтому итоговый путь
    # берём из обычных строк вывода ([Merger]/[download] Destination) в parse_destination.
    out_tmpl = os.path.join(out_dir, _name_template(out_dir, option, title))
    args = [
        tools.YTDLP_EXE, "--newline", "--no-playlist",
        # Сбой второстепенных шагов (субтитры/обложка/встраивание) не должен
        # ронять загрузку самого видео. Реальный успех проверяем по наличию файла.
        "--ignore-errors",
        "-o", out_tmpl,
        "--progress-template",
        f"download:{PROGRESS_TAG}%(progress._percent_str)s|"
        f"%(progress._speed_str)s|%(progress._eta_str)s|"
        f"%(progress._total_bytes_str)s|%(progress._total_bytes_estimate_str)s|"
        f"%(progress._downloaded_bytes_str)s",
    ]
    if not info_json:                   # с готовой info извлечения нет — PO не нужен
        args += tools.pot_ytdlp_args(url)   # PO-токен провайдер (только YouTube)
    if impersonate:
        args += ["--impersonate", "chrome"]   # обход 403 по TLS-отпечатку
    if cookies:
        args += cookie_args(settings, url)   # куки: свой файл или браузер
    loc = tools.ffmpeg_location()
    if loc:
        args += ["--ffmpeg-location", loc]

    args += ["-f", option["fmt"]]
    if option.get("mp3"):
        args += ["-x", "--audio-format", "mp3", "--audio-quality", "0"]
    elif option.get("audio"):
        pass   # аудио-исходник: сохраняем как есть, без склейки/ремукса
    else:
        # При конвертации склеиваем в MKV (надёжно для VP9+opus), затем
        # перекодируем в mp4; без конвертации — сразу mp4.
        args += ["--merge-output-format", _merge_ext(option, url, settings)]

    # Скачивание по таймкодам (только одиночные ссылки): «*START-END» в секундах.
    sec = option.get("section")
    if sec:
        args += ["--download-sections", sec, "--force-keyframes-at-cuts"]

    # Пост-процессинг из настроек.
    if settings.get("embed_thumbnail"):
        args.append("--embed-thumbnail")

    if info_json:
        args += ["--load-info-json", info_json]   # качаем из готовой info
    else:
        args.append(url)
    return args


def cleanup_temp():
    """Стартовая уборка мусора (single-instance гарантирует, что чужих активных
    файлов нет): недокачанные папки заданий APP_DIR/tmp/* и осиротевшие
    snatchr_*.info.json в системном %TEMP% (остаются после краша/kill). Чистим
    ТОЛЬКО своё — по префиксу/расположению."""
    try:
        if os.path.isdir(TMP_ROOT):
            for n in os.listdir(TMP_ROOT):
                _rm_dir(os.path.join(TMP_ROOT, n))
    except OSError:
        pass
    try:
        import tempfile
        import glob
        for p in glob.glob(os.path.join(tempfile.gettempdir(), "snatchr_*.info.json")):
            try:
                os.remove(p)
            except OSError:
                pass
    except Exception:
        pass


def _write_info_json(info):
    """Пишет info во временный .json (для --load-info-json). Путь или None."""
    if not info:
        return None
    try:
        import tempfile
        fd, path = tempfile.mkstemp(suffix=".info.json", prefix="snatchr_")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(info, f)
        return path
    except Exception:
        return None


def _clean_size(s):
    s = (s or "").strip()
    return s if s and s.upper() not in ("N/A", "NA", "NONE") else ""


def parse_progress(line):
    """Строка прогресса -> {percent_str, speed, eta, frac, size} либо None."""
    if PROGRESS_TAG not in line:
        return None
    payload = line.split(PROGRESS_TAG, 1)[1].strip()
    parts = payload.split("|")
    pct = parts[0].strip() if len(parts) > 0 else ""
    speed = parts[1].strip() if len(parts) > 1 else ""
    eta = parts[2].strip() if len(parts) > 2 else ""
    size = _clean_size(parts[3]) if len(parts) > 3 else ""
    if not size and len(parts) > 4:
        size = _clean_size(parts[4])     # оценка, если точный размер неизвестен
    downloaded = _clean_size(parts[5]) if len(parts) > 5 else ""
    frac = None
    try:
        frac = max(0.0, min(1.0, float(pct.replace("%", "").strip()) / 100.0))
    except ValueError:
        pass
    return {"percent_str": pct, "speed": speed, "eta": eta, "frac": frac,
            "size": size, "downloaded": downloaded}


def _stream_count(line):
    """Сколько потоков сольются в один итоговый файл (видео+аудио), из строки
    '[info] … Downloading N format(s): 248+251' -> 2. Иначе None."""
    if "format(s):" not in line:
        return None
    spec = line.split("format(s):", 1)[1].strip().split()
    if not spec:
        return None
    return spec[0].count("+") + 1


def parse_destination(line):
    """Извлекает путь к итоговому файлу (приоритет — точная строка --print)."""
    if DEST_TAG in line:
        return line.split(DEST_TAG, 1)[1].strip().strip('"')
    markers = ("[Merger] Merging formats into ",
               "[ExtractAudio] Destination: ",
               "[download] Destination: ")
    for m in markers:
        if m in line:
            return line.split(m, 1)[1].strip().strip('"')
    return None


_ERROR_MAP = [
    ("conversion failed", "Downloaded, but conversion failed."),
    ("failed to decrypt with dpapi", "Browser cookies locked (Chrome encryption)."),
    ("could not copy chrome cookie", "Close the browser and retry (cookies busy)."),
    ("not a bot", "Bot check — try cookies or later."),
    ("confirm you", "Bot check — try cookies or later."),
    ("sign in to confirm your age", "Age-restricted — sign-in needed."),
    ("http error 401", "Sign-in required (401)."),
    ("http error 403", "Access denied (403)."),
    ("http error 404", "Not found (404)."),
    ("http error 429", "Too many requests — try later."),
    ("private video", "Video is private."),
    ("members-only", "Members-only content."),
    ("join this channel", "Members-only content."),
    ("video unavailable", "Video unavailable."),
    ("this video is not available", "Video unavailable."),
    ("not available in your country", "Not available in your region."),
    ("geo restricted", "Not available in your region."),
    ("could not authenticate", "X/Twitter: sign-in failed (yt-dlp limitation)."),
    ("no video could be found", "No downloadable video found."),
    ("unable to extract", "Couldn't read — site may have changed."),
    ("unsupported url", "Link not supported."),
    ("ffmpeg", "Processing failed (ffmpeg)."),
    ("no space left", "Not enough disk space."),
]


def is_cookie_error(text):
    """Провал из-за извлечения кук из браузера (Chrome App-Bound Encryption /
    залоченная БД), а не из-за самого видео — тогда есть смысл повторить без кук."""
    low = (text or "").lower()
    return ("failed to decrypt with dpapi" in low
            or ("could not copy" in low and "cookie" in low)
            or ("could not find" in low and "cookie" in low)
            or ("could not decrypt" in low and "cookie" in low)
            or "unable to read cookies" in low)


def is_auth_error(text):
    """Похоже ли, что ошибку можно обойти куками (бот-чек / вход / 403)."""
    low = (text or "").lower()
    return ("not a bot" in low or "confirm you" in low or "http error 403" in low
            or "sign in" in low or "login required" in low or "private video" in low
            or "members-only" in low or "age" in low and "confirm" in low)


def friendly_error(text, default=None):
    """Человекочитаемое (и локализованное) объяснение ошибки по выводу утилит."""
    low = (text or "").lower()
    for needle, msg in _ERROR_MAP:
        if needle in low:
            return tr(msg)
    return tr(default) if default else tr("Download failed.")


# ------------------------------------------------------------------ #
def is_youtube(url):
    u = (url or "").lower()
    return "youtube.com" in u or "youtu.be" in u


# Популярные сайты для мониторинга буфера обмена (yt-dlp умеет намного больше,
# но здесь — узкий список, чтобы не всплывать на любой ссылке).
_SUPPORTED_HOSTS = (
    "youtube.com", "youtu.be", "instagram.com", "tiktok.com",
    "reddit.com", "redd.it", "pornhub.com", "vimeo.com", "twitch.tv",
    # Русскоязычные сервисы (yt-dlp их поддерживает).
    "vk.com", "vkvideo.ru", "ok.ru", "rutube.ru",
)


def is_supported_url(url):
    """http(s)-ссылка на один из известных сайтов (для тоста из буфера обмена)."""
    u = (url or "").strip()
    if not (u.lower().startswith("http://") or u.lower().startswith("https://")):
        return False
    try:
        from urllib.parse import urlparse
        host = (urlparse(u).netloc or "").split("@")[-1].split(":")[0].lower()
    except Exception:
        return False
    if host.startswith("www."):
        host = host[4:]
    return any(host == h or host.endswith("." + h) for h in _SUPPORTED_HOSTS)


def is_playlist_url(url):
    u = (url or "").lower()
    return ("list=" in u) or ("/playlist" in u)


def is_channel_url(url):
    """Ссылка на КАНАЛ/ПРОФИЛЬ (а не на одно видео): yt-dlp по такой начинает
    перечислять все видео канала. Ловим YouTube (@handle, /channel/, /c/, /user/)
    и TikTok-профиль (/@user без /video/). Одиночные видео (/watch, /shorts/,
    youtu.be/ID, tiktok .../video/ID) каналами НЕ считаются."""
    from urllib.parse import urlparse
    try:
        pr = urlparse((url or "").strip())
    except Exception:
        return False
    host = (pr.netloc or "").lower()
    if host.startswith("www."):
        host = host[4:]
    path = pr.path or ""
    if "youtube.com" in host:
        if ("/watch" in path or "/shorts/" in path or "/clip/" in path
                or "v=" in (pr.query or "")):
            return False
        return bool(re.match(r"^/(@[^/]+|channel/|c/|user/)", path))
    if "tiktok.com" in host:
        # профиль: /@user  (одиночное видео: /@user/video/ID)
        return bool(re.match(r"^/@[^/]+/?$", path))
    return False


def is_downloadable_single(text):
    """True только если text — ОДНА ссылка на одиночное видео поддерживаемого
    сайта: без лишнего текста рядом, без плейлиста, без страницы канала/профиля.
    Для авто-триггеров (тост из буфера, Paste): не всплывать на скопированном
    тексте со ссылкой внутри и не запускать анализ целого канала/плейлиста."""
    t = (text or "").strip()
    if not t or len(t.split()) != 1:        # рядом со ссылкой есть посторонний текст
        return False
    return (is_supported_url(t)
            and not is_playlist_url(t)
            and not is_channel_url(t))


def slim_info(info):
    """Урезанная info для кэша: только нужные поля + обложка + форматы."""
    keep = ("title", "uploader", "channel", "duration", "thumbnail")
    out = {k: info.get(k) for k in keep if info.get(k) is not None}
    fmt_keys = ("format_id", "vcodec", "acodec", "height",
                "tbr", "vbr", "abr", "dynamic_range", "ext")
    fmts = []
    for f in info.get("formats", []):
        fmts.append({k: f.get(k) for k in fmt_keys if f.get(k) is not None})
    out["formats"] = fmts
    return out


def should_convert(option, url, settings):
    """Нужна ли конвертация: галочка вкл, видео (не mp3/не аудио), источник — YouTube."""
    o = option or {}
    return (bool(settings.get("convert_yt"))
            and is_youtube(url)
            and not o.get("mp3")
            and not o.get("audio"))


def probe_flat(url, timeout=90):
    """Быстрый разбор плейлиста (--flat-playlist): только список записей."""
    args = [tools.YTDLP_EXE, "-J", "--flat-playlist", "--no-warnings", url]
    r = tools.run(args, timeout=timeout)
    if r.returncode != 0 or not r.stdout.strip():
        raise RuntimeError((r.stdout or r.stderr or "yt-dlp failed").strip())
    return json.loads(r.stdout)


def _best_thumb(entry):
    """URL наибольшей обложки из flat-записи плейлиста (или None)."""
    thumbs = entry.get("thumbnails")
    if isinstance(thumbs, list) and thumbs:
        def area(t):
            return (t.get("width") or 0) * (t.get("height") or 0)
        best = max(thumbs, key=area)
        return best.get("url") or thumbs[-1].get("url")
    return entry.get("thumbnail")


def playlist_entries(info):
    """Список записей плейлиста: [{url, title, duration, thumbnail, uploader}]."""
    pl_uploader = info.get("uploader") or info.get("channel") or ""
    out = []
    for e in (info.get("entries") or []):
        if not e:
            continue
        url = e.get("url") or e.get("webpage_url")
        if (not url or not str(url).startswith("http")) and e.get("id"):
            url = "https://www.youtube.com/watch?v=" + e["id"]
        if not url:
            continue
        out.append({"url": url,
                    "title": e.get("title") or "Unknown",
                    "duration": e.get("duration"),
                    "thumbnail": _best_thumb(e),
                    "uploader": e.get("uploader") or e.get("channel") or pl_uploader})
    return out



# ------------------------------------------------------------------ #
#  Fallback-утилита (streamlink для Twitch) и единый раннер задания
# ------------------------------------------------------------------ #
def is_twitch(url):
    return "twitch.tv" in (url or "").lower()


def _out_dir(settings):
    return settings.get("download_path") or os.path.join(
        os.path.expanduser("~"), "Downloads")


def _new_job_dir():
    """Создаёт временную папку задания (tmp/aaaa-xxxx-bbbb-dddd рядом с конфигом)."""
    h = uuid.uuid4().hex[:16]
    name = "-".join(h[i:i + 4] for i in range(0, 16, 4))
    d = os.path.join(TMP_ROOT, name)
    os.makedirs(d, exist_ok=True)
    return d


def _rm_dir(path):
    try:
        if path and os.path.isdir(path):
            shutil.rmtree(path, ignore_errors=True)
    except Exception:
        pass


# Промежуточные файлы отдельных потоков yt-dlp: «<base>.f248.webm».
_FRAG_RE = re.compile(r"\.f\d+\.", re.IGNORECASE)


def _scan_job_output(job_dir, ext):
    """Ищет итоговый файл в изолированной папке задания. Нужен как запасной
    вариант к parse_destination: если в заголовке есть emoji/символы вне кодовой
    страницы консоли, yt-dlp печатает путь искажённым и он не совпадает с реально
    записанным файлом. Папка задания одноразовая, поэтому итог там — единственный
    «настоящий» файл (обрезки .part/.fNNN. пропускаем)."""
    try:
        names = os.listdir(job_dir)
    except OSError:
        return ""
    cands = []
    for n in names:
        p = os.path.join(job_dir, n)
        if not os.path.isfile(p):
            continue
        low = n.lower()
        if low.endswith((".part", ".ytdl", ".temp")) or _FRAG_RE.search(n):
            continue
        cands.append(p)
    if not cands:
        return ""
    pref = [c for c in cands if c.lower().endswith("." + ext.lower())]
    pool = pref or cands
    return max(pool, key=lambda p: os.path.getsize(p))


def _move_to_dest(src, download_dir):
    """Переносит src в папку загрузок с уникальным (и очищенным от спецсимволов)
    именем; возвращает путь. Чистка здесь ловит и режимы, где имя задавал сам
    yt-dlp (напр., фоновый Paste), а не наш шаблон."""
    os.makedirs(download_dir, exist_ok=True)
    base, ext = os.path.splitext(os.path.basename(src))
    base = _sanitize_name(base)
    final_base = _unique_base(download_dir, base, ext.lstrip(".") or "mp4")
    final = os.path.join(download_dir, final_base + ext)
    shutil.move(src, final)
    return final


def build_streamlink_args(url, settings, out_dir=None):
    out_dir = out_dir or _out_dir(settings)
    out = os.path.join(out_dir, "stream-" + time.strftime("%Y%m%d-%H%M%S") + ".mp4")
    args = [tools.streamlink_path(), "--ffmpeg-ffmpeg", tools.FFMPEG_EXE,
            url, "best", "-o", out, "--force"]
    return args, out


def _iter_lines(stream):
    """Итерирует вывод процесса по '\\n' И '\\r'. ffmpeg обновляет строку прогресса
    через '\\r' (без перевода строки), поэтому обычный итератор по строкам склеил бы
    весь прогресс в одну строку, приходящую только в самом конце."""
    buf = []
    while True:
        ch = stream.read(1)
        if not ch:
            if buf:
                yield "".join(buf)
            return
        if ch in ("\r", "\n"):
            if buf:
                yield "".join(buf)
                buf = []
        else:
            buf.append(ch)


_FF_TIME_RE = re.compile(r"time=(\d+):(\d+):(\d+(?:\.\d+)?)")


def _parse_ffmpeg_time(line):
    """Секунды из строки прогресса ffmpeg ('… time=00:00:03.52 …') или None."""
    m = _FF_TIME_RE.search(line)
    if not m:
        return None
    h, mnt, sec = int(m.group(1)), int(m.group(2)), float(m.group(3))
    return h * 3600 + mnt * 60 + sec


def _section_seconds(option):
    """Длительность выбранной секции (end-start) из option['section']='*a-b' или
    None. Нужна, чтобы вести полосу прогресса по ffmpeg-резке (у неё нет обычного
    download-прогресса yt-dlp)."""
    m = re.match(r"\*(\d+(?:\.\d+)?)-(\d+(?:\.\d+)?)", (option or {}).get("section") or "")
    if m:
        a, b = float(m.group(1)), float(m.group(2))
        if b > a:
            return b - a
    return None


_POST_RE = re.compile(
    r"\[(ExtractAudio|Merger|Metadata|EmbedThumbnail|EmbedSubtitle|"
    r"VideoConvertor|Fixup\w*)\]")


def _stream(args, hooks, log, progress=True, ff_total=None):
    """Запускает процесс со стримингом вывода в лог; возвращает (ok, dest).

    Прогресс отдельных потоков (видео, затем аудио) объединяется в один job:
    вместо двух пробегов 0→100% полоса идёт единожды 0→100% по всем файлам.

    ff_total (сек) — при скачивании по таймкодам yt-dlp режет через ffmpeg, у
    которого нет обычного download-прогресса; тогда ведём полосу по ffmpeg
    'time=' относительно длительности секции."""
    proc = tools.popen(args)
    hooks.set_proc(proc)
    dest = ""
    n_streams = 1        # сколько файлов сольётся в один (видео+аудио = 2)
    file_idx = -1        # индекс текущего скачиваемого файла (0-based)
    for line in _iter_lines(proc.stdout):
        if hooks.is_stopped():
            break
        if progress:
            ns = _stream_count(line)
            if ns:
                n_streams = ns
            pr = parse_progress(line)
            if pr:
                if n_streams > 1 and pr.get("frac") is not None:
                    idx = min(max(file_idx, 0), n_streams - 1)
                    frac = max(0.0, min(1.0, (idx + pr["frac"]) / n_streams))
                    pr["frac"] = frac
                    pr["percent_str"] = f"{frac * 100:.1f}%"
                hooks.on_progress(pr)
                continue
            if ff_total:                     # прогресс ffmpeg-резки по таймкодам
                t = _parse_ffmpeg_time(line)
                if t is not None:
                    frac = max(0.0, min(1.0, t / ff_total))
                    hooks.on_progress({"frac": frac,
                                       "percent_str": f"{frac * 100:.1f}%"})
                    continue
            if _POST_RE.search(line):        # постобработка после 100% (mp3/merge/…)
                hooks.on_progress({"stage": "post"})
                continue
            d = parse_destination(line)
            if d:
                dest = d
                if "[download] Destination: " in line:
                    file_idx += 1       # начался новый файл потока
        log.raw(line.rstrip())
    rc = proc.wait()
    return rc == 0, dest


_YT_ID_RE = re.compile(
    r"(?:v=|/vi/|/live/|/shorts/|/embed/|youtu\.be/)([\w-]{11})")


def _youtube_id(url):
    m = _YT_ID_RE.search(url or "")
    return m.group(1) if m else ""


# Ключи сайтов для автовставки ссылки при открытии окна (см. settings_page).
AUTOPASTE_SITES = ["youtube", "instagram", "tiktok", "reddit",
                   "twitter", "vk", "soundcloud"]
_SITE_HOSTS = [
    ("youtube", ("youtube.com", "youtu.be")),
    ("instagram", ("instagram.com",)),
    ("tiktok", ("tiktok.com",)),
    ("reddit", ("reddit.com", "redd.it")),
    ("twitter", ("twitter.com", "x.com")),
    ("vk", ("vk.com", "vk.ru", "vkvideo.ru")),
    ("soundcloud", ("soundcloud.com",)),
]


def link_site(url):
    """Ключ сайта по ссылке ('youtube'/'tiktok'/…), '' если не распознан."""
    u = (url or "").lower()
    for key, hosts in _SITE_HOSTS:
        if any(h in u for h in hosts):
            return key
    return ""


def _dl_url_to_file(url, path):
    try:
        import urllib.request
        req = urllib.request.Request(url, headers={"User-Agent": "Snatchr"})
        with urllib.request.urlopen(req, timeout=20) as resp, open(path, "wb") as f:
            f.write(resp.read())
        return os.path.getsize(path) > 0
    except Exception:
        return False


def _run_thumbnail_job(url, settings, hooks, title):
    """Скачивание ТОЛЬКО обложки в jpg. Для YouTube основной путь — прямая
    ссылка img.youtube.com/vi/<id>/{maxres,hq}default.jpg (в ~8 раз быстрее
    yt-dlp и в макс. качестве; имя файла берём из уже известного title).
    Fallback и не-YouTube — yt-dlp (--skip-download --write-thumbnail)."""
    log = logbook.Log(url)
    log.event("Downloading thumbnail")
    job_dir = _new_job_dir()
    dest = ""
    if is_youtube(url) and not hooks.is_stopped():
        vid = _youtube_id(url)
        if vid:
            out = os.path.join(job_dir, (_sanitize_name(title) or vid) + ".jpg")
            for q in ("maxresdefault", "hqdefault"):
                u = f"https://img.youtube.com/vi/{vid}/{q}.jpg"
                # >2 КБ отсекает серую заглушку 120×90 при отсутствии maxres.
                if _dl_url_to_file(u, out) and os.path.getsize(out) > 2048:
                    dest = out
                    break
    if not dest and not hooks.is_stopped():
        log.event("Thumbnail via yt-dlp")
        out_tmpl = os.path.join(
            job_dir, _name_template(job_dir, {"thumbnail": True}, title))
        args = [tools.YTDLP_EXE, "--no-playlist", "--ignore-errors", "-o", out_tmpl,
                "--skip-download", "--write-thumbnail", "--convert-thumbnails", "jpg"]
        args += tools.pot_ytdlp_args(url)
        args += cookie_args(settings, url)
        loc = tools.ffmpeg_location()
        if loc:
            args += ["--ffmpeg-location", loc]
        args.append(url)
        _stream(args, hooks, log, progress=False)
        _del_cookie_copy(args)
        dest = _scan_job_output(job_dir, "jpg")
    if hooks.is_stopped():
        _rm_dir(job_dir)
        return False, "", log
    final = ""
    if dest and os.path.exists(dest):
        try:
            final = _move_to_dest(dest, _out_dir(settings))
        except Exception as exc:
            log.info("Move failed: " + str(exc))
    _rm_dir(job_dir)
    if final:
        log.event("Done")
    return bool(final), final, log


def run_job(option, url, settings, hooks, title=None, info=None):
    """
    Полный цикл одного задания: yt-dlp -> (fallback streamlink для Twitch) ->
    конвертация. Возвращает (ok, dest, log). Безопасно прерывается на любом
    этапе (kill процесса + чистка недокачанных кусков).

    info — готовая info с анализа: пробуем быстрый путь (--load-info-json) без
    повторного извлечения; при любой неудаче откатываемся к обычному.
    """
    if (option or {}).get("thumbnail"):
        return _run_thumbnail_job(url, settings, hooks, title)
    log = logbook.Log(url)
    log.event("Starting download (yt-dlp)")
    download_dir = _out_dir(settings)
    # Качаем в отдельную временную папку; готовый файл переносим в папку загрузок.
    # Так все обломки и temp-файлы изолированы и легко удаляются при отмене.
    job_dir = _new_job_dir()
    expected = _expected_path(option, url, settings, title, job_dir)

    def _resolve(parsed):
        if expected and os.path.exists(expected):
            return expected
        if parsed and os.path.exists(parsed):
            return parsed
        # Путь из консоли не совпал с файлом (напр., emoji в заголовке) —
        # ищем итог прямо в одноразовой папке задания.
        return _scan_job_output(job_dir, _merge_ext(option, url, settings))

    # Куки применяем best-effort. Если их извлечение из браузера падает (Chrome
    # App-Bound Encryption / залоченная БД), повторяем БЕЗ кук — публичное видео
    # тогда всё равно скачается (раньше такой сбой ронял вообще любую загрузку).
    use_cookies = True
    ok, dest = False, ""
    ff_total = _section_seconds(option)   # для полосы прогресса при таймкод-резке

    # Быстрый путь: качаем из готовой info (--load-info-json), без повторного
    # извлечения. При любой неудаче — обычное извлечение (полный фолбэк).
    info_file = _write_info_json(info)
    if info_file and not hooks.is_stopped():
        log.event("Fast path: load-info-json (no re-extraction)")
        _args = build_download_args(option, url, settings, title, job_dir,
                                    info_json=info_file)
        ok, dest = _stream(_args, hooks, log, progress=True, ff_total=ff_total)
        _del_cookie_copy(_args)          # одноразовую копию кук — сразу убрать
        if not hooks.is_stopped():
            dest = _resolve(dest)
            ok = bool(dest)
        try:
            os.remove(info_file)
        except OSError:
            pass
        if not ok and not hooks.is_stopped():
            log.event("load-info-json failed — full extraction fallback")
            _rm_dir(job_dir)
            job_dir = _new_job_dir()
            expected = _expected_path(option, url, settings, title, job_dir)

    if not ok and not hooks.is_stopped():
        _args = build_download_args(option, url, settings, title, job_dir)
        ok, dest = _stream(_args, hooks, log, progress=True, ff_total=ff_total)
        _del_cookie_copy(_args)
        # С --ignore-errors код возврата ненадёжен; успех = итоговый файл существует.
        if not hooks.is_stopped():
            dest = _resolve(dest)
            ok = bool(dest)
    if (not ok and not hooks.is_stopped() and cookie_args(settings, url)
            and is_cookie_error(log.text())):
        log.event("Cookie extraction failed — retrying without cookies")
        use_cookies = False
        _rm_dir(job_dir)
        job_dir = _new_job_dir()
        expected = _expected_path(option, url, settings, title, job_dir)
        ok, dest = _stream(
            build_download_args(option, url, settings, title, job_dir, cookies=False),
            hooks, log, progress=True, ff_total=ff_total)
        if not hooks.is_stopped():
            dest = _resolve(dest)
            ok = bool(dest)

    # HTTP 403 (часто — проверка TLS-отпечатка сервером): повторяем с
    # impersonation — yt-dlp притворяется настоящим браузером (curl_cffi).
    if (not ok and not hooks.is_stopped()
            and "http error 403" in log.text().lower()):
        log.event("HTTP 403 — retrying with browser impersonation")
        _rm_dir(job_dir)
        job_dir = _new_job_dir()
        expected = _expected_path(option, url, settings, title, job_dir)
        _args = build_download_args(option, url, settings, title, job_dir,
                                    cookies=use_cookies, impersonate=True)
        ok, dest = _stream(_args, hooks, log, progress=True, ff_total=ff_total)
        _del_cookie_copy(_args)
        if not hooks.is_stopped():
            dest = _resolve(dest)
            ok = bool(dest)

    if not ok and not hooks.is_stopped():
        log.event("yt-dlp failed")
        if is_twitch(url) and tools.have_streamlink():
            hooks.on_status(tr("Trying streamlink…"))
            log.event("Trying streamlink")
            args, out = build_streamlink_args(url, settings, job_dir)
            ok, _ = _stream(args, hooks, log, progress=False)
            dest = out if (ok and os.path.exists(out)) else ""

    conv_failed = False
    if ok and dest and not hooks.is_stopped() and should_convert(option, url, settings):
        try:
            hooks.on_status(tr("Converting…"))
            log.event("Converting to editor-friendly mp4")
            from core import convert
            # Галочка включена -> конвертируем всегда, независимо от кодека.
            dest = convert.convert(dest, hooks=hooks, force=True)
        except Exception as exc:
            conv_failed = True
            log.event("Conversion failed")
            log.info("Conversion failed: " + str(exc))

    # Отмена задания или конвертации -> удаляем всю временную папку.
    if hooks.is_stopped():
        _rm_dir(job_dir)
        return False, "", log

    final = ""
    if ok and dest and os.path.exists(dest):
        try:
            final = _move_to_dest(dest, download_dir)   # переносим готовый файл
        except Exception as exc:
            log.info("Move failed: " + str(exc))
            ok = False
    else:
        ok = False
    _rm_dir(job_dir)   # временная папка больше не нужна

    # Скачали, но конвертация упала: файл оставляем (перенесён), но это НЕ успех.
    if conv_failed:
        return False, final, log
    if ok:
        log.event("Done")
    return ok, final, log
