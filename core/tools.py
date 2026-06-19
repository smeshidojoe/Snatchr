"""
Менеджер внешних бинарников (yt-dlp.exe, ffmpeg.exe, ffprobe.exe).

Бинарники лежат в папке tools/ рядом с конфигом и кэшем (APP_DIR в %APPDATA%),
а не в каталоге программы — так они переживают переустановку/обновление
приложения. yt-dlp обновляется часто (ломаются сайты) — его умеем
докачивать/обновлять отдельно. ffmpeg/ffprobe ставятся один раз (сборка BtbN).

Все запуски процессов идут без всплывающего окна консоли (CREATE_NO_WINDOW).
"""

import os
import io
import shutil
import zipfile
import subprocess
import urllib.request

from core.config import APP_DIR
from core.constants import BASE_DIR

TOOLS_DIR = os.path.join(APP_DIR, "tools")
# Старое расположение (каталог программы) — для разовой миграции.
_OLD_TOOLS_DIR = os.path.join(BASE_DIR, "tools")

YTDLP_EXE     = os.path.join(TOOLS_DIR, "yt-dlp.exe")
FFMPEG_EXE    = os.path.join(TOOLS_DIR, "ffmpeg.exe")
FFPROBE_EXE   = os.path.join(TOOLS_DIR, "ffprobe.exe")

YTDLP_URL  = "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp.exe"
FFMPEG_API = "https://api.github.com/repos/BtbN/FFmpeg-Builds/releases?per_page=20"
# Берём СТАБИЛЬНУЮ сборку ветки 7.1 (а не master): master собран против свежих
# nvenc-заголовков и требует драйвер новее, чем у большинства NVIDIA-карт, из-за
# чего GPU-кодирование падает и всё уходит на CPU. У 7.1 nvenc совместим с
# текущими драйверами. Если резолв не удался — откатываемся на master.
FFMPEG_ZIP = ("https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/"
              "ffmpeg-master-latest-win64-gpl.zip")

# Флаг запуска процесса без окна консоли (Windows).
CREATE_NO_WINDOW = 0x08000000


def _migrate_old_tools():
    """Разовый перенос бинарников из старой папки (каталог программы) в APP_DIR."""
    if os.path.isdir(TOOLS_DIR) or not os.path.isdir(_OLD_TOOLS_DIR):
        return
    if os.path.abspath(_OLD_TOOLS_DIR) == os.path.abspath(TOOLS_DIR):
        return
    try:
        os.makedirs(APP_DIR, exist_ok=True)
        shutil.move(_OLD_TOOLS_DIR, TOOLS_DIR)
    except Exception:
        pass


def ensure_dir():
    _migrate_old_tools()
    os.makedirs(TOOLS_DIR, exist_ok=True)


# Разовая миграция при импорте — чтобы проверки have_* сразу видели бинарники
# на новом месте и не запускали повторное скачивание.
_migrate_old_tools()


def have_ytdlp():
    return os.path.isfile(YTDLP_EXE)


def have_ffmpeg():
    return os.path.isfile(FFMPEG_EXE) and os.path.isfile(FFPROBE_EXE)


def streamlink_path():
    """Путь к streamlink, если он есть (в tools/ или в PATH). Иначе None."""
    local = os.path.join(TOOLS_DIR, "streamlink.exe")
    if os.path.isfile(local):
        return local
    return shutil.which("streamlink")


def have_streamlink():
    return streamlink_path() is not None


# ------------------------------------------------------------------ #
#  Запуск процессов
# ------------------------------------------------------------------ #
def _utf8_env():
    """Окружение, заставляющее дочерний Python (yt-dlp) выводить UTF-8, иначе на
    Windows кириллица в путях приходит в cp1251 и ломается при декодировании."""
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    return env


def run(args, timeout=None):
    """Синхронный запуск; возвращает CompletedProcess (text=True)."""
    return subprocess.run(
        args, capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=timeout, creationflags=CREATE_NO_WINDOW,
        env=_utf8_env(),
    )


def popen(args):
    """Асинхронный запуск со стримингом stdout (для прогресса загрузки)."""
    return subprocess.Popen(
        args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace", bufsize=1,
        creationflags=CREATE_NO_WINDOW, env=_utf8_env(),
    )


