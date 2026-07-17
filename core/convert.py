"""
Конвертация скачанного видео в формат, удобный для монтажа.

Логика (адаптивно):
  * SDR не-H.264 (VP9/AV1)        -> H.264 8-bit mp4
  * HDR                            -> HEVC 10-bit mp4 (с сохранением HDR-метаданных)
  * уже H.264 SDR / HEVC10 HDR     -> пропускаем (и так монтажно)

Кодирование на GPU (NVENC/QSV/AMF) с fallback на CPU (libx264/libx265):
пробуем энкодеры по очереди (рабочий GPU -> следующий GPU -> CPU).
"""

import os
import json

from core import tools
from core.i18n import tr

_ENCODERS = None

_GPU_HINTS = ("nvenc", "qsv", "amf")


def available_encoders():
    """Множество доступных в ffmpeg энкодеров (кэшируется)."""
    global _ENCODERS
    if _ENCODERS is not None:
        return _ENCODERS
    enc = set()
    try:
        r = tools.run([tools.FFMPEG_EXE, "-hide_banner", "-encoders"], timeout=25)
        text = r.stdout or ""
        for name in ("h264_nvenc", "hevc_nvenc", "h264_qsv", "hevc_qsv",
                     "h264_amf", "hevc_amf", "libx264", "libx265"):
            if name in text:
                enc.add(name)
    except Exception:
        pass
    _ENCODERS = enc
    return enc


def _src_bitrate(v, fmt):
    """Битрейт ВИДЕОпотока (бит/с) или None. У VP9/webm у потока bit_rate обычно
    пустой, поэтому запасные пути: format.bit_rate минус звук, затем size/duration."""
    try:
        br = int(v.get("bit_rate") or 0)
        if br > 0:
            return br
    except (TypeError, ValueError):
        pass
    try:
        br = int(fmt.get("bit_rate") or 0)
        if br > 0:
            return int(br * 0.93)        # вычитаем примерную долю звука
    except (TypeError, ValueError):
        pass
    try:
        size = float(fmt.get("size") or 0)
        dur = float(fmt.get("duration") or 0)
        if size > 0 and dur > 0:
            return int(size * 8 / dur * 0.93)
    except (TypeError, ValueError):
        pass
    return None


def analyze(path):
    """ffprobe -> параметры первого видеопотока (или None)."""
    try:
        r = tools.run([tools.FFPROBE_EXE, "-v", "quiet", "-print_format", "json",
                       "-show_streams", "-show_format", path], timeout=40)
        data = json.loads(r.stdout or "{}")
    except Exception:
        return None
    v = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), None)
    if not v:
        return None
    pix = (v.get("pix_fmt") or "").lower()
    transfer = (v.get("color_transfer") or "").lower()
    hdr = transfer in ("smpte2084", "arib-std-b67")
    depth = 10 if ("10" in pix or "p010" in pix or "12" in pix) else 8
    return {
        "codec": (v.get("codec_name") or "").lower(),
        "height": int(v.get("height") or 0),
        "pix_fmt": pix,
        "hdr": hdr,
        "depth": depth,
        "primaries": v.get("color_primaries") or "",
        "transfer": transfer,
        "space": v.get("color_space") or "",
        "bit_rate": _src_bitrate(v, data.get("format") or {}),
        "fps": _fps(v),
    }


def _fps(v):
    """Кадры/с из r_frame_rate («60/1» -> 60.0) или None."""
    raw = v.get("r_frame_rate") or v.get("avg_frame_rate") or ""
    try:
        num, den = str(raw).split("/")
        fps = float(num) / float(den)
        return fps if 1.0 <= fps <= 480.0 else None
    except (ValueError, ZeroDivisionError):
        return None


def decide_target(a, force=False):
    """'h264' | 'hevc' | None (None — конвертация не нужна).

    Галочка «Convert YouTube Videos» трогает ТОЛЬКО VP9: H.264 и так монтажный,
    AV1 из селектора исключён. HDR VP9 -> HEVC 10-bit, обычный VP9 -> H.264 8-bit.

    force=True — конвертируем всегда (даже если файл уже монтажный).
    """
    if a is None:
        return None
    if force:
        return "hevc" if a["hdr"] else "h264"
    if a["codec"] not in ("vp9", "vp09"):
        return None
    return "hevc" if a["hdr"] else "h264"


