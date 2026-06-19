"""
Понятный человеку лог загрузки. Заголовочные строки переводятся на язык
программы (i18n.tr), вывод утилит добавляется как есть. При ошибке лог можно
сохранить на рабочий стол.
"""

import os
import time

from core import i18n


class Log:
    def __init__(self, url=""):
        self._lines = []
        self.event("Snatchr download log")
        if url:
            self.info(f"URL: {url}")

    def _stamp(self):
        return time.strftime("%H:%M:%S")

    def event(self, key):
        """Ключевое событие (переводится)."""
        self._lines.append(f"[{self._stamp()}] {i18n.tr(key)}")

    def info(self, text):
        self._lines.append(f"[{self._stamp()}] {text}")

    def raw(self, text):
        """Сырая строка вывода утилиты (без перевода)."""
        if text:
            self._lines.append(text)

    def text(self):
        return "\n".join(self._lines)

    def save_error(self):
        """Сохраняет лог на рабочий стол; возвращает путь (или '')."""
        base = os.path.join(os.path.expanduser("~"), "Desktop")
        if not os.path.isdir(base):
            base = os.path.expanduser("~")
        path = os.path.join(base, "Snatchr-error-" + time.strftime("%Y%m%d-%H%M%S") + ".log")
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(self.text())
            return path
        except OSError:
            return ""
