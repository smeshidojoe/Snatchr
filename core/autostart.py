"""
Автозапуск Snatchr при старте Windows через ключ реестра
HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run.

Без прав администратора. Пишем полный путь к exe в кавычках. В dev-режиме
(не собранный exe) реестр НЕ трогаем — просто сообщаем «выключено», чтобы
галочка в настройках не прописывала python.exe в автозапуск.
"""

import os
import sys

_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_NAME = "Snatchr"


def _is_frozen():
    return bool(getattr(sys, "frozen", False))


def _exe_path():
    return os.path.abspath(sys.executable)


def is_enabled():
    """Прописан ли Snatchr в автозапуске (по данным реестра)."""
    if not _is_frozen():
        return False
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as k:
            val, _ = winreg.QueryValueEx(k, _NAME)
            return bool(val)
    except OSError:
        return False


def set_enabled(on):
    """Включает/выключает автозапуск. Возвращает True при успехе (или в dev,
    где регистрировать нечего). Ошибки реестра проглатываем -> False."""
    if not _is_frozen():
        return True                     # dev: ничего не регистрируем
    try:
        import winreg
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as k:
            if on:
                winreg.SetValueEx(k, _NAME, 0, winreg.REG_SZ, f'"{_exe_path()}"')
            else:
                try:
                    winreg.DeleteValue(k, _NAME)
                except FileNotFoundError:
                    pass
        return True
    except OSError:
        return False
