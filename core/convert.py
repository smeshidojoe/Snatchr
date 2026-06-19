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


def analyze(path):
    """ffprobe -> параметры первого видеопотока (или None)."""
    try:
        r = tools.run([tools.FFPROBE_EXE, "-v", "quiet", "-print_format", "json",
                       "-show_streams", path], timeout=40)
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
    }


def decide_target(a, force=False):
    """'h264' | 'hevc' | None (None — конвертация не нужна).

    force=True — конвертируем всегда (даже если файл уже монтажный): HDR -> HEVC
    10-bit, иначе -> H.264 8-bit.
    """
    if a is None:
        return None
    if force:
        return "hevc" if a["hdr"] else "h264"
    codec = a["codec"]
    if a["hdr"]:
        if codec in ("hevc", "h265") and a["depth"] >= 10:
            return None
        return "hevc"
    if codec in ("h264", "avc1", "avc"):
        return None
    return "h264"


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


def build_args(in_path, out_path, target, encoder, a):
    args = [tools.FFMPEG_EXE, "-hide_banner", "-y", "-i", in_path,
            "-map", "0:v:0", "-map", "0:a?", "-c:a", "aac", "-b:a", "192k"]
    args += ["-c:v", encoder]

    if target == "hevc":          # HDR 10-bit
        if "nvenc" in encoder:
            args += ["-preset", "p5", "-rc", "vbr", "-cq", "23", "-pix_fmt", "p010le"]
        elif "qsv" in encoder:
            args += ["-global_quality", "23", "-pix_fmt", "p010le"]
        elif "amf" in encoder:
            args += ["-quality", "quality", "-pix_fmt", "p010le"]
        else:
            args += ["-preset", "medium", "-crf", "20",
                     "-pix_fmt", "yuv420p10le", "-x265-params", "profile=main10"]
        args += ["-tag:v", "hvc1"]
        if a.get("primaries"):
            args += ["-color_primaries", a["primaries"]]
        if a.get("transfer"):
            args += ["-color_trc", a["transfer"]]
        if a.get("space"):
            args += ["-colorspace", a["space"]]
    else:                         # H.264 8-bit
        if "nvenc" in encoder:
            args += ["-preset", "p5", "-rc", "vbr", "-cq", "21", "-pix_fmt", "yuv420p"]
        elif "qsv" in encoder:
            args += ["-global_quality", "21", "-pix_fmt", "nv12"]
        elif "amf" in encoder:
            args += ["-quality", "quality", "-pix_fmt", "yuv420p"]
        else:
            args += ["-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p"]
        args += ["-tag:v", "avc1"]

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


def convert(path, on_status=None, hooks=None, force=False):
    """
    Конвертирует файл при необходимости; возвращает путь к итоговому файлу
    (оригинал перезаписывается). Прерываемо (hooks): при отмене ffmpeg
    убивается, частичный файл удаляется. force=True — конвертируем всегда.
    """
    if not path or not os.path.isfile(path):
        return path
    a = analyze(path)
    target = decide_target(a, force=force)
    if target is None:
        return path

    encoders = available_encoders()
    base, _ = os.path.splitext(path)
    out = base + ".__conv__.mp4"
    duration = probe_duration(path)

    if on_status:
        on_status(tr("Converting…"))

    def _emit(frac, speed):
        if hooks is not None and getattr(hooks, "on_progress", None):
            pct = int(round(frac * 100))
            hooks.on_progress({
                "stage": "convert", "frac": frac,
                "percent_str": f"{tr('Converting…')} {pct}%",
                "speed": speed or "", "eta": "", "size": "",
            })

    # Пробуем энкодеры по очереди: рабочий GPU -> следующий GPU -> CPU.
    rc = None
    for enc in _encoder_order(target, encoders):
        rc = _run_cancellable(build_args(path, out, target, enc, a),
                              hooks, duration, _emit)
        if rc is None:                  # отменено пользователем
            _rm(out)
            return path
        if rc == 0:
            break
        _rm(out)                        # энкодер не сработал — следующий кандидат
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
