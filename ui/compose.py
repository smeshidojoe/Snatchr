"""
Как Windows собирает наши прозрачные окна (по образцу Clipr).

Прозрачное окно Qt по умолчанию растровое: каждый кадр целиком рисуется в
памяти и отдаётся системе через UpdateLayeredWindow. Для окон, которые
двигаются и меняют прозрачность (главное окно, Spotlight, меню, тосты), это
дорого. Окно, собранное через GPU (RHI/Direct3D), получает прозрачность от DWM,
и анимации окна стоят заметно дешевле.

Второй плюс: только на таком (не layered) окне работает исключение из захвата
экрана — без него «живое» стекло снимало бы само себя (см. ui/glass.py).

Выключить: переменная окружения SNATCHR_NO_GPU=1 — на случай драйвера, с
которым прозрачность через DWM ведёт себя плохо.
"""

import os
import sys

from PySide6.QtCore import Qt


def enabled():
    return (sys.platform == "win32"
            and os.environ.get("SNATCHR_NO_GPU", "") != "1"
            and os.environ.get("QT_QPA_PLATFORM", "") != "offscreen")


def gpu_composited(window):
    """Переводит верхнеуровневое окно на сборку через GPU.

    Переключает окно любой RHI-виджет внутри — даже скрытый размером 1x1
    (проверено в Clipr), поэтому ни пикселя он не рисует. Вызывать ДО первого
    show(): тип поверхности окна выбирается при создании его хендла."""
    if not enabled() or getattr(window, "_gpu_probe", None) is not None:
        return
    try:
        from PySide6.QtWidgets import QRhiWidget
    except ImportError:
        return
    probe = QRhiWidget(window)
    probe.setFixedSize(1, 1)
    probe.setAttribute(Qt.WA_TransparentForMouseEvents, True)
    probe.hide()
    window._gpu_probe = probe          # держим ссылку


def exclude_from_capture(window, on=True):
    """Прячет окно от снимков и записи экрана (WDA_EXCLUDEFROMCAPTURE,
    Windows 10 2004+). Работает только у окна, собранного через GPU.
    True — получилось."""
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        hwnd = int(window.winId())
        affinity = 0x11 if on else 0x0      # WDA_EXCLUDEFROMCAPTURE / WDA_NONE
        return bool(ctypes.windll.user32.SetWindowDisplayAffinity(hwnd, affinity))
    except Exception:
        return False
