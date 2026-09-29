"""
Небольшие хелперы анимаций (PySide6). Референс по ощущению — «жидкое стекло»
iOS: плавные ease-in-out с лёгкими overshoot.
"""

import sys

from PySide6.QtCore import (
    QPropertyAnimation, QVariantAnimation, QEasingCurve, QPointF, QPoint, Qt
)
from PySide6.QtGui import QPainter, QPixmap, QRegion
from PySide6.QtWidgets import QGraphicsOpacityEffect, QWidget


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

# --- токены длительностей (мс) ------------------------------------------ #
# Все длительности живут здесь. Двадцать почти одинаковых чисел, набитых по
# файлам вручную, — верный способ получить интерфейс, который движется
# вразнобой. Потолок для интерфейса ~300 мс, выход быстрее входа: появление
# рассматривают, уход — нет.
HOVER_MS = 150          # подсветка под курсором (часто — значит коротко)
PRESS_MS = 160          # отклик на нажатие
BOUNCE_MS = 240         # «пружинка» нажатия: сжатие -> перелёт -> возврат
ENTER_MS = 200          # появление: сообщения, тосты, подсказки, попапы
EXIT_MS = 160           # исчезновение
TOGGLE_MS = 240         # галочка чекбокса, бегунок переключателя, пилюля сегментов
SHIFT_MS = 220          # сдвиг элементов списка (реордер, подтяжка после удаления)
SLIDE_MS = 300          # въезд новой строки / карточки
STATE_MS = 320          # смена состояния строки (загрузка <-> готово)
DRAWER_IN_MS = 320      # выдвижная панель (обрезка, плейлист) — раскрытие
DRAWER_OUT_MS = 240     # и схлопывание

# Смена размера окна: движение уже видимого окна -> ease-in-out. Нижняя панель
# (папка/about) синхронизируется этими же значениями — иначе диагональ ломается.
WIN_RESIZE_MS = 340
WIN_RESIZE_EASING = EASE_IN_OUT

# Смена вкладок: сначала уходит старая, потом проявляется новая.
PAGE_FADE_OUT_MS = 160
PAGE_FADE_IN_MS = 220
PAGE_FADE_EASING = EASE_OUT


# --- «Меньше анимации» ---------------------------------------------------- #
# Системная настройка Windows «Показывать анимацию в Windows» (Параметры ->
# Специальные возможности -> Дисплей). Выключена -> сдвиги и изменение размеров
# становятся мгновенными, а плавная смена прозрачности остаётся: «меньше
# анимации» — это не «никакой обратной связи».
_SPI_GETCLIENTAREAANIMATION = 0x1042
_forced = None


def set_reduced_motion(value):
    """Принудительно (тесты): True/False, None — снова по системе."""
    global _forced
    _forced = value


def reduced_motion():
    if _forced is not None:
        return bool(_forced)
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        flag = ctypes.c_int(1)
        ok = ctypes.windll.user32.SystemParametersInfoW(
            _SPI_GETCLIENTAREAANIMATION, 0, ctypes.byref(flag), 0)
        return bool(ok) and not flag.value
    except Exception:
        return False


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


def fade_window(window, start, end, duration, on_finished=None,
                easing=None):
    """Прозрачность ВЕРХНЕУРОВНЕВОГО окна через windowOpacity.

    fade() для окна не годится: QGraphicsOpacityEffect на полупрозрачном окне
    на кадр показывал насквозь рабочий стол (моргание, см. Spotlight)."""
    window.setWindowOpacity(float(start))
    return animate(window, start, end, duration,
                   lambda v: window.setWindowOpacity(v),
                   easing=easing or EASE_OUT, on_finished=on_finished,
                   attr="_win_fade_anim")


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
            easing=None, on_finished=None, attr="_anim", moves=False):
    """Числовая анимация: каждый тик вызывает on_tick(value).

    Предыдущая анимация с тем же attr останавливается — иначе две живые
    анимации писали бы одно и то же значение вперемешку.

    moves=True — значение двигает или растягивает что-то на экране. При
    «меньше анимации» такое не анимируется: сразу конечное значение."""
    if easing is None:
        easing = EASE_IN_OUT       # без указания — движение видимого элемента
    _stop_previous(owner, attr)
    if moves and reduced_motion():
        on_tick(float(end))
        if on_finished:
            on_finished()
        return None
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


