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


def _log(msg):
    """Пишет строку в _update/helper.log (для диагностики обновления)."""
    try:
        import datetime
        os.makedirs(UPDATE_DIR, exist_ok=True)
        with open(os.path.join(UPDATE_DIR, "helper.log"), "a", encoding="utf-8") as f:
            f.write("[%s] %s\n"
                    % (datetime.datetime.now().isoformat(timespec="seconds"), msg))
    except Exception:
        pass


def apply_self_update(target):
    """Выполняется НОВЫМ exe (Snatchr-new.exe), запущенным с --apply-update: ждёт,
    пока старый target (текущий exe) освободится, подменяет его собой и запускает.
    Полностью на Python — надёжно и без проблем с Юникодом в путях."""
    import time
    src = sys.executable
    if not target or not os.path.isfile(src):
        return False
    ok = False
    for _ in range(200):                 # до ~80 c ждём выхода старого процесса
        try:
            shutil.copyfile(src, target)   # target разблокируется после выхода старого
            ok = True
            break
        except (PermissionError, OSError):
            time.sleep(0.4)
    _log("self-apply copy ok=%s -> %s" % (ok, target))
    # Запускаем целевой exe (обновлённый при успехе; старый — чтобы юзер не остался
    # без программы, если подмена не удалась).
    try:
        subprocess.Popen([target], creationflags=_DETACHED, close_fds=True,
                         cwd=os.path.dirname(target) or None)
    except Exception as exc:
        _log("relaunch err: %s" % exc)
    return ok


def cleanup_applied():
    """При старте убирает Snatchr-new.exe, если он уже совпадает с текущим exe
    (обновление применилось). Иначе оставляем — можно повторить."""
    if not is_frozen() or not os.path.isfile(NEW_EXE):
        return
    try:
        if os.path.getsize(NEW_EXE) == os.path.getsize(sys.executable):
            os.remove(NEW_EXE)
    except OSError:
        pass


def restart_to_update():
    """Готовит новый exe и запускает подмену. Основной способ — сам новый exe с
    флагом --apply-update (полный путь, без зависимости от PowerShell); фолбэк —
    помощник PowerShell. После вызова приложение обязано немедленно завершиться
    (os._exit), чтобы освободить свой exe-файл. Только frozen."""
    if not is_frozen() or not has_pending_update():
        return False
    new_exe = _extract_new_exe()
    if not new_exe or not os.path.isfile(new_exe):
        _log("restart_to_update: no new exe extracted")
        return False
    # zip больше не нужен (exe уже извлечён) — убираем, чтобы не мешал.
    try:
        os.remove(UPDATE_ZIP)
    except OSError:
        pass

    # Основной способ: новый exe сам себя применяет (запуск по полному пути).
    try:
        subprocess.Popen([new_exe, "--apply-update", sys.executable],
                         creationflags=_DETACHED, close_fds=True)
        _log("launched self-apply helper: %s" % new_exe)
        return True
    except Exception as exc:
        _log("self-apply launch failed: %s — fallback to PowerShell" % exc)

    return _restart_via_powershell(new_exe)


def _restart_via_powershell(new_exe):
    """Фолбэк-помощник на PowerShell: ждёт освобождения exe, копирует новый и
    запускает."""
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
        # Полный путь к powershell.exe — не полагаемся на PATH (частая причина сбоя).
        ps = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"),
                          "System32", "WindowsPowerShell", "v1.0", "powershell.exe")
        if not os.path.isfile(ps):
            ps = "powershell"
        subprocess.Popen(
            [ps, "-NoProfile", "-ExecutionPolicy", "Bypass",
             "-WindowStyle", "Hidden", "-EncodedCommand", enc],
            creationflags=_DETACHED, close_fds=True)
        _log("launched PowerShell helper")
        return True
    except Exception as exc:
        _log("PowerShell helper launch failed: %s" % exc)
        return False


def relaunch_app():
    """Перезапуск ТОГО ЖЕ приложения (без обновления). Отвязанный помощник ждёт
    выхода текущего процесса по PID (иначе новый экземпляр упрётся в single-
    instance-мьютекс), затем стартует exe заново. Вызывающий обязан сразу выйти
    (os._exit) после вызова. Возвращает True при успешном запуске помощника."""
    exe = os.path.abspath(sys.executable)
    pid = os.getpid()
    if is_frozen():
        script = (
            "$ErrorActionPreference='SilentlyContinue';\n"
            f"$exe={_ps_lit(exe)}; $procId={pid};\n"
            "try{ Wait-Process -Id $procId -Timeout 30 }catch{}\n"
            "Start-Process -FilePath $exe\n"
        )
        try:
            import base64
            enc = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
            ps = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"),
                              "System32", "WindowsPowerShell", "v1.0", "powershell.exe")
            if not os.path.isfile(ps):
                ps = "powershell"
            subprocess.Popen(
                [ps, "-NoProfile", "-ExecutionPolicy", "Bypass",
                 "-WindowStyle", "Hidden", "-EncodedCommand", enc],
                creationflags=_DETACHED, close_fds=True)
            _log("relaunch_app: helper launched")
            return True
        except Exception as exc:
            _log("relaunch_app failed: %s" % exc)
            return False
    # dev-режим: перезапуск интерпретатора с теми же аргументами (мьютекс-гонку
    # игнорируем — фича в основном для собранного exe).
    try:
        subprocess.Popen([sys.executable] + sys.argv, close_fds=True)
        return True
    except Exception:
        return False
