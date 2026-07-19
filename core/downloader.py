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
from core import formats
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


# Хост ссылки -> домен, под которым лежат куки сервиса.
_COOKIE_DOMAINS = {
    "youtube.com": "youtube.com", "youtu.be": "youtube.com",
    "vk.com": "vk.com", "vkvideo.ru": "vk.com",
    "instagram.com": "instagram.com", "tiktok.com": "tiktok.com",
    "twitter.com": "twitter.com", "x.com": "x.com",
    "reddit.com": "reddit.com", "redd.it": "reddit.com",
    "twitch.tv": "twitch.tv", "vimeo.com": "vimeo.com",
    "soundcloud.com": "soundcloud.com", "facebook.com": "facebook.com",
    "fb.watch": "facebook.com", "ok.ru": "ok.ru", "rutube.ru": "rutube.ru",
    "pornhub.com": "pornhub.com",
}


def _cookie_domain(url):
    """Домен кук для ссылки ('www.youtube.com/...' -> 'youtube.com') или ''."""
    try:
        from urllib.parse import urlparse
        host = (urlparse(url or "").netloc or "").split("@")[-1].split(":")[0].lower()
    except Exception:
        return ""
    if host.startswith("www."):
        host = host[4:]
    for h, dom in _COOKIE_DOMAINS.items():
        if host == h or host.endswith("." + h):
            return dom
    return ""


def fast_cookie_args(settings, url):
    """Куки ТОЛЬКО нужного сервиса, выгруженные в одноразовый Netscape-файл.

    Зачем: `--cookies-from-browser` заставляет yt-dlp прочитать и расшифровать
    ВСЮ куки-базу браузера — это ~3 секунды на каждый вызов (а вызовов на одну
    ссылку бывает несколько). Читаем сами только домен сервиса (~0.03 с) и
    отдаём готовым файлом. Возвращает [] — тогда вызывающий берёт обычный путь.

    Файл одноразовый: имя начинается с 'use_', и _del_cookie_copy стирает его
    сразу после запуска процесса (куки не остаются лежать на диске)."""
    dom = _cookie_domain(url)
    if not dom:
        return []
    browser = (settings or {}).get("cookies_browser") or "auto"
    if browser == "auto":
        browser = tools.default_browser()
    if not browser:
        return []
    try:
        import browser_cookie3
        reader = getattr(browser_cookie3, browser, None)
        if reader is None:
            return []
        jar = list(reader(domain_name=dom))
        if not jar:
            return []                    # для этого сервиса кук нет — не мешаем
        import tempfile
        fd, path = tempfile.mkstemp(prefix="use_", suffix=".txt")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write("# Netscape HTTP Cookie File\n")
            for c in jar:
                f.write("%s\t%s\t%s\t%s\t%d\t%s\t%s\n" % (
                    c.domain, "TRUE" if c.domain.startswith(".") else "FALSE",
                    c.path or "/", "TRUE" if c.secure else "FALSE",
                    int(c.expires or 0), c.name, c.value))
        return ["--cookies", path]
    except Exception:
        return []                        # любая осечка -> обычный путь


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


def have_cookies(settings, url=""):
    """Есть ли вообще что подставить в куки — БЕЗ побочных эффектов.

    Отдельно от cookie_args, потому что тот выгружает куки во временный файл:
    вызов «просто чтобы проверить» плодил бы файлы, которые никто не удалит."""
    return bool(file_cookie_args(settings)) or bool(browser_cookie_args(settings))


def cookie_args(settings, url=""):
    """Куки, которые применяем ВСЕГДА. Порядок: свой файл из настроек ->
    быстрый путь (куки только этого сервиса, см. fast_cookie_args) -> чтение
    всей базы браузера силами yt-dlp.

    Куки всегда свежие: кэширования между запусками нет (надёжнее для
    сессионной авторизации), быстрый путь лишь сужает выборку до одного домена."""
    return (file_cookie_args(settings)
            or fast_cookie_args(settings, url)
            or browser_cookie_args(settings))


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
    if height >= 2880:
        return "5K"
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


