# -*- coding: utf-8 -*-
"""След выполнения run_job на наборе сценариев.

Всё, что лезет в сеть и на диск, подменено. Пишется ПОСЛЕДОВАТЕЛЬНОСТЬ вызовов
и результат — это и есть поведение. Запуск:

    python trace_runjob.py <файл_следа>

Гоняется до и после рефакторинга, файлы сравниваются построчно.
"""
import io, sys, os, hashlib
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.abspath("."))

import core.downloader as dl
from core import hls_cut, ember_dl, convert, tools

TRACE = []


def rec(what, *a):
    TRACE.append("%s(%s)" % (what, ", ".join(str(x) for x in a)))


# --- управляемое поведение подделок ---------------------------------- #
class Env:
    def __init__(self, **kw):
        self.stream_results = []      # очередь ответов _stream
        self.ember_results = []       # очередь ответов _try_ember
        self.hls = None               # None | путь | "raise"
        self.hls_only = False
        self.ember_primary = False
        self.convert_raises = False
        self.move_raises = False
        self.stopped_after = None     # номер вызова _stream, после которого стоп
        self.have_cookies = False
        self.streamlink = False
        self.twitch = False
        self.log_text = ""
        self.convert_on = False
        self.__dict__.update(kw)
        self.stream_calls = 0


ENV = Env()
EXISTING = set()
JOB_N = [0]


def _next_job_dir():
    JOB_N[0] += 1
    return "JOB%d" % JOB_N[0]


class FakeHooks:
    def __init__(self):
        self._stop = False

    def is_stopped(self):
        return self._stop

    def on_status(self, s):
        rec("hooks.on_status", s)

    def on_progress(self, *a, **k):
        pass


HOOKS = FakeHooks()


class FakeLog:
    def __init__(self, url):
        self.url = url

    def begin(self, prefix="download"):
        rec("log.begin")

    def event(self, t):
        rec("log.event", t)

    def info(self, t):
        rec("log.info", t[:40])

    def text(self):
        return ENV.log_text

    def discard(self):
        rec("log.discard")

    def save_error(self):
        rec("log.save_error")


def fake_stream(args, hooks, log, progress=True, ff_total=None):
    ENV.stream_calls += 1
    n = ENV.stream_calls
    rec("_stream#%d" % n, "args=%d" % len(args or []), "progress=%s" % progress)
    if ENV.stopped_after == n:
        HOOKS._stop = True
    if ENV.stream_results:
        ok, dest = ENV.stream_results.pop(0)
    else:
        ok, dest = False, ""
    if ok and dest:
        EXISTING.add(dest)
    rec("  -> ", ok, dest)
    return ok, dest


def fake_ember(url, option, settings, hooks, log, job_dir, title):
    rec("_try_ember", url)
    if ENV.ember_results:
        ok, dest = ENV.ember_results.pop(0)
    else:
        ok, dest = False, ""
    if ok and dest:
        EXISTING.add(dest)
    rec("  -> ", ok, dest)
    return ok, dest


def fake_hls_cut(info, a, b, out, height=None, hooks=None, log=None, limit_bps=None):
    rec("hls_cut.cut", "%.1f-%.1f" % (a, b), out)
    if ENV.hls == "raise":
        raise hls_cut.HlsCutError("оборвалось")
    if ENV.hls:
        EXISTING.add(ENV.hls)
    return ENV.hls or ""


def fake_convert(path, hooks=None, log=None, section=None):
    rec("convert.convert", path, "section=%s" % (section,))
    if ENV.convert_raises:
        raise RuntimeError("конвертация упала")
    out = path + ".conv"
    EXISTING.add(out)
    return out


def fake_move(src, dst_dir):
    rec("_move_to_dest", src, dst_dir)
    if ENV.move_raises:
        raise RuntimeError("перенос упал")
    return os.path.join(dst_dir, os.path.basename(src))


def install():
    dl._stream = fake_stream
    dl._try_ember = fake_ember
    dl._move_to_dest = fake_move
    dl._rm_dir = lambda d: rec("_rm_dir", d)
    dl._new_job_dir = _next_job_dir
    dl._expected_path = lambda *a, **k: "EXPECTED"
    dl._scan_job_output = lambda d, e: rec("_scan_job_output", d, e) or ""
    dl._out_dir = lambda s: "DOWNLOADS"
    dl._write_info_json = lambda i: ("INFO.json" if i else None)
    dl._del_cookie_copy = lambda a: None
    dl.build_download_args = lambda *a, **k: ["ytdlp"] * (3 + len(k))
    dl.build_streamlink_args = lambda url, s, d: (["streamlink"], "SL.mp4")
    dl.have_cookies = lambda s, u: ENV.have_cookies
    dl.cookie_args = lambda s, u: []
    dl.is_twitch = lambda u: ENV.twitch
    dl.probe = lambda *a, **k: {"formats": [{"url": "u"}]}
    dl._run_thumbnail_job = lambda *a: rec("_run_thumbnail_job") or (True, "THUMB.jpg", None)
    dl.logbook = type("LB", (), {"Log": FakeLog})
    tools.have_streamlink = lambda: ENV.streamlink
    hls_cut.looks_hls_only = lambda i: ENV.hls_only
    hls_cut.cut = fake_hls_cut
    ember_dl.is_primary = lambda u: ENV.ember_primary
    convert.convert = fake_convert
    dl.should_convert = lambda o, u, st: ENV.convert_on
    os.path.exists = lambda p: p in EXISTING
    os.remove = lambda p: None
    os.path.isfile = lambda p: p in EXISTING


