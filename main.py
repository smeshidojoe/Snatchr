import os
import sys

# Отключаем аппаратное декодирование в Qt Multimedia (FFmpeg-бэкенд): у VP9 через
# HW-декодер маленький пул кадров, и при перемотке превью в панели обрезки он
# переполняется («Static surface pool size exceeded» / vp9 get_buffer failed).
# Программное декодирование коротких роликов дешёвое и стабильное.
os.environ.setdefault("QT_FFMPEG_DECODING_HW_DEVICE_TYPES", "")

from PySide6.QtWidgets import QApplication

from core import updater
from app import App
from tray import TrayIcon
from core.constants import APP_NAME

_INSTANCE_MUTEX = None


def _is_only_instance():
    """True — мы единственный инстанс; False — Snatchr уже запущен (тогда выходим,
    чтобы не плодить иконки в трее). Именованный мьютекс живёт до конца процесса."""
    global _INSTANCE_MUTEX
    try:
        import ctypes
        from ctypes import wintypes
        k = ctypes.windll.kernel32
        k.CreateMutexW.restype = wintypes.HANDLE
        k.CreateMutexW.argtypes = [wintypes.LPCVOID, wintypes.BOOL, wintypes.LPCWSTR]
        _INSTANCE_MUTEX = k.CreateMutexW(None, False, "Snatchr-Single-Instance-Mutex")
        return k.GetLastError() != 183          # ERROR_ALREADY_EXISTS
    except Exception:
        return True                              # не блокируем запуск при ошибке


def _set_app_identity(app):
    """Имя приложения для ОС (панель задач/уведомления группируются под Snatchr,
    а не под «Python»). Имя процесса в Диспетчере задач задаёт уже сам .exe."""
    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName(APP_NAME)
    app.setOrganizationName(APP_NAME)
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_NAME)
    except Exception:
        pass


if __name__ == "__main__":
    # Самоприменение обновления: этот же exe (Snatchr-new.exe) запущен с флагом
    # --apply-update <старый_exe> — ждём выхода старого, подменяем его собой и
    # запускаем. Обрабатываем ДО мьютекса — это не «второй инстанс».
    if "--apply-update" in sys.argv:
        try:
            i = sys.argv.index("--apply-update")
            updater.apply_self_update(sys.argv[i + 1] if i + 1 < len(sys.argv) else "")
        except Exception:
            pass
        sys.exit(0)

    # Защита от нескольких запусков: если Snatchr уже работает — тихо выходим.
    if not _is_only_instance():
        sys.exit(0)

    # Страховка: если с прошлого запуска остался распакованный апдейт —
    # применяем то, что не заблокировано (сам exe заменяет внешний помощник).
    try:
        updater.apply_pending_update()
        updater.cleanup_applied()      # убрать Snatchr-new.exe, если апдейт применён
    except Exception:
        pass

    # Уборка мусора прошлых запусков (папки заданий + осиротевшие info.json в %TEMP%).
    try:
        from core import downloader
        downloader.cleanup_temp()
    except Exception:
        pass

    app = QApplication(sys.argv)
    _set_app_identity(app)
    # Окно живёт в трее: не закрываем приложение, когда окно скрыто.
    app.setQuitOnLastWindowClosed(False)

    window = App()

    tray = TrayIcon(window)
    window.tray = tray
    tray.run()
    window.sync_autostart()       # привести реестр автозапуска к настройке
    window.start_update_watch()   # фоновая проверка обновлений + тост-анонс
    window.run_first_launch()     # первый старт: Spotlight + подсказка про трей

    sys.exit(app.exec())
