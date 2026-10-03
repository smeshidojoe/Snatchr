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

import ctypes
import threading

from PySide6.QtCore import QObject, Signal

# Скан-код модификатора в keyboard -> какие клавиши спросить у Windows. По
# скан-коду, а не по таблице keyboard: у той для Win записаны чужие коды.
_MOD_VK = {29: (0x11,), 56: (0x12,),                 # Ctrl, Alt (левый и правый)
           42: (0x10,), 54: (0x10,),                 # Shift
           91: (0x5B, 0x5C), 92: (0x5B, 0x5C)}       # Win
_guard_lock = threading.Lock()
_guard_installed = False


def _install_stale_guard(kb):
    """Чистит в keyboard «залипшие» модификаторы перед каждым нажатием клавиши.

    keyboard помнит нажатые клавиши по событиям хука и верит им на слово. Если
    отпускание не дошло — Ctrl+Alt+Del и экран блокировки (там другой рабочий
    стол), окно с правами администратора (хук обычной программы его ввода не
    видит) — клавиша для неё так и остаётся зажатой. С залипшим Alt обычный
    Ctrl+V становился «Ctrl+Alt+V» и запускал загрузку из буфера, а Ctrl+Shift+E
    переставал срабатывать вовсе. Поэтому перед обычной клавишей сверяем
    модификаторы с реальным состоянием клавиатуры и лишние выбрасываем.

    Хук блокирующий (иначе он отработал бы ПОСЛЕ сопоставления сочетаний), а
    значит, обязан вернуть True при любом исходе: False проглотил бы клавишу во
    всей системе.
    """
    global _guard_installed
    try:
        is_down = ctypes.windll.user32.GetAsyncKeyState
    except Exception:
        return

    def physically_up(scan_code):
        vks = _MOD_VK.get(scan_code)
        if not vks:
            return False                  # не знаем, что это — не трогаем
        return not any(is_down(vk) & 0x8000 for vk in vks)

    def guard(event):
        try:
            if event.event_type != kb.KEY_DOWN or kb.is_modifier(event.scan_code):
                return True
            with kb._pressed_events_lock:
                stale = [sc for sc in kb._pressed_events
                         if kb.is_modifier(sc) and physically_up(sc)]
                for sc in stale:
                    del kb._pressed_events[sc]
                    kb._listener.active_modifiers.discard(sc)
        except Exception:
            pass
        return True

    # Менеджеров несколько (Spotlight, видео, аудио), регистрируются они
    # параллельно, а сторож нужен один на процесс.
    with _guard_lock:
        if _guard_installed:
            return
        try:
            kb.hook(guard, suppress=True)
            _guard_installed = True
        except Exception:
            pass


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
            _install_stale_guard(keyboard)
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
