"""
Замер времени UI-операций для поиска подтормаживаний.

Выключен по умолчанию. Включается переменной окружения SNATCHR_PERF=1 —
конфиг не трогаем, в обычной сборке кода как будто нет.

Пишет в %APPDATA%/Snatchr/logs/perf-<дата>.log только операции дольше порога,
чтобы лог не заплывал шумом.

    from core import perflog
    with perflog.measure("rebuild history", rows=len(entries)):
        ...
"""

import os
import time

from core.config import APP_DIR

ENABLED = os.environ.get("SNATCHR_PERF", "") not in ("", "0", "false", "False")
SLOW_MS = float(os.environ.get("SNATCHR_PERF_MS", "30"))   # порог записи

_LOG_DIR = os.path.join(APP_DIR, "logs")
_path = None
_t0 = time.perf_counter()


def _file():
    global _path
    if _path is None:
        os.makedirs(_LOG_DIR, exist_ok=True)
        _path = os.path.join(
            _LOG_DIR, "perf-%s.log" % time.strftime("%Y%m%d-%H%M%S"))
        with open(_path, "w", encoding="utf-8") as f:
            f.write("Snatchr perf log (порог %.0f мс)\n" % SLOW_MS)
    return _path


def note(text):
    """Пишет строку в лог (если замер включён)."""
    if not ENABLED:
        return
    try:
        with open(_file(), "a", encoding="utf-8") as f:
            f.write("[%8.3f] %s\n" % (time.perf_counter() - _t0, text))
    except OSError:
        pass


# --- сторож цикла событий -------------------------------------------- #
# Таймер тикает каждые 4 мс. Если между тиками прошло заметно больше, значит
# поток UI был чем-то занят. Если пропусков нет, а картинка всё равно дёргается,
# виновата не наша логика, а отрисовка/композиция окна.
_watch = None
STALL_MS = float(os.environ.get("SNATCHR_STALL_MS", "20"))


def watch_loop(parent=None):
    """Запускает сторож задержек цикла событий (только при SNATCHR_PERF=1)."""
    global _watch
    if not ENABLED or _watch is not None:
        return
    from PySide6.QtCore import QTimer, Qt
    last = [time.perf_counter()]

    def tick():
        now = time.perf_counter()
        gap = (now - last[0]) * 1000.0
        last[0] = now
        if gap >= STALL_MS:
            note("%7.1f мс  ПРОПУСК цикла событий%s" % (gap, _tally_tail()))

    _watch = QTimer(parent)
    _watch.setTimerType(Qt.PreciseTimer)
    _watch.setInterval(4)
    _watch.timeout.connect(tick)
    _watch.start()
    _start_watchdog(last)


HANG_MS = float(os.environ.get("SNATCHR_HANG_MS", "300"))


def _start_watchdog(last):
    """Отдельный поток: если главный не тикал дольше HANG_MS — пишет его стек.

    Пропуск цикла событий говорит ЧТО поток встал, но не ГДЕ. Дамп из соседнего
    потока показывает точную строку, на которой главный стоит прямо сейчас.
    """
    import threading
    import faulthandler

    def run():
        while True:
            time.sleep(0.05)
            if (time.perf_counter() - last[0]) * 1000.0 < HANG_MS:
                continue
            try:
                with open(_file(), "a", encoding="utf-8") as f:
                    f.write("[%8.3f] === главный поток стоит, стек: ===\n"
                            % (time.perf_counter() - _t0))
                    faulthandler.dump_traceback(file=f, all_threads=True)
                    f.write("=== конец стека ===\n")
            except (OSError, RuntimeError):
                pass
            while (time.perf_counter() - last[0]) * 1000.0 >= HANG_MS:
                time.sleep(0.05)          # ждём, пока отвиснет — без спама

    threading.Thread(target=run, daemon=True).start()


# --- накопительный учёт частых операций ------------------------------- #
# Отрисовку строки мерить по одной бессмысленно (доли миллисекунды), но за кадр
# их десятки. Копим счёт и время, и выводим вместе с ближайшим пропуском.
_tally = {}


def tally(key, ms):
    if not ENABLED:
        return
    n, total, peak = _tally.get(key, (0, 0.0, 0.0))
    _tally[key] = (n + 1, total + ms, max(peak, ms))


def _tally_tail():
    if not _tally:
        return ""
    parts = ["%s: %d шт / %.1f мс / макс %.1f" % (k, n, t, pk)
             for k, (n, t, pk) in _tally.items()]
    _tally.clear()
    return "  [" + ", ".join(parts) + "]"


class measure:
    """Контекст: пишет строку, если операция заняла больше порога."""

    def __init__(self, label, **extra):
        self.label = label
        self.extra = extra
        self.t = 0.0

    def __enter__(self):
        if ENABLED:
            self.t = time.perf_counter()
        return self

    def __exit__(self, *exc):
        if not ENABLED:
            return False
        ms = (time.perf_counter() - self.t) * 1000.0
        if ms >= SLOW_MS:
            tail = " ".join("%s=%s" % kv for kv in self.extra.items())
            note("%7.1f мс  %s %s" % (ms, self.label, tail))
        return False
