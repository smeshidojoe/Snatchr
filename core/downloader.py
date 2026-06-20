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
import json
import time

from core import tools
from core import logbook
from core.i18n import tr

SEP = " · "          # разделитель блоков в подписи
MIN_HEIGHT = 480     # ниже 480p не показываем

# Best Quality: максимальное разрешение, предпочитая VP9, но НИКОГДА не AV1
# (av1 декодируется тяжело и часто не нужен). Если VP9 нет — лучший не-AV1 кодек.
BEST_VIDEO_FMT = "bv*[vcodec~='vp0?9']+ba/bv*[vcodec!^=av01]+ba/b"
# Совместимость: максимальное разрешение с кодеком AVC (обычно до 1080p).
AVC_VIDEO_FMT = "bv*[vcodec^=avc]+ba/b"
PROGRESS_TAG = "@@SN@@"    # префикс строки прогресса
DEST_TAG = "@@DEST@@"      # префикс строки с итоговым путём файла


# ------------------------------------------------------------------ #
def probe(url, no_playlist=True, timeout=60):
    """Информация о ссылке (dict). Бросает RuntimeError при ошибке."""
    args = [tools.YTDLP_EXE, "-J", "--no-warnings"]
    if no_playlist:
        args.append("--no-playlist")
    args.append(url)
    r = tools.run(args, timeout=timeout)
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
def _sanitize_name(name):
    for ch in '<>:"/\\|?*':
        name = name.replace(ch, "_")
    return name.strip().rstrip(".") or "video"


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


def _expected_path(option, url, settings, title):
    """Детерминированный путь файла сразу после загрузки/склейки (мы задаём -o).
    None — если заголовок неизвестен (тогда полагаемся на распарсенный путь)."""
    if not title:
        return None
    out_dir = settings.get("download_path") or os.path.join(
        os.path.expanduser("~"), "Downloads")
    base = _unique_base(out_dir, _sanitize_name(title), _final_ext(option))
    return os.path.join(out_dir, base + "." + _merge_ext(option, url, settings))


def build_download_args(option, url, settings, title=None):
    """Аргументы yt-dlp для скачивания одиночной ссылки по выбранному варианту."""
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
        f"%(progress._total_bytes_str)s|%(progress._total_bytes_estimate_str)s",
    ]
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

    # Пост-процессинг из настроек.
    if settings.get("embed_thumbnail"):
        args.append("--embed-thumbnail")
    if settings.get("embed_metadata"):
        args.append("--embed-metadata")

    args.append(url)
    return args


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
    frac = None
    try:
        frac = max(0.0, min(1.0, float(pct.replace("%", "").strip()) / 100.0))
    except ValueError:
        pass
    return {"percent_str": pct, "speed": speed, "eta": eta, "frac": frac, "size": size}


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
    ("conversion failed", "Downloaded, but conversion failed (file kept unconverted)."),
    ("http error 401", "Access requires sign-in (401)."),
    ("http error 403", "Access denied (403) — the video may be region-locked or need sign-in."),
    ("http error 404", "Not found (404) — the link may be broken or removed."),
    ("http error 429", "Too many requests (429) — try again a bit later."),
    ("private video", "This video is private."),
    ("sign in to confirm your age", "Age-restricted — sign-in is required."),
    ("confirm you’re not a bot", "Blocked by anti-bot check — try again later."),
    ("members-only", "Members-only content."),
    ("join this channel", "Members-only content."),
    ("video unavailable", "This video is unavailable."),
    ("this video is not available", "This video is unavailable."),
    ("not available in your country", "Not available in your region."),
    ("geo restricted", "Not available in your region."),
    ("unable to extract", "Couldn’t read this link — the site may have changed."),
    ("unsupported url", "This link isn’t supported."),
    ("ffmpeg", "Processing failed (ffmpeg)."),
    ("no space left", "Not enough disk space."),
]


def friendly_error(text):
    """Человекочитаемое объяснение ошибки по выводу утилит."""
    low = (text or "").lower()
    for needle, msg in _ERROR_MAP:
        if needle in low:
            return msg
    return "Download failed — see the log on your Desktop."


# ------------------------------------------------------------------ #
def is_youtube(url):
    u = (url or "").lower()
    return "youtube.com" in u or "youtu.be" in u


