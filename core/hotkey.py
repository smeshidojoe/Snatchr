"""
Глобальный хоткей (Ctrl+Shift+D) для вызова окна Spotlight.

Библиотека `keyboard` ставит низкоуровневый хук и вызывает колбэк в СВОЁМ потоке,
поэтому в UI-поток мы уходим через Qt-сигнал (очередь). Если хук не установился
(нет прав/библиотеки) — просто молчим, приложение работает как обычно.
"""

from PySide6.QtCore import QObject, Signal


class HotkeyManager(QObject):
    triggered = Signal()

    def __init__(self, combo="ctrl+shift+d", parent=None):
        super().__init__(parent)
        self._combo = combo
        self._kb = None
        self._handle = None

    def start(self):
        try:
            import keyboard
            self._kb = keyboard
            # emit из фонового потока -> слот в UI-потоке (авто-queued).
            self._handle = keyboard.add_hotkey(self._combo,
                                               lambda: self.triggered.emit())
            return True
        except Exception:
            return False

    def stop(self):
        try:
            if self._kb is not None and self._handle is not None:
                self._kb.remove_hotkey(self._handle)
        except Exception:
            pass
        self._handle = None
