"""
Точная вырезка фрагмента из HLS-потока (Twitch VOD и т.п.).

Зачем отдельный путь: при скачивании по таймкодам yt-dlp отдаёт поток ffmpeg, а
тот идёт по сегментам ОТ НАЧАЛА, чтобы добраться до нужной позиции. На 8-часовом
Twitch-VOD это 700+ сегментов ради 10 секунд — выглядит как зависание (ffmpeg на
этом этапе даже не печатает time=, поэтому и полоса прогресса стоит пустая).

Плейлист m3u8 содержит длительность каждого сегмента, поэтому нужные сегменты
вычисляются точно и качаются напрямую — вместо 721 сегмента скачивается 1-2.

Любая осечка (мастер-плейлист без нужного варианта, сеть, нестандартный формат)
возвращает "" — вызывающий тихо откатывается на обычный путь yt-dlp.
"""

import os
import re
import time
import urllib.request
from urllib.parse import urljoin

from core import tools
from core.i18n import tr


def _fmt_size(n):
    """Байты -> «12.3MiB» (тот же вид, что в строках прогресса yt-dlp)."""
    if not n or n <= 0:
        return ""
    mib = n / (1024.0 * 1024.0)
    return "%.2fGiB" % (mib / 1024.0) if mib >= 1024 else "%.2fMiB" % mib