def motion_ms(duration):
    """Длительность для движения: 0 при «меньше анимации». Для прямых
    QPropertyAnimation(pos/geometry), которые не идут через animate()."""
    return 0 if reduced_motion() else duration


# --- смена страниц через снимок ------------------------------------------- #
class _Snapshot(QWidget):
    """Картинка страницы, которая плавно проявляется или гаснет.

    QGraphicsOpacityEffect на живой странице перерисовывает всё её дерево
    виджетов вне экрана на каждом кадре. Снимок рисуется одним drawPixmap."""

    def __init__(self, parent, pixmap, geometry):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self._pm = pixmap
        self._opacity = 1.0
        self.setGeometry(geometry)

    def set_opacity(self, value):
        self._opacity = float(value)
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setOpacity(self._opacity)
        p.drawPixmap(0, 0, self._pm)
        p.end()


def snapshot(widget):
    """Картинка виджета с детьми на ПРОЗРАЧНОМ фоне.

    QWidget.grab() не годится: он рендерит с DrawWindowBackground и заливает
    фон палитрой, даже если сама страница прозрачная. Поверх стекла (тема
    Frosted) такой снимок на время перехода выглядел плоской серой плашкой."""
    dpr = widget.devicePixelRatioF() or 1.0
    pm = QPixmap(max(1, round(widget.width() * dpr)), max(1, round(widget.height() * dpr)))
    pm.setDevicePixelRatio(dpr)
    pm.fill(Qt.transparent)
    widget.render(pm, QPoint(), QRegion(), QWidget.DrawChildren)
    return pm


def swap_pages(host, old, new, on_finished=None, on_shown=None):
    """Уходит old, затем проявляется new — каждая через свой снимок.

    host — окно-родитель снимков. on_shown() зовётся, когда поверх страниц
    появился снимок (поднять кнопки нижней панели и т. п.), on_finished() — в
    конце. Незаконченную смену снимает cancel_swap(host)."""
    cancel_swap(host)
    out_pm = snapshot(old)
    out = _Snapshot(host, out_pm, old.geometry())
    host._page_swap = [out]
    old.hide()
    out.show()
    out.raise_()
    if on_shown:
        on_shown()

    def phase_in():
        out.hide()
        out.deleteLater()
        in_pm = snapshot(new)       # страница уже разложена (setGeometry)
        cover = _Snapshot(host, in_pm, new.geometry())
        host._page_swap = [cover]
        cover.set_opacity(0.0)
        cover.show()
        cover.raise_()
        if on_shown:
            on_shown()

        def done():
            host._page_swap = []
            new.show()
            new.raise_()
            cover.hide()
            cover.deleteLater()
            if on_shown:
                on_shown()
            if on_finished:
                on_finished()

        animate(cover, 0.0, 1.0, PAGE_FADE_IN_MS, cover.set_opacity,
                easing=PAGE_FADE_EASING, on_finished=done, attr="_snap_anim")

    animate(out, 1.0, 0.0, PAGE_FADE_OUT_MS, out.set_opacity,
            easing=PAGE_FADE_EASING, on_finished=phase_in, attr="_snap_anim")


def cancel_swap(host):
    """Обрывает смену вкладок: снимки убираются, колбэки не вызываются."""
    for snap in getattr(host, "_page_swap", None) or []:
        try:
            _stop_previous(snap, "_snap_anim")
            snap.hide()
            snap.deleteLater()
        except RuntimeError:
            pass
    host._page_swap = []