def video_formats(info, youtube=True, settings=None):
    """Best Quality + список разрешений по убыванию (см. спецификацию).

    YouTube: Best Quality = максимальное разрешение в VP9, плюс отдельная строка
    «Best Compatibility (1080p)» (максимальное доступное разрешение с кодеком AVC).
    Прочие сайты: Best Quality = максимальное разрешение (любой кодек); строки
    разрешений показываем, только если они реально доступны. AV1 не показываем.

    settings — если передан, к готовому списку применяются видимость и порядок
    со страницы Format Priority.
    """
    if youtube:
        options = [
            {"label": tr("Best Quality"), "fmt": BEST_VIDEO_FMT, "mp3": False,
             "key": "best"},
            {"label": tr("Best Compatibility (1080p)"),
             "fmt": AVC_VIDEO_FMT, "mp3": False, "key": "compat"},
        ]
    else:
        options = [{"label": tr("Best Quality"), "fmt": BEST_VIDEO_FMT,
                    "mp3": False, "key": "best"}]

    vids = []
    for f in info.get("formats", []):
        if f.get("vcodec") in (None, "none"):
            continue
        # AV1 полностью исключаем из селектора.
        codec_lbl = _codec_label(f.get("vcodec"))
        if codec_lbl == "AV1":
            continue
        # VP9 нужен только там, где H.264 нет (1440p/4K). На 1080p и ниже H.264
        # доступен всегда и не требует конвертации — VP9 там только мусорит выбор.
        if codec_lbl == "VP9" and (f.get("height") or 0) <= 1080:
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
            "key": formats.res_key(h, codec),
        })
    options.append({"label": tr("Thumbnail"), "thumbnail": True, "mp3": False,
                    "key": "thumbnail"})
    if settings is not None:
        options = formats.apply(options, settings)
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
        f"%(progress._downloaded_bytes_str)s|"
        # Сырые байты — по ним взвешиваем потоки (видео+аудио) в общей полосе.
        f"%(progress.downloaded_bytes)s|%(progress.total_bytes)s|"
        f"%(progress.total_bytes_estimate)s",
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


TRIM_TMP_TTL = 24 * 3600        # сколько живёт фрагмент, скопированный в буфер