SETTINGS = {"download_dir": "D", "convert": True}


def run(name, option, url, env, info=None, title="Ролик"):
    global ENV
    ENV = env
    EXISTING.clear()
    HOOKS._stop = False
    JOB_N[0] = 0
    TRACE.append("")
    TRACE.append("=== %s ===" % name)
    try:
        ok, dest, log = dl.run_job(option, url, SETTINGS, HOOKS, title=title, info=info)
        TRACE.append("ИТОГ: ok=%s dest=%s" % (ok, dest))
    except Exception as exc:
        TRACE.append("ИСКЛЮЧЕНИЕ: %s: %s" % (type(exc).__name__, exc))


V = "https://example.com/watch?v=1"
OPT = {"label": "Best Quality", "fmt": "bv+ba"}
OPT_SEC = dict(OPT, section="*10.0-40.0")

install()

run("миниатюра", {"thumbnail": True}, V, Env())
run("галерея: успех", {"gallery": True}, V, Env(ember_results=[(True, "GAL")]))
run("галерея: ember упал", {"gallery": True}, V, Env())
run("галерея: перенос упал", {"gallery": True}, V,
    Env(ember_results=[(True, "GAL")], move_raises=True))
run("HLS-секция: успех", OPT_SEC, V, Env(hls_only=True, hls="CUT.mp4"), info={"x": 1})
run("HLS-секция: обрыв", OPT_SEC, V, Env(hls_only=True, hls="raise"), info={"x": 1})
run("ember основной: успех", OPT, V, Env(ember_primary=True, ember_results=[(True, "EMB")]))
run("ember основной: упал, дальше yt-dlp", OPT, V,
    Env(ember_primary=True, stream_results=[(True, "EXPECTED")]))
run("быстрый путь: успех", OPT, V, Env(stream_results=[(True, "EXPECTED")]), info={"x": 1})
run("быстрый путь: упал, полное извлечение", OPT, V,
    Env(stream_results=[(False, ""), (True, "EXPECTED")]), info={"x": 1})
run("обычный путь: успех", OPT, V, Env(stream_results=[(True, "EXPECTED")]))
run("повтор без кук", OPT, V,
    Env(stream_results=[(False, ""), (True, "EXPECTED")], have_cookies=True,
        log_text="could not copy chrome cookie database"))
run("403 -> impersonate", OPT, V,
    Env(stream_results=[(False, ""), (True, "EXPECTED")],
        log_text="ERROR: HTTP Error 403: Forbidden"))
run("секция: фолбэк на полную + резка", OPT_SEC, V,
    Env(stream_results=[(False, ""), (True, "EXPECTED")],
        log_text="error opening input: connection reset by peer"))
run("секция: фолбэк, резка упала", OPT_SEC, V,
    Env(stream_results=[(False, ""), (True, "EXPECTED")], convert_raises=True,
        log_text="error opening input: connection reset by peer"))
run("vimeo: плеерная ссылка", OPT, "https://vimeo.com/123456789",
    Env(stream_results=[(False, ""), (True, "VIM")]))
run("ember как запасной", OPT, V,
    Env(stream_results=[(False, "")], ember_results=[(True, "EMB2")]))
run("twitch: streamlink", OPT, "https://twitch.tv/videos/1",
    Env(stream_results=[(False, "")], twitch=True, streamlink=True))
run("всё провалилось", OPT, V, Env(stream_results=[(False, "")]))
run("конвертация: успех", OPT, V,
    Env(stream_results=[(True, "EXPECTED")], convert_on=True))
run("конвертация упала", OPT, V,
    Env(stream_results=[(True, "EXPECTED")], convert_raises=True, convert_on=True))
run("ember: конвертация пропускается", OPT, V,
    Env(ember_primary=True, ember_results=[(True, "EMB")], convert_on=True))
run("HLS: конвертация пропускается", OPT_SEC, V,
    Env(hls_only=True, hls="CUT.mp4", convert_on=True), info={"x": 1})
run("остановлено на первом _stream", OPT, V,
    Env(stream_results=[(False, "")], stopped_after=1))
run("перенос упал в конце", OPT, V,
    Env(stream_results=[(True, "EXPECTED")], move_raises=True))

text = "\n".join(TRACE) + "\n"
out = sys.argv[1]
with open(out, "w", encoding="utf-8") as f:
    f.write(text)
print("строк следа:", len(TRACE))
print("sha256:", hashlib.sha256(text.encode("utf-8")).hexdigest()[:16])
print("->", out)
