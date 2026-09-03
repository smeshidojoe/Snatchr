"""
Небольшие хелперы анимаций (PySide6). Референс по ощущению — «жидкое стекло»
iOS: плавные ease-in-out с лёгкими overshoot.
"""

from PySide6.QtCore import (
    QPropertyAnimation, QVariantAnimation, QEasingCurve, QPointF
)
from PySide6.QtWidgets import QGraphicsOpacityEffect


# --- кривые ---------------------------------------------------------------- #
def _bezier(x1, y1, x2, y2):
    """Кривая по тем же контрольным точкам, что и CSS cubic-bezier(x1,y1,x2,y2)."""
    c = QEasingCurve(QEasingCurve.BezierSpline)
    c.addCubicBezierSegment(QPointF(x1, y1), QPointF(x2, y2), QPointF(1.0, 1.0))
    return c


# Готовые кривые Qt (OutCubic и родня) слишком вялые: за первые 20% времени
# OutCubic проходит 49% пути, а кривая ниже — 68%. Разгон в начале и есть то,
# из-за чего движение читается как быстрый отклик, а не как проигрывание
# анимации. Значения — стандартные для интерфейсов, не подобранные на глаз.
EASE_OUT = _bezier(0.23, 1.0, 0.32, 1.0)        # входы, выходы, отклик
EASE_IN_OUT = _bezier(0.77, 0.0, 0.175, 1.0)    # движение уже видимого элемента
EASE_DRAWER = _bezier(0.32, 0.72, 0.0, 1.0)     # выдвижные панели (кривая iOS)

# ВЫХОДЫ ТОЖЕ EASE_OUT. Соблазн взять ease-in («уезжает с разгоном») ломает
# ощущение: медленное начало задерживает ровно тот момент, на который смотрит
# пользователь, и закрытие кажется вязким.

# --- общие тайминги (единый источник для синхронных анимаций) ------------- #
# Смена размера окна: движение уже видимого окна -> ease-in-out. Нижняя панель
# (папка/about) синхронизируется этими же значениями — иначе диагональ ломается.
WIN_RESIZE_MS = 340
WIN_RESIZE_EASING = EASE_IN_OUT

# Кросс-фейд между вкладками (уходящая чуть быстрее — лёгкий перехлёст).
PAGE_FADE_OUT_MS = 240
PAGE_FADE_IN_MS = 300
PAGE_FADE_EASING = EASE_IN_OUT


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
         easing=None, on_finished=None):
    """Плавное изменение прозрачности виджета через QGraphicsOpacityEffect."""
    if easing is None:
        easing = EASE_OUT          # проявление/исчезновение — это вход и выход
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
            easing=None, on_finished=None, attr="_anim"):
    """Числовая анимация: каждый тик вызывает on_tick(value).

    Предыдущая анимация с тем же attr останавливается — иначе две живые
    анимации писали бы одно и то же значение вперемешку."""
    if easing is None:
        easing = EASE_IN_OUT       # без указания — движение видимого элемента
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
