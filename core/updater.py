"""
Самообновление приложения через релизы GitHub.

Поток:
  1. check_update()        — есть ли релиз новее текущего (с zip-ассетом).
  2. download_update(url)  — качаем zip в APP_DIR/_update/update.zip.
  3. restart_to_update()   — закрываем приложение и запускаем внешний помощник
     (PowerShell), который дожидается выхода процесса, распаковывает zip поверх
     папки установки и заново запускает .exe. Так можно заменить и сам exe,
     который во время работы заблокирован.
  4. apply_pending_update()— страховка при старте: если zip остался (помощник не
     отработал), распаковываем то, что не заблокировано.

Только stdlib (urllib) — чтобы ничего лишнего не тащить в сборку.
"""

import os
import sys
import shutil
import zipfile
import subprocess
import urllib.request

from core.constants import APP_VERSION, GITHUB_REPO, APP_NAME

API_URL = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"

# Флаги запуска отвязанного процесса (Windows): переживает выход приложения.
_DETACHED = 0x00000008 | 0x00000200   # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP


# ------------------------------------------------------------------ #
def is_frozen():
    return bool(getattr(sys, "frozen", False))


def install_dir():
    """Папка установки (где лежит exe). В режиме разработки — корень проекта."""
    if is_frozen():
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# Папку обновления держим рядом с программой (в папке установки).
UPDATE_DIR = os.path.join(install_dir(), "_update")
UPDATE_ZIP = os.path.join(UPDATE_DIR, "update.zip")


def _parse(v):
    """'v1.2.3' -> (1, 2, 3); нечисловые части -> 0."""
    out = []
    for part in (v or "").lstrip("vV").split("."):
        num = "".join(c for c in part if c.isdigit())
        out.append(int(num) if num else 0)
    return tuple(out) or (0,)


def _is_newer(remote, current):
    return _parse(remote) > _parse(current)


# ------------------------------------------------------------------ #
def check_update(timeout=8):
    """
    Возвращает dict:
      {"status": "available"|"current"|"error",
       "version": <tag>, "download_url": <zip|None>, "notes": <str>, "error": <str>}
    """
    req = urllib.request.Request(
        API_URL,
        headers={"User-Agent": APP_NAME, "Accept": "application/vnd.github+json"})
    try:
        import json
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.load(resp)
    except Exception as exc:
        return {"status": "error", "error": str(exc)}

    tag = data.get("tag_name") or ""
    if not tag:
        return {"status": "error", "error": "no releases"}

    if not _is_newer(tag, APP_VERSION):
        return {"status": "current", "version": tag}

    zip_url = None
    for a in data.get("assets", []):
        if (a.get("name") or "").endswith(".zip"):
            zip_url = a.get("browser_download_url")
            break

    return {"status": "available", "version": tag,
            "download_url": zip_url, "notes": data.get("body", "")}


def download_update(url, on_progress=None, timeout=60):
    """Скачивает zip обновления в UPDATE_ZIP. on_progress(frac 0..1). Бросает при сбое."""
    if not url:
        raise RuntimeError("no download url")
    os.makedirs(UPDATE_DIR, exist_ok=True)
    req = urllib.request.Request(url, headers={"User-Agent": APP_NAME})
    tmp = UPDATE_ZIP + ".part"
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        total = int(resp.headers.get("Content-Length") or 0)
        done = 0
        with open(tmp, "wb") as f:
            while True:
                chunk = resp.read(1024 * 64)
                if not chunk:
                    break
                f.write(chunk)
                done += len(chunk)
                if on_progress and total:
                    on_progress(done / total)
    os.replace(tmp, UPDATE_ZIP)
    return True


def has_pending_update():
    return os.path.isfile(UPDATE_ZIP)


def apply_pending_update(target=None):
    """Страховка при старте: распаковать оставшийся zip поверх папки установки.
    Заблокированные файлы (сам exe) заменить не сможет — это делает помощник."""
    if not has_pending_update():
        return False
    target = target or install_dir()
    try:
        with zipfile.ZipFile(UPDATE_ZIP, "r") as zf:
            zf.extractall(target)
        os.remove(UPDATE_ZIP)
        return True
    except Exception:
        return False