def _encoder_order(target, encoders):
    """Очередь энкодеров: сначала GPU (NVENC/QSV/AMF), затем CPU. Пробуем по
    очереди — неработающий GPU-энкодер откатывается на следующий, а не сразу
    на процессор."""
    order = {
        "h264": ["h264_nvenc", "h264_qsv", "h264_amf", "libx264"],
        "hevc": ["hevc_nvenc", "hevc_qsv", "hevc_amf", "libx265"],
    }[target]
    chain = [e for e in order if e in encoders]
    return chain or [order[-1]]


def _is_gpu(enc):
    return any(h in enc for h in _GPU_HINTS)


def probe_duration(path):
    """Длительность файла в секундах (для прогресса конвертации) или None."""
    try:
        r = tools.run([tools.FFPROBE_EXE, "-v", "quiet", "-show_entries",
                       "format=duration", "-of", "csv=p=0", path], timeout=30)
        return float((r.stdout or "").strip())
    except Exception:
        return None


def target_bitrate(a, target):
    """Целевой битрейт (бит/с) по исходному из ffprobe, или None (нет данных).

    H.264 менее эффективен, чем VP9, поэтому берём исходный с запасом ~1.3x —
    качество сохраняется, а файл не раздувается (было: фиксированный CQ/CRF без
    привязки к источнику -> 24 Мбит VP9 превращались в 67 Мбит H.264).
    HEVC примерно равен VP9 по эффективности — коэффициент 1.0."""
    src = (a or {}).get("bit_rate")
    if not src or src <= 0:
        return None
    k = 1.0 if target == "hevc" else 1.3
    br = int(src * k)
    return max(500_000, min(br, 200_000_000))


def _rate_args(encoder, br):
    """Качество + ПОТОЛОК битрейта (br) под конкретный энкодер.

    Не жёсткий `-b:v br`: он заставляет энкодер выбирать весь битрейт даже на
    простой картинке — файл не меньше, а кодируется заметно медленнее. Ведём по
    качеству (cq/crf), а br служит лишь ограничителем сверху — так простые сцены
    занимают меньше, а раздувания (24 Мбит VP9 -> 67 Мбит H.264) не происходит."""
    bufsize = br * 2
    if "nvenc" in encoder:
        return ["-rc", "vbr", "-cq", "21", "-b:v", "0",
                "-maxrate", str(br), "-bufsize", str(bufsize)]
    if "qsv" in encoder:
        return ["-global_quality", "21",
                "-maxrate", str(br), "-bufsize", str(bufsize)]
    if "amf" in encoder:        # «-quality quality» уже задан в build_args
        return ["-b:v", str(br), "-maxrate", str(br), "-bufsize", str(bufsize)]
    return ["-crf", "20", "-maxrate", str(br), "-bufsize", str(bufsize)]  # libx26x