def cleanup_temp():
    """Стартовая уборка мусора (single-instance гарантирует, что чужих активных
    файлов нет): недокачанные папки заданий APP_DIR/tmp/*, осиротевшие
    snatchr_*.info.json и старые snatchr_trim_* в системном %TEMP%. Чистим
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
        tmp = tempfile.gettempdir()
        for p in glob.glob(os.path.join(tmp, "snatchr_*.info.json")):
            try:
                os.remove(p)
            except OSError:
                pass
        # Фрагменты «скопировать в буфер»: в буфере лежит лишь ССЫЛКА на файл,
        # поэтому сразу удалять нельзя (сломается вставка). Убираем только старые
        # — свежие мог скопировать пользователь и ещё не вставить.
        now = time.time()
        for p in glob.glob(os.path.join(tmp, "snatchr_trim_*")):
            try:
                if now - os.path.getmtime(p) > TRIM_TMP_TTL:
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


def _num(parts, i):
    """Сырое число из поля progress-template ('NA'/пусто -> None)."""
    if len(parts) <= i:
        return None
    try:
        return float(parts[i].strip())
    except (ValueError, TypeError):
        return None


def parse_progress(line):
    """Строка прогресса -> {percent_str, speed, eta, frac, size, …} либо None."""
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
    dl_bytes = _num(parts, 6)
    tot_bytes = _num(parts, 7) or _num(parts, 8)    # точный размер или оценка
    return {"percent_str": pct, "speed": speed, "eta": eta, "frac": frac,
            "size": size, "downloaded": downloaded,
            "dl_bytes": dl_bytes, "tot_bytes": tot_bytes}


# Доля, которую резервируем под ещё не начатые потоки, пока их размер неизвестен.
# У YouTube это аудио-дорожка к видео: обычно 2-5% от объёма видео.
_TAIL_RESERVE = 0.04


def _merged_frac(pr, done_bytes, cur_total, file_idx, n_streams):
    """Общий прогресс job'а, взвешенный по БАЙТАМ всех потоков (видео+аудио).

    Делить полосу поровну между потоками нельзя: видео весит гигабайты, аудио —
    десятки мегабайт, и полоса замирала на видео, а потом прыгала на аудио.
    Пока последний поток не начался, его размер неизвестен — резервируем под
    хвост _TAIL_RESERVE, иначе полоса дошла бы до 100% и откатилась."""
    dl = pr.get("dl_bytes")
    if dl is None or cur_total <= 0:
        # Байт нет (редкий формат/оценка недоступна) — равномерно по потокам.
        idx = min(max(file_idx, 0), n_streams - 1)
        return max(0.0, min(1.0, (idx + (pr.get("frac") or 0.0)) / n_streams))
    total = done_bytes + cur_total
    if file_idx < n_streams - 1:        # впереди ещё потоки — оставляем им место
        total *= (1.0 + _TAIL_RESERVE)
    if total <= 0:
        return 0.0
    return max(0.0, min(1.0, (done_bytes + dl) / total))


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
    """Похоже ли, что ошибку можно обойти куками (бот-чек / вход / 403).

    Паттерны намеренно узкие: широкое «confirm you» ловило стандартный хвост
    yt-dlp «Confirm you are on the latest version», из-за чего любая ошибка
    выглядела как проблема доступа (и повтор без кук не запускался)."""
    low = (text or "").lower()
    return any(k in low for k in (
        # бот-чек / прямой запрет
        "not a bot", "http error 403",
        # требование входа (формулировки сильно разнятся по сайтам)
        "sign in", "login required", "log in", "logged in", "sign up",
        "requires authentication", "authorization required",
        # приватность / ограниченный доступ
        "private video", "is private", "protected", "members-only",
        "subscribers", "premium",
        # возрастные ограничения
        "confirm your age", "age-restricted", "age restricted",
        # антифлуд: куки часто снимают лимит, без них повтор бесполезен
        "rate-limit", "rate limit",
    ))


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
    # Соцсети (yt-dlp и/или Ember).
    "twitter.com", "x.com", "facebook.com", "fb.watch", "soundcloud.com",
    # Русскоязычные сервисы (yt-dlp их поддерживает).
    "vk.com", "vkvideo.ru", "ok.ru", "rutube.ru",
)


def is_supported_url(url):
    """http(s)-ссылка на один из известных сайтов (для тоста из буфера обмена).

    Кроме явного списка учитываем всё, что умеет Ember (Bluesky, Pinterest,
    Newgrounds, Tumblr …) — иначе Spotlight отклонял бы ссылку ещё до попытки
    скачать, хотя движок с ней справляется."""
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
    if any(host == h or host.endswith("." + h) for h in _SUPPORTED_HOSTS):
        return True
    from core import ember_dl
    return ember_dl.can_handle(u)


def is_playlist_url(url):
    u = (url or "").lower()
    if ("list=" in u) or ("/playlist" in u):
        return True
    # Наборы, которые умеет только Ember: сет SoundCloud, лента автора
    # (профиль/канал) в Twitter/VK/Instagram и т.п.
    from core import ember_dl
    return ember_dl.is_collection(u)


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


def overall_progress(p, convert):
    """Общий прогресс строки -> (frac, percent_str | None).

    С включённой конвертацией полоса единая: скачивание занимает 0..50%,
    конвертация — 50..100%. Процент в тексте пересчитываем под ту же шкалу,
    иначе он спорит с полосой (скачивание показывало свои 46.6% на 23% полосы,
    а конвертация начинала счёт заново с 0%). None -> текст не подменяем."""
    stage = p.get("stage")
    if stage == "post":
        return (0.5 if convert else 1.0), None
    if stage == "convert":
        frac = 0.5 + 0.5 * (p.get("frac") or 0.0)
        # GPU-энкодер отвалился -> кодируем процессором (втрое медленнее и с
        # нуля). Подписываем явно, иначе выглядит как необъяснимый тормоз.
        label = tr("Converting…") + (" (CPU)" if p.get("cpu") else "")
        return frac, "%s %d%%" % (label, round(frac * 100))
    base = p.get("frac") or 0.0
    if convert:
        return base * 0.5, "%.1f%%" % (base * 50.0)
    return base, None


def _picks_vp9(option):
    """Приведёт ли выбранный формат к VP9 (по ключу строки селектора).

    Конвертация трогает только VP9 (см. convert.decide_target), поэтому для
    H.264-строк её планировать нельзя: иначе файл зря склеивался бы в MKV, а
    полоса прогресса резервировала бы половину под конвертацию, которой нет."""
    key = (option or {}).get("key") or ""
    if key == "compat":
        return False                     # Best Compatibility — заведомо AVC
    if key == "best":
        return True                      # лучший не-AV1 на YouTube — обычно VP9
    if "_" in key:
        return key.split("_", 1)[1] == "VP9"
    return True                          # ключ неизвестен — прежнее поведение


def should_convert(option, url, settings):
    """Нужна ли конвертация: галочка вкл, видео (не mp3/аудио/обложка),
    источник — YouTube, и выбранный формат действительно VP9."""
    o = option or {}
    return (bool(settings.get("convert_yt"))
            and is_youtube(url)
            and not o.get("mp3")
            and not o.get("audio")
            and not o.get("thumbnail")
            and _picks_vp9(o))


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
    if (info or {}).get("_ember"):
        return list(info.get("entries") or [])   # Ember отдаёт уже в этом формате
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


def _rm(path):
    try:
        if path and os.path.isfile(path):
            os.remove(path)
    except OSError:
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
_FF_SPEED_RE = re.compile(r"speed=\s*([\d.]+)x")
_FF_SIZE_RE = re.compile(r"(?:^|\s)L?size=\s*(\d+)(KiB|MiB|kB|kb|B)")


def _ff_size_mib(line):
    """Размер выходного файла из строки ffmpeg ('size=3328KiB') в 'N.NMiB'/''."""
    m = _FF_SIZE_RE.search(line)
    if not m:
        return ""
    val, unit = float(m.group(1)), m.group(2)
    mib = {"KiB": val / 1024, "MiB": val, "kB": val / 1024,
           "kb": val / 1024, "B": val / (1024 * 1024)}.get(unit, 0.0)
    return "%.1fMiB" % mib if mib >= 0.05 else ""


def _parse_ffmpeg_time(line):
    """Секунды из строки прогресса ffmpeg ('… time=00:00:03.52 …') или None."""
    m = _FF_TIME_RE.search(line)
    if not m:
        return None
    h, mnt, sec = int(m.group(1)), int(m.group(2)), float(m.group(3))
    return h * 3600 + mnt * 60 + sec


def _fmt_eta(secs):
    """Секунды -> 'M:SS' или 'H:MM:SS' (для ETA ffmpeg-резки по таймкодам)."""
    secs = max(0, int(secs))
    h, rem = divmod(secs, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _section_bounds(option):
    """(start, end) секции из option['section']='*a-b' (сек) или None."""
    m = re.match(r"\*(\d+(?:\.\d+)?)-(\d+(?:\.\d+)?)", (option or {}).get("section") or "")
    if m:
        a, b = float(m.group(1)), float(m.group(2))
        if b > a:
            return a, b
    return None


def _section_seconds(option):
    """Длительность выбранной секции (end-start) или None."""
    b = _section_bounds(option)
    return (b[1] - b[0]) if b else None


# Сетевой сбой ffmpeg-стриминга секции: провайдер рвёт прямое соединение ffmpeg
# к googlevideo (DPI-блокировка по TLS). yt-dlp своим клиентом её проходит,
# поэтому фолбэк — скачать формат целиком нативно и вырезать секцию локально.
_SECTION_NET_ERR = ("error opening input", "failed to read handshake",
                    "-10054", "10054", "connection reset",
                    "ffmpeg exited with code")


def _is_section_stream_error(text):
    t = (text or "").lower()
    return any(k in t for k in _SECTION_NET_ERR)


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
    done_bytes = 0.0     # суммарный размер уже скачанных потоков
    cur_total = 0.0      # размер текущего потока
    for line in _iter_lines(proc.stdout):
        if hooks.is_stopped():
            break
        if progress:
            ns = _stream_count(line)
            if ns:
                n_streams = ns
            pr = parse_progress(line)
            if pr:
                if pr.get("tot_bytes"):
                    cur_total = pr["tot_bytes"]
                if n_streams > 1 and pr.get("frac") is not None:
                    frac = _merged_frac(pr, done_bytes, cur_total,
                                        file_idx, n_streams)
                    pr["frac"] = frac
                    pr["percent_str"] = f"{frac * 100:.1f}%"
                hooks.on_progress(pr)
                continue
            if ff_total:                     # прогресс ffmpeg-резки по таймкодам
                t = _parse_ffmpeg_time(line)
                if t is not None:
                    frac = max(0.0, min(1.0, t / ff_total))
                    # У ffmpeg-пути нет download-скорости/размера, но есть
                    # speed=Nx (реалтайм-множитель) — из него скорость-пилюля и
                    # ETA по оставшейся длительности секции.
                    info = {"frac": frac, "percent_str": f"{frac * 100:.1f}%"}
                    sm = _FF_SPEED_RE.search(line)
                    if sm:
                        spd = float(sm.group(1))
                        info["speed"] = f"{spd:.2f}x"
                        if spd > 0:
                            info["eta"] = _fmt_eta((ff_total - t) / spd)
                    sz = _ff_size_mib(line)
                    if sz:
                        info["size"] = sz    # размер вырезанного фрагмента (растёт)
                    hooks.on_progress(info)
                    continue
            if _POST_RE.search(line):        # постобработка после 100% (mp3/merge/…)
                hooks.on_progress({"stage": "post"})
                continue
            d = parse_destination(line)
            if d:
                dest = d
                if "[download] Destination: " in line:
                    if file_idx >= 0:
                        done_bytes += cur_total   # прошлый поток докачан целиком
                    file_idx += 1       # начался новый файл потока
                    cur_total = 0.0
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


def _try_ember(url, option, settings, hooks, log, job_dir, title):
    """Скачивание через Ember. Возвращает (ok, dest).

    Используется двояко: как ОСНОВНОЙ путь для Twitter/X и как ЗАПАСНОЙ для
    прочих поддерживаемых сервисов, когда yt-dlp исчерпал свои повторы."""
    from core import ember_dl
    if not ember_dl.can_handle(url) or hooks.is_stopped():
        return False, ""
    try:
        log.event("Trying Ember")
        result = ember_dl.extract(url, settings)
        dest = ember_dl.download(result, job_dir, option=option,
                                 hooks=hooks, title=title)
        if dest and os.path.isfile(dest):
            log.info("Ember: downloaded %s" % os.path.basename(dest))
            return True, dest
        log.info("Ember: nothing downloaded")
    except Exception as exc:
        log.info("Ember failed: %s" % str(exc)[:200])
    return False, ""


def run_job(option, url, settings, hooks, title=None, info=None):
    """
    Полный цикл одного задания: yt-dlp -> (fallback streamlink для Twitch) ->
    конвертация. Возвращает (ok, dest, log). Безопасно прерывается на любом
    этапе (kill процесса + чистка недокачанных кусков).

    info — готовая info с анализа: пробуем быстрый путь (--load-info-json) без
    повторного извлечения; при любой неудаче откатываемся к обычному.
    """
    if (option or {}).get("thumbnail") and not (option or {}).get("ember"):
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
    ember_used = False

    # Twitter/X — Ember ОСНОВНОЙ движок (yt-dlp там регулярно не справляется).
    from core import ember_dl
    if ember_dl.is_primary(url) and not hooks.is_stopped():
        ok, dest = _try_ember(url, option, settings, hooks, log, job_dir, title)
        ember_used = ok
        if not ok:
            log.event("Ember failed — falling back to yt-dlp")
    ff_total = _section_seconds(option)   # для полосы прогресса при таймкод-резке

    # Быстрый путь: качаем из готовой info (--load-info-json), без повторного
    # извлечения. При любой неудаче — обычное извлечение (полный фолбэк).
    info_file = _write_info_json(info) if not ok else None
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
    # Повтор без кук. Два случая: (1) куки не извлеклись (залоченная БД/DPAPI);
    # (2) куки извлеклись, но САМ САЙТ с ними отдаёт ответ, который экстрактор не
    # разбирает (VK с авторизацией: «Failed to parse JSON»). Не повторяем, только
    # если ошибка явно про доступ (приватное/вход/403) — там куки как раз нужны.
    if (not ok and not hooks.is_stopped() and have_cookies(settings, url)
            and (is_cookie_error(log.text()) or not is_auth_error(log.text()))):
        log.event("Retrying without cookies")
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

    # Секция упала на сетевом сбое ffmpeg-стриминга (провайдер рвёт прямое
    # соединение ffmpeg к googlevideo). Фолбэк: качаем формат ЦЕЛИКОМ нативным
    # клиентом yt-dlp (он блокировку проходит) и точно вырезаем секцию локально.
    sect = _section_bounds(option)
    section_fallback = False
    if (not ok and sect and not hooks.is_stopped()
            and _is_section_stream_error(log.text())):
        log.event("Section stream blocked — full download + local precise cut")
        _rm_dir(job_dir)
        job_dir = _new_job_dir()
        opt_full = {k: v for k, v in option.items() if k != "section"}
        expected = _expected_path(opt_full, url, settings, title, job_dir)
        _args = build_download_args(opt_full, url, settings, title, job_dir,
                                    cookies=use_cookies, impersonate=True)
        ok, dest = _stream(_args, hooks, log, progress=True)
        _del_cookie_copy(_args)
        if not hooks.is_stopped():
            if expected and os.path.exists(expected):
                dest = expected
            elif not (dest and os.path.exists(dest)):
                dest = _scan_job_output(job_dir, _merge_ext(opt_full, url, settings))
            ok = bool(dest)
        if ok and dest and not hooks.is_stopped():
            # Точная резка = перекодирование, совмещённое с конвертацией в один
            # проход (не два). Помечаем, чтобы обычный convert-блок не повторял.
            section_fallback = True
            try:
                hooks.on_status(tr("Trimming…"))
                log.event("Precise cut + convert (%.1f-%.1f)" % sect)
                from core import convert
                dest = convert.convert(dest, hooks=hooks, log=log, section=sect)
            except Exception as exc:
                log.event("Section cut failed")
                log.info("Section cut failed: " + str(exc))
                ok = False

    # yt-dlp исчерпал повторы — пробуем Ember как запасной движок (для сервисов,
    # которые он поддерживает). Для Twitter он уже отработал выше как основной.
    if (not ok and not hooks.is_stopped() and not ember_used
            and not ember_dl.is_primary(url)):
        ok, dest = _try_ember(url, option, settings, hooks, log, job_dir, title)
        ember_used = ok

    if not ok and not hooks.is_stopped():
        log.event("yt-dlp failed")
        if is_twitch(url) and tools.have_streamlink():
            hooks.on_status(tr("Trying streamlink…"))
            log.event("Trying streamlink")
            args, out = build_streamlink_args(url, settings, job_dir)
            ok, _ = _stream(args, hooks, log, progress=False)
            dest = out if (ok and os.path.exists(out)) else ""

    conv_failed = False
    # Ember отдаёт готовый H.264/AAC — перекодировать нечего, конвертацию пропускаем.
    if (ok and dest and not hooks.is_stopped() and not section_fallback
            and not ember_used and should_convert(option, url, settings)):
        try:
            hooks.on_status(tr("Converting…"))
            log.event("Converting to editor-friendly mp4")
            from core import convert
            # force=False: решение по РЕАЛЬНОМУ кодеку файла. VP9 -> перекодируем;
            # если вместо него приехал H.264 (VP9 у ролика не оказалось) — просто
            # ремуксим MKV в MP4, без бессмысленного H.264 -> H.264.
            dest = convert.convert(dest, hooks=hooks, log=log)
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
