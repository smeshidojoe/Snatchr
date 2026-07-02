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
DENO_EXE      = os.path.join(TOOLS_DIR, "deno.exe")

YTDLP_URL  = "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp.exe"
# Каналы yt-dlp: стабильный и nightly (свежие фиксы YouTube приезжают раньше).
YTDLP_URLS = {
    "stable":  YTDLP_URL,
    "nightly": "https://github.com/yt-dlp/yt-dlp-nightly-builds/releases/latest/download/yt-dlp.exe",
}
# Deno — JS-движок, которым yt-dlp решает JS-челленджи YouTube (nsig). Кладём его
# в tools/ и добавляем в PATH — yt-dlp сам подхватывает deno/node из PATH. Без
# него yt-dlp откатывается на встроенный интерпретатор (медленнее, иногда падает).
DENO_URL   = ("https://github.com/denoland/deno/releases/latest/download/"
              "deno-x86_64-pc-windows-msvc.zip")
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


def have_deno():
    return os.path.isfile(DENO_EXE)


def default_browser():
    """Основной браузер пользователя как имя для yt-dlp --cookies-from-browser
    (chrome/edge/firefox/brave/opera/vivaldi) или None, если не определили."""
    try:
        import winreg
        key = (r"Software\Microsoft\Windows\Shell\Associations"
               r"\UrlAssociations\https\UserChoice")
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as k:
            progid = (winreg.QueryValueEx(k, "ProgId")[0] or "").lower()
    except Exception:
        return None
    for needle, name in (("brave", "brave"), ("firefox", "firefox"),
                         ("msedge", "edge"), ("edge", "edge"),
                         ("opera", "opera"), ("vivaldi", "vivaldi"),
                         ("chromium", "chromium"), ("chrome", "chrome")):
        if needle in progid:
            return name
    return None


