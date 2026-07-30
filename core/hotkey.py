"""
Глобальный хоткей (Ctrl+Shift+D) для вызова окна Spotlight.

Библиотека `keyboard` ставит низкоуровневый хук и вызывает колбэк в СВОЁМ потоке,
поэтому в UI-поток мы уходим через Qt-сигнал (очередь). Если хук не установился
(нет прав/библиотеки) — просто молчим, приложение работает как обычно.

Регистрация идёт в ФОНОВОМ потоке: измерено, что на главном она стоила ~296 мс
(`import keyboard` 114 мс + построение таблиц имён клавиш 182 мс, сама постановка
хука — 1.2 мс). Это была заметная доля паузы на старте, из-за которой первое
нажатие по иконке в трее открывало окно не сразу.

Системный RegisterHotKey был бы почти бесплатным (0.06 мс), но он ЭКСКЛЮЗИВНЫЙ:
если сочетание занято другой программой, регистрация не проходит. Низкоуровневый
хук срабатывает и в этом случае — поэтому оставлен он.
"""

import threading

from PySide6.QtCore import QObject, Signal


class HotkeyManager(QObject):
    triggered = Signal()

    def __init__(self, combo="ctrl+shift+d", parent=None):
        super().__init__(parent)
        self._combo = combo
        self._kb = None
        self._handle = None
        self._lock = threading.Lock()
        self._cancelled = False

    def start(self):
        """Ставит хук в фоне и сразу возвращает управление.

        Регистрация небыстрая, а мгновенно результат не нужен: между запуском
        приложения и первым нажатием сочетания всегда проходит куда больше.
        """
        threading.Thread(target=self._register, daemon=True).start()
        return True

    def _register(self):
        try:
            import keyboard
        except Exception:
            return
        with self._lock:
            if self._cancelled:
                return                    # stop() успел раньше — хук не ставим
            self._kb = keyboard
        try:
            # emit из фонового потока -> слот в UI-потоке (авто-queued).
            handle = keyboard.add_hotkey(self._combo, self._fired)
        except Exception:
            return
        with self._lock:
            if not self._cancelled:
                self._handle = handle
                return
        # Пока регистрировались, менеджер остановили (смена сочетания в
        # настройках) — снимаем хук сразу, иначе он остался бы висеть.
        try:
            keyboard.remove_hotkey(handle)
        except Exception:
            pass

    def _fired(self):
        """Колбэк библиотеки (её поток): отмечаем момент и уходим в UI-поток."""
        from core import perflog
        perflog.note("хоткей пойман (поток keyboard)")
        self.triggered.emit()

    def stop(self):
        with self._lock:
            self._cancelled = True        # ещё не поставленный хук не появится
            kb, handle = self._kb, self._handle
            self._handle = None
        try:
            if kb is not None and handle is not None:
                kb.remove_hotkey(handle)
        except Exception:
            pass