def kill_tree(proc):
    """Убивает процесс и всё его дерево (yt-dlp порождает ffmpeg при merge)."""
    if proc is None:
        return
    try:
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                       capture_output=True, creationflags=CREATE_NO_WINDOW)
    except Exception:
        try:
            proc.terminate()
        except Exception:
            pass


# ------------------------------------------------------------------ #
#  Загрузка/обновление бинарников
# ------------------------------------------------------------------ #
def _download(url, dest, progress=None):
    ensure_dir()
    req = urllib.request.Request(url, headers={"User-Agent": "Snatchr"})
    tmp = dest + ".part"
    with urllib.request.urlopen(req, timeout=60) as resp:
        total = int(resp.headers.get("Content-Length") or 0)
        done = 0
        with open(tmp, "wb") as f:
            while True:
                chunk = resp.read(1024 * 64)
                if not chunk:
                    break
                f.write(chunk)
                done += len(chunk)
                if progress and total:
                    progress(done / total)
    os.replace(tmp, dest)


def download_ytdlp(progress=None):
    """Скачать/перезаписать yt-dlp.exe последней версией."""
    _download(YTDLP_URL, YTDLP_EXE, progress)


def ensure_ytdlp(progress=None, on_status=None):
    """Проверка при запуске: если yt-dlp.exe нет — скачиваем его."""
    if not have_ytdlp():
        if on_status:
            on_status("Downloading yt-dlp…")
        download_ytdlp(progress)
    return YTDLP_EXE


def update_ytdlp(progress=None):
    """
    Переустановить yt-dlp: удалить текущий exe и скачать свежий с GitHub.
    progress(frac) — колбэк хода скачивания (0..1). Бросает исключение при сбое.
    """
    try:
        if os.path.isfile(YTDLP_EXE):
            os.remove(YTDLP_EXE)
    except OSError:
        pass
    download_ytdlp(progress)
    return True


def _ffmpeg_url():
    """Ссылка на свежую СТАБИЛЬНУЮ сборку BtbN (win64-gpl ветки 7.1).
    Имена ассетов содержат git-хеш и лежат под датированными тегами, поэтому
    резолвим через GitHub API. Фолбэк — master (тег latest)."""
    import json as _json
    try:
        req = urllib.request.Request(
            FFMPEG_API,
            headers={"User-Agent": "Snatchr", "Accept": "application/vnd.github+json"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            releases = _json.load(resp)
        for rel in releases:
            if rel.get("tag_name") == "latest":
                continue
            for a in rel.get("assets", []):
                n = a.get("name", "")
                if n.endswith("win64-gpl-7.1.zip") and "shared" not in n:
                    return a.get("browser_download_url")
    except Exception:
        pass
    return FFMPEG_ZIP


def download_ffmpeg(progress=None):
    """Скачать ffmpeg.exe + ffprobe.exe (стабильная сборка BtbN, zip).

    progress(frac) — ход скачивания zip (0..1); распаковка быстрая.
    """
    ensure_dir()
    req = urllib.request.Request(_ffmpeg_url(), headers={"User-Agent": "Snatchr"})
    buf = io.BytesIO()
    with urllib.request.urlopen(req, timeout=120) as resp:
        total = int(resp.headers.get("Content-Length") or 0)
        done = 0
        while True:
            chunk = resp.read(1024 * 64)
            if not chunk:
                break
            buf.write(chunk)
            done += len(chunk)
            if progress and total:
                progress(done / total)
    with zipfile.ZipFile(buf) as zf:
        for name in zf.namelist():
            base = os.path.basename(name)
            if base in ("ffmpeg.exe", "ffprobe.exe"):
                with zf.open(name) as src, open(os.path.join(TOOLS_DIR, base), "wb") as dst:
                    dst.write(src.read())


def update_ffmpeg(progress=None):
    """Переустановить ffmpeg+ffprobe: удалить текущие exe и скачать заново."""
    for p in (FFMPEG_EXE, FFPROBE_EXE):
        try:
            if os.path.isfile(p):
                os.remove(p)
        except OSError:
            pass
    download_ffmpeg(progress)
    return True


def ffmpeg_location():
    """Папка с ffmpeg/ffprobe для передачи в yt-dlp (--ffmpeg-location)."""
    return TOOLS_DIR if have_ffmpeg() else None