def _gop_args(encoder, a):
    """Keyframe примерно раз в 2 секунды.

    По умолчанию энкодеры ставят длинный GOP (у NVENC вообще бесконечный), и
    перемотка 4K упирается в редкие опорные кадры — плеер отматывает до далёкого
    keyframe и декодирует всё до нужного места (это и есть «подлагивает при
    быстрой перемотке»; faststart тут ни при чём — он про расположение moov)."""
    fps = (a or {}).get("fps") or 30.0
    g = max(12, int(round(fps * 2)))
    args = ["-g", str(g), "-keyint_min", str(max(1, g // 2))]
    if "libx264" in encoder or "libx265" in encoder:
        args += ["-sc_threshold", "0"]   # равномерный шаг keyframe
    return args


def build_args(in_path, out_path, target, encoder, a):
    args = [tools.FFMPEG_EXE, "-hide_banner", "-y", "-i", in_path,
            "-map", "0:v:0", "-map", "0:a?", "-c:a", "aac", "-b:a", "192k"]
    args += ["-c:v", encoder]
    args += _gop_args(encoder, a)      # частые keyframe -> отзывчивая перемотка
    br = target_bitrate(a, target)      # None -> запасной путь по качеству (CQ/CRF)

    if target == "hevc":          # HDR 10-bit
        if "nvenc" in encoder:
            args += ["-preset", "p5", "-pix_fmt", "p010le"]
            args += _rate_args(encoder, br) if br else ["-rc", "vbr", "-cq", "23"]
        elif "qsv" in encoder:
            args += ["-pix_fmt", "p010le"]
            args += _rate_args(encoder, br) if br else ["-global_quality", "23"]
        elif "amf" in encoder:
            args += ["-quality", "quality", "-pix_fmt", "p010le"]
            args += _rate_args(encoder, br) if br else []
        else:
            args += ["-preset", "medium", "-pix_fmt", "yuv420p10le",
                     "-x265-params", "profile=main10"]
            args += _rate_args(encoder, br) if br else ["-crf", "20"]
        args += ["-tag:v", "hvc1"]
        if a.get("primaries"):
            args += ["-color_primaries", a["primaries"]]
        if a.get("transfer"):
            args += ["-color_trc", a["transfer"]]
        if a.get("space"):
            args += ["-colorspace", a["space"]]
    else:                         # H.264 8-bit
        if "nvenc" in encoder:
            args += ["-preset", "p5", "-pix_fmt", "yuv420p"]
            args += _rate_args(encoder, br) if br else ["-rc", "vbr", "-cq", "21"]
        elif "qsv" in encoder:
            args += ["-pix_fmt", "nv12"]
            args += _rate_args(encoder, br) if br else ["-global_quality", "21"]
        elif "amf" in encoder:
            args += ["-quality", "quality", "-pix_fmt", "yuv420p"]
            args += _rate_args(encoder, br) if br else []
        else:
            args += ["-preset", "medium", "-pix_fmt", "yuv420p"]
            args += _rate_args(encoder, br) if br else ["-crf", "20"]
        # High profile: для 4K заметно лучше Main (8x8 transform) и роднее плеерам —
        # NVENC по умолчанию отдавал Main.
        args += ["-profile:v", "high", "-tag:v", "avc1"]

    # Машиночитаемый прогресс на stdout (для полосы), без шумной строки stats.
    args += ["-progress", "pipe:1", "-nostats"]
    args += ["-movflags", "+faststart", out_path]
    return args


def _run_cancellable(args, hooks, duration=None, on_progress=None):
    """Запуск ffmpeg с возможностью отмены. Возвращает returncode (или None при
    отмене). on_progress(frac, speed) вызывается по ходу кодирования."""
    proc = tools.popen(args)
    if hooks is not None:
        hooks.set_proc(proc)
    speed = ""
    for line in proc.stdout:
        if hooks is not None and hooks.is_stopped():
            tools.kill_tree(proc)
            proc.wait()
            return None
        if on_progress and duration:
            s = line.strip()
            if s.startswith("speed="):
                speed = s.split("=", 1)[1].strip()
            elif s.startswith(("out_time_us=", "out_time_ms=")):
                try:
                    sec = int(s.split("=", 1)[1]) / 1_000_000.0   # ffmpeg: микросекунды
                    on_progress(max(0.0, min(1.0, sec / duration)), speed)
                except ValueError:
                    pass
    return proc.wait()


def remux_to_mp4(path, hooks=None, on_progress=None, log=None):
    """MKV -> MP4 без перекодирования (-c copy): меняем только контейнер.

    Нужен, когда конвертация не понадобилась: контейнер выбирается ДО загрузки
    (под ожидаемый VP9 берём MKV), а кодек известен только ПОСЛЕ. Если приехал
    H.264, перекодировать его в H.264 незачем — достаточно переложить потоки в
    MP4 без потери качества. Прогресс шлём наружу: на 4K-файле это копирование
    гигабайтов, и без него полоса замирала бы на середине. Возвращает путь."""
    if not path or not os.path.isfile(path):
        return path
    base, ext = os.path.splitext(path)
    if ext.lower() == ".mp4":
        return path
    out = base + ".__remux__.mp4"
    dur = probe_duration(path) if on_progress else None
    try:
        rc = _run_cancellable(
            [tools.FFMPEG_EXE, "-hide_banner", "-y", "-i", path,
             "-map", "0:v:0", "-map", "0:a?", "-c", "copy",
             "-movflags", "+faststart", "-progress", "pipe:1", "-nostats", out],
            hooks, dur, on_progress)
    except Exception:
        rc = 1                       # сбой запуска — файл не теряем
    if rc is None:                   # отменили — вернуть исходник как есть
        _rm(out)
        return path
    ok = (rc == 0 and os.path.isfile(out) and os.path.getsize(out) > 0)
    if not ok:
        if log is not None:
            log.info("remux to mp4 failed -> keeping %s" % ext)
        _rm(out)
        return path                  # не вышло — отдаём как есть, файл цел
    final = base + ".mp4"
    try:
        os.replace(out, final)
    except OSError:
        # Переименовать не вышло — откатываемся к исходнику, а не оставляем на
        # диске две копии одного видео (на 4K это лишние гигабайты).
        _rm(out)
        return path
    _rm(path)                        # исходный контейнер больше не нужен
    return final


def convert(path, on_status=None, hooks=None, force=False, log=None):
    """
    Конвертирует файл при необходимости; возвращает путь к итоговому файлу
    (оригинал перезаписывается). Прерываемо (hooks): при отмене ffmpeg
    убивается, частичный файл удаляется. force=True — конвертируем всегда.

    Если перекодирование не нужно (напр., вместо ожидаемого VP9 приехал H.264),
    файл всё равно приводится к MP4 — быстрым ремуксом, без потери качества.
    """
    if not path or not os.path.isfile(path):
        return path
    a = analyze(path)
    target = decide_target(a, force=force)
    cur = {"enc": ""}          # какой энкодер работает прямо сейчас ("" = ремукс)

    def _emit(frac, speed):
        if hooks is not None and getattr(hooks, "on_progress", None):
            pct = int(round(frac * 100))
            hooks.on_progress({
                "stage": "convert", "frac": frac,
                "percent_str": f"{tr('Converting…')} {pct}%",
                "speed": speed or "", "eta": "", "size": "",
                # Упал GPU-энкодер — кодируем процессором, ВТРОЕ медленнее и с
                # нуля. Сообщаем наверх, чтобы подписать это в строке загрузки.
                # Ремукс (enc="") процессор не грузит — там пометки нет.
                "cpu": bool(cur["enc"]) and not _is_gpu(cur["enc"]),
            })

    if target is None:
        # Перекодировать нечего, но полоса уже отвела половину под конвертацию:
        # ведём её ремуксом, иначе на большом файле она замрёт на 50%.
        if on_status:
            on_status(tr("Converting…"))
        return remux_to_mp4(path, hooks=hooks, on_progress=_emit, log=log)

    encoders = available_encoders()
    base, _ = os.path.splitext(path)
    out = base + ".__conv__.mp4"
    duration = probe_duration(path)

    if on_status:
        on_status(tr("Converting…"))

    # Пробуем энкодеры по очереди: рабочий GPU -> следующий GPU -> CPU.
    rc = None
    for enc in _encoder_order(target, encoders):
        cur["enc"] = enc
        rc = _run_cancellable(build_args(path, out, target, enc, a),
                              hooks, duration, _emit)
        if rc is None:                  # отменено пользователем
            _rm(out)
            return path
        if rc == 0:
            break
        # Энкодер не сработал — следующий кандидат, и КОДИРУЕМ С НУЛЯ (частичный
        # файл непригоден). Пишем в лог: иначе тихий уход на CPU не отследить.
        if log is not None:
            log.info("convert: encoder %s failed (rc=%s) -> next candidate"
                     % (enc, rc))
        _rm(out)
        if _is_gpu(enc) and _ENCODERS is not None:
            _ENCODERS.discard(enc)      # больше не пытаемся им в этой сессии

    if rc != 0:
        _rm(out)
        raise RuntimeError("conversion failed")

    final = base + ".mp4"
    try:
        os.replace(out, final)
        if os.path.abspath(final) != os.path.abspath(path) and os.path.exists(path):
            _rm(path)
        return final
    except OSError:
        return out


def _rm(p):
    try:
        if p and os.path.exists(p):
            os.remove(p)
    except OSError:
        pass
