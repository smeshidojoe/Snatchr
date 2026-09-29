"""
Стекло темы Frosted: окно рисует то, что под ним, размытым (по образцу Spotty).

Как это устроено:

* Снимок. Экран под окном снимается через GDI BitBlt. Без «живого» режима —
  один раз, в момент показа окна (событие Show приходит ДО того, как окно
  появилось на экране, поэтому себя окно не снимает). Снимается область
  под окном с запасом в сторону, куда окно растёт (glass_grow окна). Вышло
  окно за снятое (перетащили, раскрылась обрезка) — фон переснимается, на
  ~60 мс исключив окно из захвата (см. Backdrop.refresh).
* Живой режим (настройка Live Glass). Пока окно видно, снимок повторяется
  ~24 раза в секунду в фоновом потоке. Чтобы окно не снимало само себя, оно исключено из захвата
  экрана (WDA_EXCLUDEFROMCAPTURE) — побочный эффект: его не видно на скриншотах
  и в записи экрана. Работает только у окна, собранного через GPU
  (ui/compose.py). Перерисовка — только когда фон реально поменялся: на
  статичном рабочем столе окно не перерисовывается вовсе.
* Размытие — на CPU, «двойным фильтром»: картинку шесть раз уменьшаем
  вдвое со сглаживанием и увеличиваем обратно. Получается почти гауссово пятно
  за доли миллисекунды — без OpenGL-контекста, который может не подняться
  (удалённый рабочий стол, старый драйвер).
* Отрисовка. Панель кладёт размытую картинку кистью (а не setClipPath —
  обрезка по пути в Qt не сглаживается), сверху — подкраска (тень) и кромка.

Если снимка нет (не Windows, сбой захвата, тему включили на открытом окне без
живого режима), paint() возвращает False, и окно рисует обычный градиент.
"""

import ctypes
import sys
import threading
from ctypes import wintypes

from PySide6.QtCore import QEvent, QObject, QPoint, QRect, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (
    QBrush, QColor, QGuiApplication, QImage, QPainter, QPainterPath, QPen, QTransform,
)

from core import themes
from ui import compose

LIVE_INTERVAL_MS = 42          # ~24 кадра/с: захват GDI ~10 мс, чаще — лишняя нагрузка
DOWN_STEPS = 6                 # уменьшений вдвое: 1/64 — сила размытия
UP_STEPS = 3                   # увеличений обратно: до 1/8 — остальное доделает кисть
MARGIN = 48                    # запас снимка вокруг окна, логические px


# --- захват экрана (GDI) -------------------------------------------------- #
SRCCOPY = 0x00CC0020


class _BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD), ("biWidth", ctypes.c_long),
        ("biHeight", ctypes.c_long), ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", ctypes.c_long),
        ("biYPelsPerMeter", ctypes.c_long), ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


_api = None


def _gdi():
    global _api
    if _api is None:
        user32, gdi32 = ctypes.windll.user32, ctypes.windll.gdi32
        user32.GetDC.restype = wintypes.HDC
        user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
        gdi32.CreateCompatibleDC.restype = wintypes.HDC
        gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
        gdi32.CreateDIBSection.restype = wintypes.HBITMAP
        gdi32.CreateDIBSection.argtypes = [wintypes.HDC, ctypes.c_void_p, wintypes.UINT,
                                           ctypes.POINTER(ctypes.c_void_p),
                                           wintypes.HANDLE, wintypes.DWORD]
        gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
        gdi32.SelectObject.restype = wintypes.HGDIOBJ
        gdi32.BitBlt.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                 ctypes.c_int, wintypes.HDC, ctypes.c_int, ctypes.c_int,
                                 wintypes.DWORD]
        gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
        gdi32.DeleteDC.argtypes = [wintypes.HDC]
        _api = (user32, gdi32)
    return _api


