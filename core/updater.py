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


def _ps_quote(p):
    # Одинарные кавычки в PowerShell экранируются удвоением апострофа,
    # иначе путь с U+0027 (C:\Users\O'Brien\…) ломает разбор команды.
    return "'" + str(p).replace("'", "''") + "'"


def restart_to_update():
    """Запускает внешнего помощника (PowerShell): он ждёт, пока exe реально
    разблокируется (в onefile файл держат и загрузчик, и сам процесс),
    распаковывает zip поверх папки установки и перезапускает exe. Пишет лог в
    _update/helper.log. Работает только во frozen-режиме."""
    if not is_frozen() or not has_pending_update():
        return False

    exe_q = _ps_quote(sys.executable)
    zip_q = _ps_quote(UPDATE_ZIP)
    target_q = _ps_quote(install_dir())
    log_q = _ps_quote(os.path.join(UPDATE_DIR, "helper.log"))

    # Ждём разблокировки exe, распаковываем zip во временную папку, находим наш
    # exe на любой глубине (устойчиво к архиву, где файлы лежат в подпапке) и
    # копируем его вместе с соседями в папку установки. Все шаги — в helper.log.
    script = (
        "$ErrorActionPreference='SilentlyContinue'; "
        f"$exe={exe_q}; $zip={zip_q}; $target={target_q}; $log={log_q}; "
        "$name=[System.IO.Path]::GetFileName($exe); "
        "function L($m){ ('[' + (Get-Date -Format o) + '] ' + $m) | Out-File -FilePath $log -Append -Encoding utf8 }; "
        "L('helper started; exe=' + $exe); "
        "$ok=$false; "
        "for($i=0;$i -lt 120;$i++){ "
        "  try{ $fs=[System.IO.File]::Open($exe,'Open','ReadWrite','None'); $fs.Close(); $ok=$true; break } "
        "  catch{ Start-Sleep -Milliseconds 500 } "
        "} "
        "L('exe unlocked=' + $ok); "
        "Start-Sleep -Milliseconds 400; "
        "$tmp=Join-Path $env:TEMP ('snatchr_upd_' + [Guid]::NewGuid().ToString('N')); "
        "try{ "
        "  Expand-Archive -LiteralPath $zip -DestinationPath $tmp -Force; "
        "  L('extracted to ' + $tmp); "
        "  $src=Get-ChildItem -LiteralPath $tmp -Recurse -Filter $name | Select-Object -First 1; "
        "  if($src){ "
        "    L('found ' + $name + ' in ' + $src.Directory.FullName); "
        "    Copy-Item -Path (Join-Path $src.Directory.FullName '*') -Destination $target -Recurse -Force; "
        "    L('copied into ' + $target); "
        "  } else { L('ERROR: ' + $name + ' not found in archive') } "
        "}catch{ L('ERROR: ' + $_.Exception.Message) } "
        "try{ Remove-Item -LiteralPath $zip -Force }catch{}; "
        "try{ Remove-Item -LiteralPath $tmp -Recurse -Force }catch{}; "
        "Start-Process -FilePath $exe; "
        "L('relaunched')"
    )
    try:
        os.makedirs(UPDATE_DIR, exist_ok=True)
        subprocess.Popen(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
             "-WindowStyle", "Hidden", "-Command", script],
            creationflags=_DETACHED, close_fds=True)
        return True
    except Exception:
        return False
