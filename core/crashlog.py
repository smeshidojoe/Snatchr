"""
Отчёты о падениях: %APPDATA%/Snatchr/crash-reports/crash-<дата>.log

Зачем: в собранном .exe stdout никуда не ведёт, поэтому необработанное
исключение исчезало бесследно — окно просто закрывалось, и юзеру нечего было
переслать. Ставим три перехватчика (main.install):

  * sys.excepthook          — главный (GUI) поток;
  * threading.excepthook    — обычные потоки (например, проверка обновлений);
  * QThread.run             — воркеры движка: PySide проглатывает исключение
                              внутри run(), поток молча умирает и загрузка
                              «зависает» навсегда без единой записи.

Отчёт содержит traceback и окружение (версии, ОС, тема/язык). URL пишем без
query-строки — в ней бывают токены доступа.
"""

import os
import sys
import time
import threading
import traceback

from core.config import APP_DIR

CRASH_DIR = os.path.join(APP_DIR, "crash-reports")

# Сколько держим отчёты (как и обычные логи — разбираются по свежим следам,
# но даём больше запаса: падение могут заметить не сразу).
CRASH_TTL = 14 * 24 * 3600

_installed = False


def _safe(fn, default=""):
    try:
        return fn()
    except Exception:
        return default


def _environment():
    """Строки окружения для шапки отчёта (каждая обёрнута — сбор данных сам
    не должен уронить запись отчёта)."""
    lines = []
    from core.constants import APP_NAME, APP_VERSION
    lines.append("%s %s" % (APP_NAME, APP_VERSION))
    lines.append("Python: %s" % sys.version.split()[0])
    lines.append("Frozen: %s" % bool(getattr(sys, "frozen", False)))
    lines.append("Platform: %s" % _safe(lambda: __import__("platform").platform()))
    lines.append("Qt: %s" % _safe(
        lambda: __import__("PySide6.QtCore", fromlist=["qVersion"]).qVersion()))

    def _settings_line():
        from core import config
        s = config.load()
        return "Theme: %s | Language: %s | Parallel: %s | Limit: %s MB/s" % (
            s.get("theme"), s.get("language"),
            s.get("parallel_downloads"), s.get("speed_limit_mbps"))

    lines.append(_safe(_settings_line))

    def _tools_line():
        from core import tools
        return "yt-dlp: %s | ffmpeg: %s" % (
            bool(tools.have_ytdlp()), bool(tools.have_ffmpeg()))

    lines.append(_safe(_tools_line))
    return [l for l in lines if l]


def save(exc_type, exc, tb, context=""):
    """Пишет отчёт о падении. Возвращает путь к файлу (или '')."""
    try:
        os.makedirs(CRASH_DIR, exist_ok=True)
        # Каскад падений (упал воркер -> следом поток) укладывается в одну
        # секунду, поэтому к отметке времени добавляем миллисекунды: иначе
        # второй отчёт затёр бы первый.
        stamp = "%s-%03d" % (time.strftime("%Y%m%d-%H%M%S"),
                             int(time.time() * 1000) % 1000)
        path = os.path.join(CRASH_DIR, "crash-%s.log" % stamp)
        body = ["=" * 60]
        body += _environment()
        if context:
            body.append("Context: %s" % context)
        body.append("Thread: %s" % threading.current_thread().name)
        body.append("=" * 60)
        body.append("")
        body.append("".join(traceback.format_exception(exc_type, exc, tb)))
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(body))
        return path
    except Exception:
        return ""            # отчёт о падении не имеет права падать сам


def install():
    """Ставит перехватчики. Вызывать один раз, до создания QApplication."""
    global _installed
    if _installed:
        return
    _installed = True

    def _hook(exc_type, exc, tb):
        save(exc_type, exc, tb)
        sys.__excepthook__(exc_type, exc, tb)

    sys.excepthook = _hook

    def _thread_hook(args):
        # SystemExit при штатном завершении потока падением не считаем.
        if args.exc_type is SystemExit:
            return
        save(args.exc_type, args.exc_value, args.exc_traceback,
             context="thread=%s" % getattr(args.thread, "name", "?"))

    if hasattr(threading, "excepthook"):
        threading.excepthook = _thread_hook

    _patch_qthread()


def _patch_qthread():
    """Оборачивает QThread.run: исключение внутри воркера иначе теряется."""
    try:
        from PySide6.QtCore import QThread
    except Exception:
        return
    if getattr(QThread, "_snatchr_crash_wrapped", False):
        return

    def _wrap(cls):
        original = cls.run

        def run(self):
            try:
                original(self)
            except Exception:
                save(*sys.exc_info(), context="worker=%s" % type(self).__name__)
                raise

        run.__name__ = "run"
        cls.run = run
        cls._snatchr_crash_wrapped = True

    # Патчим сами подклассы: базовый QThread.run переопределён у каждого из них,
    # поэтому обёртка на базовом классе до них бы не дошла.
    try:
        from core import workers
        for name in dir(workers):
            obj = getattr(workers, name)
            if (isinstance(obj, type) and issubclass(obj, QThread)
                    and obj is not QThread and "run" in obj.__dict__):
                _wrap(obj)
    except Exception:
        pass


def cleanup(ttl=CRASH_TTL):
    """Удаляет отчёты старше ttl секунд. Возвращает, сколько удалено."""
    removed = 0
    try:
        now = time.time()
        for name in os.listdir(CRASH_DIR):
            if not name.endswith(".log"):
                continue
            p = os.path.join(CRASH_DIR, name)
            try:
                if now - os.path.getmtime(p) > ttl:
                    os.remove(p)
                    removed += 1
            except OSError:
                pass
    except OSError:
        pass
    return removed


def latest():
    """Путь к самому свежему отчёту (или '') — для кнопки «показать»."""
    try:
        files = [os.path.join(CRASH_DIR, n) for n in os.listdir(CRASH_DIR)
                 if n.endswith(".log")]
        return max(files, key=os.path.getmtime) if files else ""
    except OSError:
        return ""
