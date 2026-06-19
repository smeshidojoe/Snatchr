"""
Небольшие хелперы анимаций (PySide6). Референс по ощущению — «жидкое стекло»
iOS: плавные ease-in-out с лёгкими overshoot.
"""

from PySide6.QtCore import QPropertyAnimation, QVariantAnimation, QEasingCurve
from PySide6.QtWidgets import QGraphicsOpacityEffect


def fade(widget, start, end, duration=200,
         easing=QEasingCurve.InOutQuad, on_finished=None):
    """Плавное изменение прозрачности виджета через QGraphicsOpacityEffect."""
    eff = QGraphicsOpacityEffect(widget)
    eff.setOpacity(start)
    widget.setGraphicsEffect(eff)

    anim = QPropertyAnimation(eff, b"opacity", widget)
    anim.setDuration(duration)
    anim.setStartValue(float(start))
    anim.setEndValue(float(end))
    anim.setEasingCurve(easing)

    def _finish():
        # Снимаем эффект после анимации (резкость/производительность).
        widget.setGraphicsEffect(None)
        if on_finished:
            on_finished()

    anim.finished.connect(_finish)
    widget._fade_anim = anim   # удерживаем ссылку
    anim.start()
    return anim


def animate(owner, start, end, duration, on_tick,
            easing=QEasingCurve.InOutCubic, on_finished=None, attr="_anim"):
    """Числовая анимация: каждый тик вызывает on_tick(value)."""
    anim = QVariantAnimation(owner)
    anim.setDuration(duration)
    anim.setStartValue(float(start))
    anim.setEndValue(float(end))
    anim.setEasingCurve(easing)
    anim.valueChanged.connect(lambda v: on_tick(float(v)))
    if on_finished:
        anim.finished.connect(on_finished)
    setattr(owner, attr, anim)   # удерживаем ссылку
    anim.start()
    return anim