def grab(x, y, width, height):
    """Область экрана в физических пикселях -> QImage (RGB32) или None."""
    if width <= 0 or height <= 0 or sys.platform != "win32":
        return None
    user32, gdi32 = _gdi()
    screen_dc = user32.GetDC(None)
    mem_dc = gdi32.CreateCompatibleDC(screen_dc)
    header = _BITMAPINFOHEADER()
    header.biSize = ctypes.sizeof(header)
    header.biWidth = width
    header.biHeight = -height                  # сверху вниз
    header.biPlanes = 1
    header.biBitCount = 32
    bits = ctypes.c_void_p()
    bitmap = gdi32.CreateDIBSection(mem_dc, ctypes.byref(header), 0,
                                    ctypes.byref(bits), None, 0)
    try:
        if not bitmap or not bits:
            return None
        old = gdi32.SelectObject(mem_dc, bitmap)
        # Без CAPTUREBLT: с ним на части машин на время снимка мигает курсор.
        ok = gdi32.BitBlt(mem_dc, 0, 0, width, height, screen_dc, x, y, SRCCOPY)
        gdi32.SelectObject(mem_dc, old)
        if not ok:
            return None
        buf = (ctypes.c_ubyte * (width * height * 4)).from_address(bits.value)
        # BGRX — ровно Format_RGB32. copy(): буфер умрёт вместе с DIB-секцией.
        return QImage(buf, width, height, width * 4, QImage.Format_RGB32).copy()
    finally:
        if bitmap:
            gdi32.DeleteObject(bitmap)
        gdi32.DeleteDC(mem_dc)
        user32.ReleaseDC(None, screen_dc)


def to_physical(screen, rect):
    """Логический прямоугольник -> физические пиксели рабочего стола.

    Qt 6 держит левый верхний угол каждого экрана в родных координатах, а
    размеры внутри экрана делит на масштаб."""
    dpr = screen.devicePixelRatio()
    origin = screen.geometry().topLeft()
    return QRect(origin.x() + round((rect.x() - origin.x()) * dpr),
                 origin.y() + round((rect.y() - origin.y()) * dpr),
                 round(rect.width() * dpr), round(rect.height() * dpr))


def saturate(img, k):
    """Насыщенность картинки: k > 1 — ярче цвета, 1 — без изменений.

    Зовётся на самой маленькой ступени размытия (десятки пикселей в ширину),
    поэтому поштучный проход по пикселям ничего не стоит."""
    if abs(k - 1.0) < 1e-3:
        return img
    img = img.convertToFormat(QImage.Format_RGB32)
    for y in range(img.height()):
        for x in range(img.width()):
            c = img.pixelColor(x, y)
            r, g, b = c.redF(), c.greenF(), c.blueF()
            gray = 0.2126 * r + 0.7152 * g + 0.0722 * b
            img.setPixelColor(x, y, QColor.fromRgbF(
                min(1.0, max(0.0, gray + (r - gray) * k)),
                min(1.0, max(0.0, gray + (g - gray) * k)),
                min(1.0, max(0.0, gray + (b - gray) * k))))
    return img


