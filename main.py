import sys

from PySide6.QtWidgets import QApplication

from core import updater
from app import App
from tray import TrayIcon
from core.constants import APP_NAME


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
    # Страховка: если с прошлого запуска остался распакованный апдейт —
    # применяем то, что не заблокировано (сам exe заменяет внешний помощник).
    try:
        updater.apply_pending_update()
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

    sys.exit(app.exec())