def is_playlist_url(url):
    u = (url or "").lower()
    return ("list=" in u) or ("/playlist" in u)


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


def build_streamlink_args(url, settings):
    out = os.path.join(_out_dir(settings), "stream-" + time.strftime("%Y%m%d-%H%M%S") + ".mp4")
    args = [tools.streamlink_path(), "--ffmpeg-ffmpeg", tools.FFMPEG_EXE,
            url, "best", "-o", out, "--force"]
    return args, out


def _stream(args, hooks, log, progress=True):
    """Запускает процесс со стримингом вывода в лог; возвращает (ok, dest)."""
    proc = tools.popen(args)
    hooks.set_proc(proc)
    dest = ""
    for line in proc.stdout:
        if hooks.is_stopped():
            break
        if progress:
            pr = parse_progress(line)
            if pr:
                hooks.on_progress(pr)
                continue
            d = parse_destination(line)
            if d:
                dest = d
        log.raw(line.rstrip())
    rc = proc.wait()
    return rc == 0, dest


def _is_fragment(rest):
    """rest вида '.f247.webm' -> True (промежуточный формат-фрагмент yt-dlp)."""
    if not rest.startswith(".f"):
        return False
    mid = rest[2:].split(".", 1)[0]
    return mid.isdigit()


def cleanup_job_artifacts(dest):
    """Удаляет обломки неудачной загрузки, привязанные к имени dest:
    фрагменты (.fNNN.*), .temp(.mp4), .part/.ytdl и файл обложки."""
    if not dest:
        return
    out_dir = os.path.dirname(dest)
    base = os.path.splitext(os.path.basename(dest))[0]
    if not out_dir or not base:
        return
    try:
        for f in os.listdir(out_dir):
            if not f.startswith(base):
                continue
            rest = f[len(base):]
            low = f.lower()
            ext = os.path.splitext(f)[1].lower()
            if (low.endswith((".part", ".ytdl", ".temp", ".temp.mp4")) or ".part-frag" in low
                    or _is_fragment(rest)
                    or ext in (".jpg", ".jpeg", ".png", ".webp")):
                try:
                    os.remove(os.path.join(out_dir, f))
                except OSError:
                    pass
    except OSError:
        pass


def run_job(option, url, settings, hooks, title=None):
    """
    Полный цикл одного задания: yt-dlp -> (fallback streamlink для Twitch) ->
    конвертация. Возвращает (ok, dest, log). Безопасно прерывается на любом
    этапе (kill процесса + чистка недокачанных кусков).
    """
    log = logbook.Log(url)
    log.event("Starting download (yt-dlp)")
    # Ожидаемый путь файла известен заранее (мы задаём -o). Проверять успех по
    # нему надёжнее, чем по распарсенному из stdout пути (кириллица там может
    # прийти битой при неверной кодировке).
    expected = _expected_path(option, url, settings, title)

    def _resolve(parsed):
        if expected and os.path.exists(expected):
            return expected
        if parsed and os.path.exists(parsed):
            return parsed
        return ""

    ok, dest = _stream(build_download_args(option, url, settings, title),
                       hooks, log, progress=True)
    # С --ignore-errors код возврата ненадёжен; успех = итоговый файл существует.
    if not hooks.is_stopped():
        dest = _resolve(dest)
        ok = bool(dest)

    if not ok and not hooks.is_stopped():
        log.event("yt-dlp failed")
        if is_twitch(url) and tools.have_streamlink():
            hooks.on_status(tr("Trying streamlink…"))
            log.event("Trying streamlink")
            args, out = build_streamlink_args(url, settings)
            ok, _ = _stream(args, hooks, log, progress=False)
            dest = out if ok else ""

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

    # При отмене чистим только обломки ТЕКУЩЕГО задания (по имени), а не всю папку.
    if hooks.is_stopped():
        cleanup_job_artifacts(expected or dest)
        return False, "", log
    # Скачали, но конвертация упала: файл оставляем (он валиден), но это НЕ успех.
    if conv_failed:
        return False, dest, log
    if ok:
        log.event("Done")
    else:
        cleanup_job_artifacts(expected or dest)   # не оставляем обломки неудачной попытки
    return ok, dest, log