def blur(image, down=DOWN_STEPS, up=UP_STEPS, saturation=1.0):
    """Размытие «двойным фильтром»: вниз вдвое down раз, потом вверх up раз.

    Уменьшение со сглаживанием усредняет соседние пиксели, увеличение —
    билинейно растягивает; вместе это близко к гауссу большого радиуса.
    Возвращает (картинка, во сколько раз она меньше исходной)."""
    img = image
    for _ in range(down):
        if img.width() < 4 or img.height() < 4:
            break
        img = img.scaled(max(1, img.width() // 2), max(1, img.height() // 2),
                         Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
    img = saturate(img, saturation)
    for _ in range(up):
        img = img.scaled(img.width() * 2, img.height() * 2,
                         Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
    factor = image.width() / max(1, img.width())
    return img, factor


def pad_edges(img, pad=2):
    """Картинка с полями в pad пикселей, залитыми крайними пикселями.

    Кисть с картинкой повторяет её за краем, и билинейная выборка у самой
    кромки подмешивала бы противоположный край — светлую или тёмную полоску.
    С продублированной кромкой подмешивать нечего."""
    w, h = img.width(), img.height()
    out = QImage(w + 2 * pad, h + 2 * pad, QImage.Format_RGB32)
    p = QPainter(out)
    p.drawImage(QRect(pad, pad, w, h), img)
    p.drawImage(QRect(0, pad, pad, h), img, QRect(0, 0, 1, h))                # слева
    p.drawImage(QRect(pad + w, pad, pad, h), img, QRect(w - 1, 0, 1, h))      # справа
    p.drawImage(QRect(0, 0, w + 2 * pad, pad), out, QRect(0, pad, w + 2 * pad, 1))
    p.drawImage(QRect(0, pad + h, w + 2 * pad, pad), out,
                QRect(0, pad + h - 1, w + 2 * pad, 1))
    p.end()
    return out


class _FrameRelay(QObject):
    """Отдаёт кадр из фонового потока в главный."""
    done = Signal(object)


def _lum_of(c):
    return 0.2126 * c.redF() + 0.7152 * c.greenF() + 0.0722 * c.blueF()


def luminance(img, cells=4):
    """(самый тёмный, самый светлый) участок фона, яркость 0..1.

    Средняя по всей области врёт: под окном половина тёмного редактора и
    половина светлой страницы дают «среднюю» яркость, и одна из половин
    выходит не того тона. Делим фон на cells x cells участков (сглаженным
    сжатием); светлому стеклу важен самый тёмный участок, тёмному — самый
    светлый."""
    small = img.scaled(cells, cells, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
    vals = [_lum_of(small.pixelColor(x, y))
            for y in range(small.height()) for x in range(small.width())]
    return (min(vals), max(vals)) if vals else (0.5, 0.5)


# --- подложка окна -------------------------------------------------------- #
class Backdrop(QObject):
    """Размытый фон под одним верхнеуровневым окном.

    Ставится на окно один раз (window._glass = Backdrop(window, app)) и сам
    следит за его показом и скрытием."""

    def __init__(self, window, app):
        super().__init__(window)
        self._win = window
        self._app = app
        self._img = None             # размытая картинка
        self._rect = None            # QRectF: какой логический прямоугольник экрана она покрывает
        self._probe = None           # маленькая копия последнего кадра — «фон поменялся?»
        self._lum = (0.5, 0.5)       # яркость фона под окном: (темнейший, светлейший) участок
        self._excluded = False
        self._busy = False           # кадр живого режима ещё считается в потоке
        self._relay = _FrameRelay()
        self._relay.done.connect(self._on_live_frame)
        self._refresh_timer = QTimer(self)   # досъёмка после перетаскивания/роста
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.setInterval(150)
        self._refresh_timer.timeout.connect(self.refresh)
        self._timer = QTimer(self)
        self._timer.setInterval(LIVE_INTERVAL_MS)
        self._timer.timeout.connect(self._live_tick)
        window.installEventFilter(self)

    # --- состояние ------------------------------------------------------ #
    def _theme(self):
        return self._app.settings.get("theme", themes.DEFAULT_THEME)

    def enabled(self):
        return (sys.platform == "win32" and compose.enabled()
                and themes.is_glass(self._theme()))

    def live(self):
        # Живой режим возможен только у окна, собранного через GPU: иначе
        # исключение из захвата не принимается, и стекло снимало бы себя.
        return self.enabled() and bool(self._app.settings.get("live_glass", False))

    def set_live(self, on):
        """Переключили настройку на открытом окне."""
        if self._win.isVisible():
            self._start()

    def theme_changed(self):
        """Сменили тему: без стекла — всё выключить; со стеклом на открытом
        окне фон появится с живым режимом сразу, без него — при следующем показе."""
        if not self.enabled():
            self._stop()
            self._set_excluded(False)
            self._img = None
        elif self._win.isVisible():
            self._start()
            if not self.live():
                self.refresh()               # тему включили на открытом окне

    # --- показ / скрытие -------------------------------------------------- #
    def eventFilter(self, obj, ev):
        if obj is self._win:
            t = ev.type()
            if t == QEvent.Show:
                self._on_show()
            elif t == QEvent.Hide:
                self._stop()
                self._refresh_timer.stop()
            elif t in (QEvent.Move, QEvent.Resize):
                self._check_coverage()
        return False

    # --- досъёмка без живого режима --------------------------------------- #
    def _check_coverage(self):
        """Окно вышло за снятую область (перетащили, выросло сильнее запаса)?
        Тогда фон надо доснять — чуть погодя, когда движение закончится."""
        if (self._img is None or self.live() or not self.enabled()
                or not self._win.isVisible()):
            return
        if not self._rect.contains(QRectF(self._win.frameGeometry())):
            self._refresh_timer.start()

    def refresh(self):
        """Переснять фон под ВИДИМЫМ окном без живого режима.

        Окно на мгновение исключается из захвата экрана: на экране оно
        остаётся, но в снимок не попадает. Исключению нужен кадр композиции
        DWM, поэтому снимаем чуть погодя и сразу возвращаем окно в захват —
        скриншот, сделанный ровно в эти ~60 мс, окна бы не увидел."""
        if not self.enabled() or self.live() or not self._win.isVisible():
            return
        if not compose.exclude_from_capture(self._win, True):
            return

        def shoot():
            try:
                if self._win.isVisible() and not self.live():
                    if self._capture(grow=True):
                        self._win.update()
            finally:
                if not self.live():
                    compose.exclude_from_capture(self._win, False)
                    self._excluded = False

        QTimer.singleShot(60, shoot)

    def _on_show(self):
        """Окно вот-вот появится (ещё не на экране) — самое время снять фон."""
        if not self.enabled():
            self._img = None
            self._set_excluded(False)
            return
        if not self.live():
            self._set_excluded(False)
            self._capture(grow=True)
        self._start()

    def _start(self):
        live = self.live()
        self._set_excluded(live)
        if live:
            self._probe = None
            if self._img is None:
                self._capture(grow=False)        # первый кадр — сразу, дальше в фоне
            self._win.update()
            self._timer.start()
        else:
            self._timer.stop()

    def _stop(self):
        self._timer.stop()

    def _set_excluded(self, on):
        if on == self._excluded:
            return
        ok = compose.exclude_from_capture(self._win, on)
        self._excluded = on and ok

    # --- снимок ---------------------------------------------------------- #
    def _target(self, grow):
        """(логический прямоугольник, физический) — что снимать.

        grow=True — с запасом по высоте: окно может вырасти (Settings выше
        главной, у Spotlight раскрывается обрезка). Сколько и в какую сторону —
        говорит само окно: glass_grow() -> (вверх, вниз), иначе запаса нет."""
        geo = self._win.frameGeometry()
        screen = (QGuiApplication.screenAt(geo.center())
                  or QGuiApplication.primaryScreen())
        if screen is None:
            return None
        # Запас вокруг окна: кисть с картинкой повторяется за её краем, и без
        # запаса у самой кромки окна проступал бы шов с противоположной стороны.
        m = MARGIN
        up = down = 0
        if grow:
            try:
                up, down = (int(v) for v in self._win.glass_grow())
            except (AttributeError, TypeError, ValueError):
                up = down = 0
        rect = QRect(geo).adjusted(-m, -m - up, m, m + down).intersected(screen.geometry())
        if rect.isEmpty():
            return None
        return rect, to_physical(screen, rect)

    def _saturation(self):
        pal = themes.palette(self._theme())
        return float(pal.get("glass_saturation", 1.0))

    @staticmethod
    def _process(rect, phys, saturation=1.0):
        """Снимок + размытие -> (картинка, покрытый прямоугольник) или None.
        Без Qt-виджетов: годится и для фонового потока."""
        try:
            shot = grab(phys.x(), phys.y(), phys.width(), phys.height())
        except Exception:
            shot = None
        if shot is None:
            return None
        img, _ = blur(shot, saturation=saturation)
        pad = 2
        kx = rect.width() / max(1, img.width())      # логических px на тексель
        ky = rect.height() / max(1, img.height())
        return (pad_edges(img, pad),
                QRectF(rect.x() - pad * kx, rect.y() - pad * ky,
                       rect.width() + 2 * pad * kx, rect.height() + 2 * pad * ky),
                luminance(img))

    def _capture(self, grow):
        target = self._target(grow)
        res = self._process(*target, self._saturation()) if target else None
        if res is None:
            self._img = None
            return False
        self._img, self._rect, self._lum = res
        return True

    def _live_tick(self):
        """Кадр живого режима. Захват экрана (~10 мс у GDI) идёт в фоновом
        потоке — окно в это время отвечает; новый кадр не заказываем, пока не
        готов предыдущий."""
        if not self._win.isVisible() or not self.live():
            self._timer.stop()
            return
        if self._busy:
            return
        target = self._target(False)
        if target is None:
            return
        self._busy = True
        relay = self._relay
        sat = self._saturation()

        def work():
            res = None
            try:
                res = self._process(*target, sat)
            finally:
                relay.done.emit(res)

        threading.Thread(target=work, name="glass-live", daemon=True).start()

    def _on_live_frame(self, res):
        self._busy = False
        if res is None or not self._timer.isActive():
            return
        img, rect, lum = res
        self._lum = lum
        # Перерисовываем окно, только если фон заметно поменялся: сравниваем
        # крошечную копию кадра — на статичном столе перерисовок ноль.
        probe = img.scaled(24, 24, Qt.IgnoreAspectRatio, Qt.FastTransformation)
        moved = rect != self._rect
        self._img, self._rect = img, rect
        if moved or probe != self._probe:
            self._probe = probe
            self._win.update()

    # --- отрисовка ------------------------------------------------------- #
    def tint_alpha(self, bright_key="glass_bright"):
        """Плотность подкраски под текущий фон.

        Постоянная плотность давала то серое окно (светлое стекло над тёмным
        столом), то нечитаемое (тёмное над светлым). Подбираем плотность a так,
        чтобы итоговая яркость a*tint + (1-a)*фон была около целевой. Для
        тёмной подкраски ориентир — самый светлый участок фона, для светлой —
        самый тёмный. Границы — чтобы стекло не исчезало и не становилось
        сплошной плашкой."""
        pal = themes.palette(self._theme())
        target = float(pal.get(bright_key, 0.9))
        lo = float(pal.get("glass_min", 0.4))
        hi = float(pal.get("glass_max", 0.88))
        tint_l = _lum_of(QColor(pal.get("glass_tint", "#ffffff")))
        darkest, brightest = self._lum
        lum = brightest if tint_l < target else darkest
        if abs(tint_l - lum) < 1e-3:
            return lo
        a = (target - lum) / (tint_l - lum)
        return max(lo, min(hi, a))

    def paint(self, p, widget, rect, radius, bright_key="glass_bright", edge=True):
        """Стекло в прямоугольнике rect (координаты widget), скругление radius.
        False — стекла нет, рисуй обычный фон."""
        if self._img is None or self._rect is None or not self.enabled():
            return False
        pal = themes.palette(self._theme())
        origin = widget.mapToGlobal(QPoint(0, 0))
        sx = self._rect.width() / max(1, self._img.width())
        sy = self._rect.height() / max(1, self._img.height())
        brush = QBrush(self._img)
        brush.setTransform(QTransform(sx, 0, 0, sy,
                                      self._rect.x() - origin.x(),
                                      self._rect.y() - origin.y()))
        path = QPainterPath()
        path.addRoundedRect(QRectF(rect), radius, radius)
        p.save()
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setRenderHint(QPainter.SmoothPixmapTransform, True)
        p.setPen(Qt.NoPen)
        p.setBrush(brush)
        p.drawPath(path)
        # Тот же снимок ещё раз в режиме Screen: 1 - (1 - b)^2 — тёмное
        # заметно светлеет, светлое почти не меняется. Белое стекло над
        # тёмным столом без этого оставалось серым при любой разумной подкраске.
        if pal.get("glass_lift", False):
            p.setCompositionMode(QPainter.CompositionMode_Screen)
            p.drawPath(path)
            p.setCompositionMode(QPainter.CompositionMode_SourceOver)
        tint = QColor(pal.get("glass_tint", "#ffffff"))
        tint.setAlphaF(self.tint_alpha(bright_key))
        p.setBrush(tint)
        p.drawPath(path)
        # Цвет фона сквозь плотную подкраску: размытый снимок ещё раз, слабо,
        # в режиме Overlay — светлое остаётся светлым, а оттенки обоев
        # проступают. Без этого на тёмном столе стекло было бы плоской плашкой.
        color = float(pal.get("glass_color", 0.0))
        if color > 0:
            p.setCompositionMode(QPainter.CompositionMode_Overlay)
            p.setOpacity(color)
            p.setBrush(brush)
            p.drawPath(path)
            p.setOpacity(1.0)
            p.setCompositionMode(QPainter.CompositionMode_SourceOver)
        if edge:
            # Светлая кромка на полпикселя внутрь — край читается как толща стекла.
            ec = QColor(pal.get("glass_edge", "#ffffff"))
            ec.setAlphaF(float(pal.get("glass_edge_alpha", 0.85)))
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(ec, 1))
            inner = QPainterPath()
            inner.addRoundedRect(QRectF(rect).adjusted(0.5, 0.5, -0.5, -0.5),
                                 max(0.0, radius - 0.5), max(0.0, radius - 0.5))
            p.drawPath(inner)
        p.restore()
        return True


def backdrop_of(widget):
    """Подложка окна, в котором лежит виджет (или None)."""
    try:
        return getattr(widget.window(), "_glass", None)
    except RuntimeError:
        return None