def _ps_lit(p):
    # Строковый литерал PowerShell: одинарные кавычки, апостроф удваивается
    # (путь C:\Users\O'Brien\… не должен ломать разбор).
    return "'" + str(p).replace("'", "''") + "'"


NEW_EXE = os.path.join(UPDATE_DIR, "Snatchr-new.exe")


def _extract_new_exe():
    """Достаёт наш exe из скачанного zip в _update/Snatchr-new.exe. Делаем это,
    пока приложение ещё работает: файл новый, блокировок нет. На любой глубине
    архива; иначе — первый .exe. Возвращает путь или None."""
    if not has_pending_update():
        return None
    try:
        want = os.path.basename(sys.executable).lower()   # напр. snatchr.exe
        with zipfile.ZipFile(UPDATE_ZIP, "r") as zf:
            names = zf.namelist()
            pick = next((n for n in names if os.path.basename(n).lower() == want), None)
            if pick is None:
                pick = next((n for n in names if n.lower().endswith(".exe")), None)
            if pick is None:
                return None
            with zf.open(pick) as src, open(NEW_EXE, "wb") as out:
                shutil.copyfileobj(src, out)
        return NEW_EXE
    except Exception:
        return None


def restart_to_update():
    """Готовит новый exe и запускает помощника (PowerShell), который ждёт, пока
    ТЕКУЩИЙ процесс полностью завершится (пока exe нельзя удалить), подменяет exe
    и запускает новый. После вызова приложение обязано немедленно завершиться
    (os._exit) — иначе onefile-процесс продолжает держать файл. Только frozen."""
    if not is_frozen() or not has_pending_update():
        return False
    new_exe = _extract_new_exe()
    if not new_exe or not os.path.isfile(new_exe):
        return False
    # zip больше не нужен (exe уже извлечён) — убираем, чтобы не мешал.
    try:
        os.remove(UPDATE_ZIP)
    except OSError:
        pass

    log = os.path.join(UPDATE_DIR, "helper.log")
    # Скрипт с путями-литералами (устойчив к Юникоду в путях). Цикл: пробуем
    # удалить старый exe — получится только когда процесс реально вышел; затем
    # копируем новый на его место и запускаем.
    script = (
        "$ErrorActionPreference='SilentlyContinue';\n"
        f"$old={_ps_lit(sys.executable)}; $new={_ps_lit(new_exe)}; $log={_ps_lit(log)};\n"
        "function L($m){ ('['+(Get-Date -Format o)+'] '+$m) | Out-File -LiteralPath $log -Append -Encoding utf8 }\n"
        "L('helper started')\n"
        "$gone=$false\n"
        "for($i=0;$i -lt 150;$i++){\n"
        "  try{ Remove-Item -LiteralPath $old -Force -ErrorAction Stop; $gone=$true; break }\n"
        "  catch{ Start-Sleep -Milliseconds 400 }\n"
        "}\n"
        "L('old removed='+$gone)\n"
        "try{ Copy-Item -LiteralPath $new -Destination $old -Force -ErrorAction Stop }\n"
        "catch{ L('copy err: '+$_.Exception.Message) }\n"
        "if(-not (Test-Path -LiteralPath $old)){ try{ Move-Item -LiteralPath $new -Destination $old -Force }catch{} }\n"
        "Start-Process -FilePath $old\n"
        "Remove-Item -LiteralPath $new -Force -ErrorAction SilentlyContinue\n"
        "L('done')\n"
    )
    try:
        os.makedirs(UPDATE_DIR, exist_ok=True)
        # -EncodedCommand (base64 UTF-16LE) полностью обходит проблемы кавычек и
        # кодировок командной строки — важно для путей с кириллицей.
        import base64
        enc = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
        subprocess.Popen(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
             "-WindowStyle", "Hidden", "-EncodedCommand", enc],
            creationflags=_DETACHED, close_fds=True)
        return True
    except Exception:
        return False
