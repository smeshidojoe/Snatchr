"""
Понятный человеку лог загрузки. Заголовочные строки переводятся на язык
программы (i18n.tr), вывод утилит добавляется как есть. При ошибке лог можно
сохранить в %APPDATA%/Snatchr/logs.
"""

import os
import time

from core import i18n
from core.config import APP_DIR

LOG_DIR = os.path.join(APP_DIR, "logs")


class Log:
    def __init__(self, url=""):
        self._lines = []
        self._path = None            # файл на диске (после begin())
        self.event("Snatchr download log")
        if url:
            self.info(f"URL: {url}")

    def _stamp(self):
        return time.strftime("%H:%M:%S")

    def begin(self, prefix="download"):
        """Начинает писать лог НА ДИСК сразу.

        Раньше файл появлялся только в конце — и если загрузка падала посреди
        (например, из-за ошибки в самом коде), от неё не оставалось следа.
        Теперь строки уходят в файл по мере появления, а при успехе он
        удаляется (discard) — на диске остаются только проблемные."""
        try:
            os.makedirs(LOG_DIR, exist_ok=True)
            self._path = os.path.join(
                LOG_DIR, "%s-%s.log" % (prefix, time.strftime("%Y%m%d-%H%M%S")))
            with open(self._path, "w", encoding="utf-8") as f:
                f.write(self.text() + chr(10))
        except OSError:
            self._path = None
        return self._path

    def discard(self):
        """Успешное задание — лог не нужен."""
        try:
            if self._path and os.path.isfile(self._path):
                os.remove(self._path)
        except OSError:
            pass
        self._path = None

    def _append(self, line):
        """Дописывает строку в уже открытый файл (если begin() был вызван)."""
        if not self._path:
            return
        try:
            with open(self._path, "a", encoding="utf-8") as f:
                f.write(line + chr(10))
        except OSError:
            pass

    def event(self, key):
        """Ключевое событие (переводится)."""
        line = f"[{self._stamp()}] {i18n.tr(key)}"
        self._lines.append(line)
        self._append(line)

    def info(self, text):
        line = f"[{self._stamp()}] {text}"
        self._lines.append(line)
        self._append(line)

    def raw(self, text):
        """Сырая строка вывода утилиты (без перевода)."""
        if text:
            self._lines.append(text)
            self._append(text)

    def text(self):
        return "\n".join(self._lines)

    def save_error(self):
        """Сохраняет лог неудачи; возвращает путь (или '')."""
        return self._save("error")

    def save(self, kind="download"):
        """Сохраняет лог УСПЕШНОЙ операции (её тоже полезно видеть постфактум:
        какой движок сработал, был ли повтор, куда делось время)."""
        return self._save(kind)

    def _save(self, prefix):
        try:
            os.makedirs(LOG_DIR, exist_ok=True)
            # Файл уже пишется с начала задания — просто оставляем его.
            if self._path and os.path.isfile(self._path):
                return self._path
            path = os.path.join(
                LOG_DIR, "%s-%s.log" % (prefix, time.strftime("%Y%m%d-%H%M%S")))
            with open(path, "w", encoding="utf-8") as f:
                f.write(self.text())
            return path
        except OSError:
            return ""


# Сколько держим логи. Чистим при старте: папка не должна расти бесконечно,
# а разбираться в проблеме обычно нужно по свежим следам.
LOG_TTL = 3 * 24 * 3600


def cleanup_logs(ttl=LOG_TTL):
    """Удаляет логи старше ttl секунд. Возвращает, сколько удалено."""
    removed = 0
    try:
        now = time.time()
        for name in os.listdir(LOG_DIR):
            if not name.endswith(".log"):
                continue
            p = os.path.join(LOG_DIR, name)
            try:
                if now - os.path.getmtime(p) > ttl:
                    os.remove(p)
                    removed += 1
            except OSError:
                pass
    except OSError:
        pass
    return removed