def windows_uses_light_theme():
    """Светлая ли тема панели задач Windows (для цвета иконки в трее).
    True — светлая панель (иконки должны быть чёрными), False — тёмная (белыми).
    Ключ SystemUsesLightTheme отвечает именно за панель задач/трей."""
    try:
        import winreg
        key = r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as k:
            return bool(winreg.QueryValueEx(k, "SystemUsesLightTheme")[0])
    except Exception:
        return False   # по умолчанию — тёмная панель (белые иконки, как раньше)


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
    Windows кириллица в путях приходит в cp1251 и ломается при декодировании.
    Плюс кладём tools/ в PATH — чтобы yt-dlp нашёл deno (JS-челленджи YouTube)."""
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    env["PATH"] = TOOLS_DIR + os.pathsep + env.get("PATH", "")
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


def _channel_cache(channel):
    """Путь к кэшу бинаря канала (yt-dlp-stable.exe / yt-dlp-nightly.exe)."""
    return os.path.join(TOOLS_DIR, f"yt-dlp-{channel}.exe")


def download_ytdlp(progress=None, channel="stable"):
    """Скачать свежий yt-dlp выбранного канала, сделать активным (yt-dlp.exe)
    и сохранить копию в кэш канала (для мгновенного переключения обратно)."""
    url = YTDLP_URLS.get(channel) or YTDLP_URL
    _download(url, YTDLP_EXE, progress)
    try:
        shutil.copy2(YTDLP_EXE, _channel_cache(channel))
    except OSError:
        pass


def activate_ytdlp_channel(channel, progress=None):
    """Сделать активным бинарь выбранного канала: из кэша (быстро, без сети) или
    скачать, если кэша нет."""
    cache = _channel_cache(channel)
    if os.path.isfile(cache):
        shutil.copy2(cache, YTDLP_EXE)
    else:
        download_ytdlp(progress, channel)
    return True


def ensure_ytdlp(progress=None, on_status=None):
    """Проверка при запуске: если yt-dlp.exe нет — скачиваем его."""
    if not have_ytdlp():
        if on_status:
            on_status("Downloading yt-dlp…")
        download_ytdlp(progress)
    return YTDLP_EXE


def update_ytdlp(progress=None, channel="stable"):
    """
    Обновить yt-dlp свежей версией выбранного канала. Скачивание идёт в .part и
    атомарно заменяет exe (os.replace) — при сбое/офлайне старый файл остаётся.
    progress(frac) — колбэк хода скачивания (0..1). Бросает исключение при сбое.
    """
    download_ytdlp(progress, channel)
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


def download_deno(progress=None):
    """Скачать deno.exe (zip с GitHub) в tools/. progress(frac) — ход загрузки.
    Ошибки пробрасываются наверх (вызывающий делает best-effort)."""
    ensure_dir()
    req = urllib.request.Request(DENO_URL, headers={"User-Agent": "Snatchr"})
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
            if os.path.basename(name).lower() == "deno.exe":
                tmp = DENO_EXE + ".part"
                with zf.open(name) as src, open(tmp, "wb") as dst:
                    dst.write(src.read())
                os.replace(tmp, DENO_EXE)     # атомарно, чтобы не остался кусок
                return True
    raise RuntimeError("deno.exe not found in archive")


def ffmpeg_location():
    """Папка с ffmpeg/ffprobe для передачи в yt-dlp (--ffmpeg-location)."""
    return TOOLS_DIR if have_ffmpeg() else None


# ------------------------------------------------------------------ #
#  PO Token provider (обход YouTube «HTTP 403» на https/DASH-форматах)
# ------------------------------------------------------------------ #
# YouTube требует PO Token (Proof of Origin) для многих форматов — без него
# загрузка данных отдаёт 403. Плагин bgutil + генератор токенов на нашем deno
# (script mode). Всё держим под APP_DIR/pot, чтобы легко удалить, если сломается.
POT_VERSION     = "1.3.1"
POT_PLUGIN_URL  = ("https://github.com/Brainicism/bgutil-ytdlp-pot-provider/"
                   f"releases/download/{POT_VERSION}/bgutil-ytdlp-pot-provider.zip")
POT_SRC_URL     = ("https://github.com/Brainicism/bgutil-ytdlp-pot-provider/"
                   f"archive/refs/tags/{POT_VERSION}.tar.gz")
POT_DIR         = os.path.join(APP_DIR, "pot")
POT_PLUGINS_DIR = os.path.join(POT_DIR, "plugins")     # -> yt-dlp --plugin-dirs
POT_SERVER_DIR  = os.path.join(POT_DIR, "server")      # -> server_home
_POT_PLUGIN_PKG = os.path.join(POT_PLUGINS_DIR, "snatchr-pot",
                               "yt_dlp_plugins", "extractor")


def have_pot():
    """Готов ли PO-token провайдер (плагин + сервер + установленные node_modules)."""
    return (os.path.isfile(os.path.join(_POT_PLUGIN_PKG, "getpot_bgutil_script.py"))
            and os.path.isfile(os.path.join(POT_SERVER_DIR, "src", "generate_once.ts"))
            and os.path.isdir(os.path.join(POT_SERVER_DIR, "node_modules")))


def pot_ytdlp_args():
    """Аргументы yt-dlp для PO-token провайдера ([] если не готов или нет deno)."""
    if not (have_pot() and have_deno()):
        return []
    return ["--plugin-dirs", POT_PLUGINS_DIR,
            "--extractor-args", "youtubepot-bgutilscript:server_home=" + POT_SERVER_DIR]


def setup_pot(progress=None):
    """Одноразовая установка PO-token провайдера: плагин + сервер + `deno install`.
    Требует deno. Бросает исключение при сбое (вызывающий работает best-effort)."""
    import tarfile
    if not have_deno():
        raise RuntimeError("deno is required for the PO token provider")
    os.makedirs(POT_DIR, exist_ok=True)

    # 1) Плагин: берём только базовый и script-провайдер (http-провайдер не нужен —
    #    иначе каждый запрос сначала стучится на localhost:4416).
    plug_zip = os.path.join(POT_DIR, "plugin.zip")
    _download(POT_PLUGIN_URL, plug_zip)
    os.makedirs(_POT_PLUGIN_PKG, exist_ok=True)
    with zipfile.ZipFile(plug_zip) as zf:
        for name in zf.namelist():
            base = os.path.basename(name)
            if base in ("getpot_bgutil.py", "getpot_bgutil_script.py"):
                with zf.open(name) as s, open(os.path.join(_POT_PLUGIN_PKG, base), "wb") as d:
                    d.write(s.read())

    # 2) Сервер-генератор (src/generate_once.ts + package.json/deno.lock).
    src_tar = os.path.join(POT_DIR, "src.tar.gz")
    _download(POT_SRC_URL, src_tar)
    extract_dir = os.path.join(POT_DIR, "_extract")
    if os.path.isdir(extract_dir):
        shutil.rmtree(extract_dir, ignore_errors=True)
    with tarfile.open(src_tar) as tf:
        tf.extractall(extract_dir)
    root = next(os.scandir(extract_dir)).path            # bgutil-…-<ver>/
    if os.path.isdir(POT_SERVER_DIR):
        shutil.rmtree(POT_SERVER_DIR, ignore_errors=True)
    shutil.move(os.path.join(root, "server"), POT_SERVER_DIR)
    shutil.rmtree(extract_dir, ignore_errors=True)
    for tmp in (plug_zip, src_tar):
        try:
            os.remove(tmp)
        except OSError:
            pass

    # 3) Ставим npm-зависимости генератора нашим deno (без интерактива).
    r = subprocess.run([DENO_EXE, "install", "--quiet"], cwd=POT_SERVER_DIR,
                       capture_output=True, text=True, encoding="utf-8", errors="replace",
                       timeout=600, creationflags=CREATE_NO_WINDOW, env=_utf8_env())
    if not os.path.isdir(os.path.join(POT_SERVER_DIR, "node_modules")):
        raise RuntimeError("deno install failed: " + (r.stderr or "")[-300:])
    return True
