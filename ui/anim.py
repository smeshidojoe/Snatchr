"""
Небольшие хелперы анимаций (PySide6). Референс по ощущению — «жидкое стекло»
iOS: плавные ease-in-out с лёгкими overshoot.
"""

from PySide6.QtCore import QPropertyAnimation, QVariantAnimation, QEasingCurve
from PySide6.QtWidgets import QGraphicsOpacityEffect


# --- общие тайминги/кривые (единый источник для синхронных анимаций) ------ #
# Смена размера окна: плавно на старте И финише (InOutCubic). Нижняя панель
# (папка/about) синхронизируется этими же значениями — иначе диагональ ломается.
WIN_RESIZE_MS = 340
WIN_RESIZE_EASING = QEasingCurve.InOutCubic

# Кросс-фейд между вкладками (уходящая чуть быстрее — лёгкий перехлёст).
PAGE_FADE_OUT_MS = 240
PAGE_FADE_IN_MS = 300
PAGE_FADE_EASING = QEasingCurve.InOutCubic


def _stop_previous(owner, attr):
    """
    Останавливает предыдущую анимацию, лежащую в owner.<attr>.

    Без этого она продолжала жить: объект создаётся с owner в родителях, поэтому
    Qt держит его даже после перезаписи атрибута, и старая анимация тикает
    дальше. Две анимации одного значения дрались между собой — например,
    подсветка «навёл»/«увёл» при быстром движении мыши залипала, потому что
    побеждала та, что закончится последней.
    """
    prev = getattr(owner, attr, None)
    if prev is None:
        return
    try:
        prev.stop()
        prev.deleteLater()
    except RuntimeError:
        pass                       # объект уже удалён Qt
    try:
        setattr(owner, attr, None)
    except (AttributeError, RuntimeError):
        pass


def stop(owner, attr):
    """Останавливает анимацию, лежащую в owner.<attr> (публичная обёртка)."""
    _stop_previous(owner, attr)


def fade(widget, start, end, duration=200,
         easing=QEasingCurve.InOutQuad, on_finished=None):
    """Плавное изменение прозрачности виджета через QGraphicsOpacityEffect."""
    _stop_previous(widget, "_fade_anim")
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
    """Числовая анимация: каждый тик вызывает on_tick(value).

    Предыдущая анимация с тем же attr останавливается — иначе две живые
    анимации писали бы одно и то же значение вперемешку."""
    _stop_previous(owner, attr)
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
