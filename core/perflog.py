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
