"""
Хелперы ffmpeg/ffprobe для истории и панели обрезки: длительность, извлечение
одного кадра (обложка / превью ползунка), полоска кадров (filmstrip) и сама
обрезка (-ss START -to END -c copy — без перекодирования, быстро).

Все вызовы синхронные и «тихие» (CREATE_NO_WINDOW, UTF-8-окружение). Ошибки не
кидаем наружу без нужды — возвращаем None/False, вызывающий решает, что делать.
"""

import os
import subprocess

from core import tools


def _run(args, timeout=60):
    try:
        return subprocess.run(
            args, capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=timeout,
            creationflags=tools.CREATE_NO_WINDOW, env=tools._utf8_env())
    except Exception:
        return None


def duration(path):
    """Длительность видео в секундах (float) или None."""
    if not path or not os.path.isfile(path):
        return None
    r = _run([tools.FFPROBE_EXE, "-v", "error", "-show_entries",
              "format=duration", "-of", "default=nk=1:nw=1", path], timeout=30)
    if r is None or r.returncode != 0:
        return None
    try:
        return float((r.stdout or "").strip())
    except (ValueError, TypeError):
        return None


def probe_media(path):
    """Разрешение и длительность готового файла (width, height, duration) через
    один вызов ffprobe. Возвращает dict с тем, что удалось прочитать (или {})."""
    if not path or not os.path.isfile(path):
        return {}
    r = _run([tools.FFPROBE_EXE, "-v", "error",
              "-select_streams", "v:0",
              "-show_entries", "stream=width,height:format=duration",
              "-of", "default=nw=1", path], timeout=30)
    if r is None or r.returncode != 0:
        return {}
    out = {}
    for line in (r.stdout or "").splitlines():
        line = line.strip()
        if "=" not in line:
            continue
        k, v = line.split("=", 1)
        v = v.strip()
        if v in ("", "N/A"):
            continue
        try:
            if k in ("width", "height"):
                out[k] = int(float(v))
            elif k == "duration":
                out["duration"] = float(v)
        except ValueError:
            pass
    return out


def res_label(height):
    """Высота -> метка разрешения (1080p, 720p, 4K, 8K…) или ''."""
    h = int(height or 0)
    if h <= 0:
        return ""
    if h >= 4320:
        return "8K"
    if h >= 2160:
        return "4K"
    if h >= 1440:
        return "1440p"
    return f"{h}p"


def frame_at(path, ts, out_path, width=0, height=0):
    """Извлекает один кадр на позиции ts (сек) в out_path (jpg). -ss ДО -i —
    быстрый seek по ключевым кадрам. Возвращает out_path или None."""
    if not path or not os.path.isfile(path):
        return None
    ts = max(0.0, float(ts or 0.0))
    vf = None
    if width and height:
        vf = f"scale={width}:{height}:force_original_aspect_ratio=increase," \
             f"crop={width}:{height}"
    elif width:
        vf = f"scale={width}:-2"
    elif height:
        vf = f"scale=-2:{height}"
    args = [tools.FFMPEG_EXE, "-y", "-ss", f"{ts:.3f}", "-i", path,
            "-frames:v", "1", "-q:v", "3"]
    if vf:
        args += ["-vf", vf]
    args.append(out_path)
    r = _run(args, timeout=30)
    if r is not None and os.path.isfile(out_path) and os.path.getsize(out_path) > 0:
        return out_path
    # Fallback: сиквенция могла упасть на seek за пределы — пробуем с нуля.
    if ts > 0:
        return frame_at(path, 0.0, out_path, width, height)
    return None


def thumbnail(path, out_path, width=320):
    """Обложка для истории — кадр примерно с 1-й секунды, шириной width."""
    dur = duration(path)
    ts = 1.0 if (dur is None or dur > 2.0) else max(0.0, (dur or 0.0) * 0.25)
    return frame_at(path, ts, out_path, width=width)


def filmstrip(path, out_path, count=12, frame_w=120, frame_h=0, dur=None):
    """Горизонтальная полоска из count кадров (tile 1xcount) для ленты обрезки.
    Возвращает out_path или None."""
    if not path or not os.path.isfile(path):
        return None
    if dur is None:
        dur = duration(path)
    if not dur or dur <= 0:
        return None
    count = max(2, int(count))
    # Берём каждый n-й кадр так, чтобы получить ~count равномерных кадров.
    fps = count / dur
    vf = f"fps={fps:.5f},scale={frame_w}:{frame_h if frame_h else -2}," \
         f"tile={count}x1"
    args = [tools.FFMPEG_EXE, "-y", "-i", path, "-frames:v", "1",
            "-vf", vf, "-q:v", "4", out_path]
    r = _run(args, timeout=60)
    if r is not None and os.path.isfile(out_path) and os.path.getsize(out_path) > 0:
        return out_path
    return None


def trim_args(path, start, end, out_path):
    """Аргументы ffmpeg для обрезки [start, end] (сек) без перекодирования, либо
    None при неверных параметрах. -ss ДО -i для скорости, длительность фрагмента
    через -t. Вынесено отдельно, чтобы вызывающий мог запустить процесс сам
    (и при необходимости убить его — отмена обрезки)."""
    if not path or not os.path.isfile(path):
        return None
    start = max(0.0, float(start or 0.0))
    end = float(end or 0.0)
    if end <= start:
        return None
    length = end - start
    return [tools.FFMPEG_EXE, "-y", "-ss", f"{start:.3f}", "-i", path,
            "-t", f"{length:.3f}", "-c", "copy", "-avoid_negative_ts", "1",
            out_path]


def trim(path, start, end, out_path):
    """Обрезка [start, end] (сек) без перекодирования (синхронно)."""
    args = trim_args(path, start, end, out_path)
    if args is None:
        return False
    r = _run(args, timeout=180)
    ok = (r is not None and os.path.isfile(out_path)
          and os.path.getsize(out_path) > 0)
    if not ok:
        return False
    return True


def trim_dest(src):
    """Имя выходного файла обрезки рядом с оригиналом: name_trim.ext (+ индекс)."""
    folder = os.path.dirname(src)
    base, ext = os.path.splitext(os.path.basename(src))
    cand = os.path.join(folder, f"{base}_trim{ext}")
    i = 2
    while os.path.exists(cand):
        cand = os.path.join(folder, f"{base}_trim{i}{ext}")
        i += 1
    return cand