def _fmt_eta(secs):
    secs = max(0, int(secs))
    h, rem = divmod(secs, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")


class HlsCutError(Exception):
    """Резка сорвалась УЖЕ ПОСЛЕ начала скачивания сегментов.

    Отличается от «"" — не наш случай» тем, что откатываться на yt-dlp здесь
    нельзя: на длинной HLS-секции он отдаёт обрезанный файл с неверной
    длительностью, и пользователь считает загрузку успешной. Честная ошибка
    лучше молча битого файла."""

# Потолок на число сегментов в одном куске. У Twitch сегмент = 10 с, так что
# 1200 штук ≈ 3.3 часа — с запасом на любую разумную вырезку.
_MAX_SEGMENTS = 1200


def _fetch(url, headers=None, timeout=30, retries=0):
    """Скачивает URL целиком. retries — сколько раз повторить при сбое сети.

    Сегменты обязательно качать с повторами: на часовом отрезке их сотни, и
    единственная сетевая осечка иначе роняет всю работу — а дальше управление
    уходило к yt-dlp, который на длинной секции отдавал обрезанный файл с
    неверной длительностью."""
    last = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(
                url, headers=dict(headers or {"User-Agent": _UA}))
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except Exception as exc:
            last = exc
            if attempt < retries:
                time.sleep(0.5 * (attempt + 1))
    raise last


def hls_format(info, height=0):
    """Формат-кандидат HLS из info yt-dlp: (url, http_headers) или (None, {}).

    Возвращаем что-то ТОЛЬКО если у ссылки нет обычных (не-HLS) видеоформатов.
    Иначе обычный путь yt-dlp и лучше, и надёжнее — а наш резак нужен именно
    там, где кроме HLS ничего нет (Twitch VOD).

    height — желаемая высота (0 = лучшая доступная)."""
    fmts = _formats(info)
    for f in fmts:
        proto = (f.get("protocol") or "").lower()
        # vcodec у прогрессивных форматов часто не заполнен (Vimeo отдаёт None),
        # поэтому ориентируемся на протокол + наличие высоты, а не на кодек.
        if (f.get("height") and "m3u8" not in proto
                and proto.startswith("http") and f.get("vcodec") != "none"):
            return None, {}              # есть обычный формат — не вмешиваемся
    best = None
    for f in fmts:
        proto = (f.get("protocol") or "").lower()
        if "m3u8" not in proto:
            continue
        if not (f.get("url") or "").startswith("http"):
            continue
        h = f.get("height") or 0
        if height and h and h > height:
            continue                     # выше запрошенного не берём
        if best is None or (h or 0) > (best.get("height") or 0):
            best = f
    if not best:
        return None, {}
    return best["url"], dict(best.get("http_headers") or {})


def _formats(info):
    """Список форматов из info, без мусора (None/не-словари/не-info)."""
    if not isinstance(info, dict):
        return []
    raw = info.get("formats")
    if not isinstance(raw, (list, tuple)):
        return []
    return [f for f in raw if isinstance(f, dict)]


def looks_hls_only(info):
    """Похоже ли, что у ссылки ТОЛЬКО HLS — по протоколам, без обращения к URL.

    Нужна как дешёвый предфильтр: URL форматов в кэше не хранятся (протухают),
    и без этой проверки пришлось бы делать лишний probe даже там, где резак
    заведомо не нужен (YouTube и т.п.). Нет форматов вовсе -> True, потому что
    судить не по чему и решит уже сам cut()."""
    fmts = _formats(info)
    if not fmts:
        return True
    has_hls = any("m3u8" in (f.get("protocol") or "").lower() for f in fmts)
    has_plain = any(
        f.get("height") and "m3u8" not in (f.get("protocol") or "").lower()
        and (f.get("protocol") or "").lower().startswith("http")
        and f.get("vcodec") != "none"
        for f in fmts)
    return has_hls and not has_plain


def _parse_segments(text, base_url):
    """Media-плейлист -> [(start_sec, end_sec, url)]. Пустой список, если это не
    media-плейлист (или в нём нет сегментов)."""
    segs, t = [], 0.0
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if not line.startswith("#EXTINF:"):
            continue
        try:
            dur = float(line.split(":", 1)[1].split(",")[0])
        except (ValueError, IndexError):
            continue
        for j in range(i + 1, min(i + 5, len(lines))):
            nxt = lines[j].strip()
            if nxt and not nxt.startswith("#"):
                segs.append((t, t + dur, urljoin(base_url, nxt)))
                t += dur
                break
    return segs


_STREAM_INF = re.compile(r"#EXT-X-STREAM-INF:.*?RESOLUTION=\d+x(\d+)", re.I)
_EXT_MAP = re.compile(r'#EXT-X-MAP:.*?URI="([^"]+)"', re.I)


def _init_segment(text, base_url):
    """URL init-сегмента (#EXT-X-MAP) для fMP4-потоков или None.

    Twitch отдаёт сегменты в fMP4 (`724.mp4`), и без init-сегмента с заголовками
    контейнера склейка невалидна — ffmpeg отказывается её открывать."""
    m = _EXT_MAP.search(text or "")
    return urljoin(base_url, m.group(1)) if m else None


def _resolve_media_playlist(url, headers, height=0, timeout=30):
    """Отдаёт (текст media-плейлиста, его базовый URL).

    Если по ссылке лежит МАСТЕР-плейлист (список качеств) — выбираем вариант
    не выше height (или лучший) и скачиваем уже его."""
    text = _fetch(url, headers, timeout).decode("utf-8", "replace")
    if "#EXT-X-STREAM-INF" not in text:
        return text, url                 # уже media-плейлист
    lines = text.splitlines()
    best = None                          # (height, url)
    for i, line in enumerate(lines):
        m = _STREAM_INF.search(line)
        if not m:
            continue
        h = int(m.group(1))
        for j in range(i + 1, min(i + 4, len(lines))):
            nxt = lines[j].strip()
            if nxt and not nxt.startswith("#"):
                if height and h > height:
                    break
                if best is None or h > best[0]:
                    best = (h, urljoin(url, nxt))
                break
    if best is None:
        return "", url
    sub = _fetch(best[1], headers, timeout).decode("utf-8", "replace")
    return sub, best[1]


def _cut_encoders():
    """Энкодеры для резки: сначала GPU, потом процессор.

    Резать 1440p60-фрагмент на libx264 — это 100% CPU на минуты; NVENC/QSV/AMF
    делают то же за секунды. Список берём из convert, чтобы не дублировать
    логику определения доступных энкодеров."""
    from core import convert
    enc = convert.available_encoders()
    order = ["h264_nvenc", "h264_qsv", "h264_amf", "libx264"]
    chain = [e for e in order if e in enc]
    return chain or ["libx264"]


def _enc_args(encoder):
    if "nvenc" in encoder:
        return ["-c:v", encoder, "-preset", "p4", "-rc", "vbr", "-cq", "21"]
    if "qsv" in encoder:
        return ["-c:v", encoder, "-global_quality", "21"]
    if "amf" in encoder:
        return ["-c:v", encoder, "-quality", "balanced"]
    return ["-c:v", encoder, "-preset", "veryfast", "-crf", "20"]


def _run_cut(raw, out_path, offset, length, hooks=None, log=None):
    """Режет [offset, offset+length] из склейки сегментов. Возвращает rc.

    Если начало запрошенного куска совпало с границей сегмента, перекодировать
    нечего — копируем потоки (мгновенно). Иначе точный кадр требует
    перекодирования: пробуем GPU, при отказе откатываемся на процессор.
    Прогресс шлём наружу, иначе полоса стоит на 100% всё время резки."""
    exact_start = offset < 0.05          # начало и так на границе сегмента
    attempts = []
    if exact_start:
        attempts.append(["-c", "copy"])
    for enc in _cut_encoders():
        attempts.append(_enc_args(enc) + ["-c:a", "aac", "-b:a", "192k"])

    for i, codec_args in enumerate(attempts):
        args = [tools.FFMPEG_EXE, "-hide_banner", "-y",
                "-ss", "%.3f" % offset, "-i", raw, "-t", "%.3f" % length,
                "-map", "0:v:0", "-map", "0:a?"] + codec_args + [
                "-movflags", "+faststart",
                "-progress", "pipe:1", "-nostats", out_path]
        rc = _stream_ffmpeg(args, length, hooks)
        if rc == 0:
            if log is not None:
                log.info("HLS cut: %s" % ("stream copy" if codec_args[:2] == ["-c", "copy"]
                                          else " ".join(codec_args[:2])))
            return 0
        if rc is None:                   # отмена пользователем
            return 1
        _rm(out_path)
        if log is not None and i + 1 < len(attempts):
            log.info("HLS cut: attempt failed (rc=%s) -> next encoder" % rc)
    return 1


def _stream_ffmpeg(args, total, hooks):
    """Запуск ffmpeg с прогрессом по out_time. rc, либо None при отмене."""
    proc = tools.popen(args)
    if hooks is not None and getattr(hooks, "set_proc", None):
        hooks.set_proc(proc)
    for line in proc.stdout:
        if hooks is not None and hooks.is_stopped():
            tools.kill_tree(proc)
            proc.wait()
            return None
        s = line.strip()
        if s.startswith(("out_time_us=", "out_time_ms=")) and total > 0:
            try:
                sec = int(s.split("=", 1)[1]) / 1_000_000.0
            except ValueError:
                continue
            frac = max(0.0, min(1.0, sec / total))
            if hooks is not None and getattr(hooks, "on_progress", None):
                # Резка — отдельный этап после скачивания: показываем как
                # постобработку, иначе полоса просто стоит на 100%.
                hooks.on_progress({
                    "stage": "convert", "frac": frac,
                    "percent_str": "%s %d%%" % (tr("Trimming…"), round(frac * 100)),
                    "speed": "", "eta": "", "size": "",
                })
    return proc.wait()


def cut(info, start, end, out_path, height=0, hooks=None, log=None, timeout=30):
    """Скачивает только сегменты, покрывающие [start, end], и точно вырезает.

    Возвращает путь к готовому файлу или "" — тогда вызывающий идёт обычным
    путём yt-dlp (ничего не сломав)."""
    try:
        url, headers = hls_format(info, height)
        if not url:
            return ""
        headers.setdefault("User-Agent", _UA)
        text, base = _resolve_media_playlist(url, headers, height, timeout)
        if not text:
            return ""
        segs = _parse_segments(text, base)
        if not segs:
            return ""
        need = [s for s in segs if s[1] > start and s[0] < end]
        if not need or len(need) > _MAX_SEGMENTS:
            return ""                    # слишком длинный кусок — обычный путь
        if log is not None:
            log.info("HLS cut: %d segment(s) of %d for %.0f-%.0f s"
                     % (len(need), len(segs), start, end))

        # 1. Качаем нужные сегменты (прогресс — по их числу). Для fMP4 первым
        #    пишем init-сегмент, иначе склейка невалидна.
        init_url = _init_segment(text, base)
        raw = out_path + (".__seg__.mp4" if init_url else ".__seg__.ts")
        got = 0
        with open(raw, "wb") as out:
            if init_url:
                out.write(_fetch(init_url, headers, timeout))
            started = time.time()
            done_bytes = 0
            for s0, _s1, seg_url in need:
                if hooks is not None and hooks.is_stopped():
                    _rm(raw)
                    return ""
                # Сегмент 1440p60 весит десятки МБ — таймаут щедрее, чем на
                # плейлист, плюс повторы на случай сетевой осечки.
                try:
                    chunk = _fetch(seg_url, headers, max(timeout, 120), retries=3)
                except Exception as exc:
                    _rm(raw)
                    raise HlsCutError(
                        "segment %d/%d failed: %s" % (got + 1, len(need),
                                                      str(exc)[:120]))
                out.write(chunk)
                done_bytes += len(chunk)
                got += 1
                if hooks is not None and getattr(hooks, "on_progress", None):
                    frac = got / float(len(need))
                    # Скорость меряем сами (сегменты качаем мы, а не yt-dlp), а
                    # полный размер оцениваем по уже скачанному: он неизвестен
                    # заранее — в плейлисте есть длительности, но не байты.
                    elapsed = max(0.001, time.time() - started)
                    speed = done_bytes / elapsed
                    total_est = done_bytes / frac if frac > 0 else 0
                    info = {
                        "frac": max(0.0, min(1.0, frac)),
                        "percent_str": "%.1f%%" % (frac * 100),
                        "speed": "%.2fMiB/s" % (speed / (1024.0 * 1024.0)),
                        "downloaded": _fmt_size(done_bytes),
                        "size": _fmt_size(total_est),
                        "eta": "",
                    }
                    if speed > 0 and total_est > done_bytes:
                        info["eta"] = _fmt_eta((total_est - done_bytes) / speed)
                    hooks.on_progress(info)
        # 2. Точная резка: сегмент начинается раньше запрошенного start,
        #    поэтому отступ считаем от начала ПЕРВОГО скачанного сегмента.
        offset = max(0.0, start - need[0][0])
        length = max(0.05, end - start)
        rc = _run_cut(raw, out_path, offset, length, hooks, log)
        _rm(raw)
        if rc != 0 or not os.path.isfile(out_path) \
                or os.path.getsize(out_path) == 0:
            if log is not None:
                log.info("HLS cut: ffmpeg failed (rc=%s)" % rc)
            _rm(out_path)
            return ""
        return out_path
    except HlsCutError:
        raise                            # уже начали качать — наверх, без отката
    except Exception as exc:
        if log is not None:
            log.info("HLS cut failed: %s" % str(exc)[:200])
        return ""


def _rm(p):
    try:
        if p and os.path.isfile(p):
            os.remove(p)
    except OSError:
        pass
