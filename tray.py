import os
import time

from PySide6.QtCore import Qt, QPoint, QPointF, QRectF, QTimer, QEasingCurve
from PySide6.QtGui import (
    QIcon, QPixmap, QImage, QPainter, QColor, QBrush, QPen, QPolygonF,
    QFontMetrics, QCursor, QGuiApplication,
)
from PySide6.QtWidgets import QSystemTrayIcon, QWidget, QApplication

from core.constants import ICONS_DIR
from core import themes, fonts, tools
from core.icons import tint_pixmap, raw_pixmap, COLORED_ICONS
from core.i18n import tr
from ui import anim

# Низкоуровневый мышиный хук (для показа меню по зажатию ЛКМ на иконке трея).
WH_MOUSE_LL    = 14
WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP   = 0x0202
WM_MOUSEMOVE   = 0x0200


def _blend(c0, c1, t):
    """Линейная интерполяция двух QColor (t: 0 -> c0, 1 -> c1)."""
    t = max(0.0, min(1.0, t))
    return QColor(
        int(c0.red()   + (c1.red()   - c0.red())   * t),
        int(c0.green() + (c1.green() - c0.green()) * t),
        int(c0.blue()  + (c1.blue()  - c0.blue())  * t),
    )


# ------------------------------------------------------------------ #
#  Меню трея в стиле селектора (открывается по ПКМ)
# ------------------------------------------------------------------ #
class TrayMenu(QWidget):
    """Всплывающее меню трея, оформленное как выпадающий список-селектор:
    тёмное скруглённое поле + строки с плавной скользящей подсветкой.
    items — список (label, callback)."""

    def __init__(self, app, items, hold=False):
        # ПКМ-меню — обычный Popup (Qt сам ведёт мышь и закрывает по клику вне).
        # Hold-меню (по зажатию ЛКМ) ведём вручную из мышиного хука, поэтому это
        # неактивируемое верхнее окно (без захвата мыши и без авто-закрытия).
        flags = Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint
        if hold:
            flags |= Qt.Tool | Qt.WindowStaysOnTopHint | Qt.WindowDoesNotAcceptFocus
        else:
            flags |= Qt.Popup
        super().__init__(None, flags)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        if hold:
            self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setMouseTracking(True)
        self._hold = hold

        self._app = app
        self._items = items
        s = app._s
        pal = themes.palette(app.settings.get("theme", themes.DEFAULT_THEME))
        self._font = fonts.font(s(11), "Regular")
        self._field_bg = QColor(pal["field_bg"])
        self._text_color = QColor(pal["text"])
        self._accent = QColor(pal["seg_sel"])
        self._border = QColor(pal["border"])
        self._on_accent = QColor(pal["on_accent"])
        self._radius = s(9)

        fm = QFontMetrics(self._font)
        self._row_h = fm.height() + s(14)
        self._pad = s(5)
        self._text_x = self._pad + s(10)
        longest = max((fm.horizontalAdvance(lbl) for lbl, _ in items), default=s(80))
        self._w = longest + s(40)

        self._hover = -1
        self._hi_pos = 0.0
        self._hi_alpha = 0.0
        self._opened_at = 0.0

    # --- позиционирование / показ ------------------------------------- #
    def popup_at(self, gpos):
        h = self._pad * 2 + self._row_h * len(self._items)
        self.resize(self._w, h)

        avail = QGuiApplication.primaryScreen().availableGeometry()
        # В hold-режиме отступаем от курсора, чтобы он изначально стоял НЕ на
        # кнопке (иначе простой hold-release случайно выберет первый пункт —
        # нужно осознанно навести на кнопку и отпустить).
        gap = self._row_h if self._hold else 0
        x = gpos.x()
        if gpos.y() + gap + h > avail.bottom():   # снизу не помещается — вверх
            y = gpos.y() - gap - h
        else:
            y = gpos.y() + gap
        x = max(avail.left(), min(x, avail.right() - self._w + 1))
        y = max(avail.top(), min(y, avail.bottom() - h + 1))
        self.move(x, y)
        self._opened_at = time.monotonic()
        self.show()
        self.raise_()

    # --- управление из мышиного хука (hold-режим, глобальные координаты) --- #
    def _idx_at_global(self, gpos):
        local = self.mapFromGlobal(gpos)
        if 0 <= local.x() <= self.width():
            return self._row_at(local.y())
        return -1

    def hover_at(self, gpos):
        idx = self._idx_at_global(gpos)
        if idx != self._hover:
            self._hover = idx
            self._animate_hi(idx)

    def release_at(self, gpos):
        idx = self._idx_at_global(gpos)
        self.close()
        if idx >= 0:
            QTimer.singleShot(0, self._items[idx][1])

    # --- мышь ---------------------------------------------------------- #
    def _row_at(self, y):
        idx = int((y - self._pad) // self._row_h)
        return idx if 0 <= idx < len(self._items) else -1

    def mouseMoveEvent(self, event):
        if self._hold:
            return                       # hold-меню ведём из хука, не из Qt
        idx = self._row_at(event.position().y())
        if idx != self._hover:
            self._hover = idx
            self._animate_hi(idx)

    def mouseReleaseEvent(self, event):
        if self._hold:
            return
        # «Хвост» открывающего клика игнорируем, чтобы меню не закрылось сразу.
        if time.monotonic() - self._opened_at < 0.18:
            return
        idx = self._row_at(event.position().y())
        self.close()
        if idx >= 0:
            QTimer.singleShot(0, self._items[idx][1])

    def _animate_hi(self, to_idx):
        frm = self._hi_pos
        a0 = self._hi_alpha
        if to_idx < 0:
            def tick(p):
                self._hi_alpha = a0 * (1.0 - p)
                self.update()
            anim.animate(self, 0.0, 1.0, 130, tick,
                         easing=QEasingCurve.OutCubic, attr="_hi_anim")
            return

        def tick(p):
            self._hi_pos = frm + (to_idx - frm) * p
            self._hi_alpha = a0 + (1.0 - a0) * p
            self.update()

        def fin():
            self._hi_pos = float(to_idx)
            self._hi_alpha = 1.0
            self.update()
        anim.animate(self, 0.0, 1.0, 190, tick,
                     easing=QEasingCurve.OutCubic, on_finished=fin, attr="_hi_anim")

    # --- отрисовка ----------------------------------------------------- #
    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        w, h = self.width(), self.height()

        p.setPen(QPen(self._border, 1))
        p.setBrush(self._field_bg)
        p.drawRoundedRect(QRectF(0.5, 0.5, w - 1, h - 1), self._radius, self._radius)

        if self._hi_alpha > 0.01:
            hy = self._pad + self._hi_pos * self._row_h
            acc = QColor(self._accent)
            acc.setAlphaF(max(0.0, min(1.0, self._hi_alpha)))
            p.setPen(Qt.NoPen)
            p.setBrush(acc)
            p.drawRoundedRect(QRectF(self._pad, hy, w - 2 * self._pad, self._row_h),
                              self._radius - 2, self._radius - 2)

        p.setFont(self._font)
        for i, (label, _) in enumerate(self._items):
            ry = self._pad + i * self._row_h
            cover = max(0.0, 1.0 - abs(i - self._hi_pos)) * self._hi_alpha
            p.setPen(_blend(self._text_color, self._on_accent, cover))
            p.drawText(QRectF(self._text_x, ry, w - self._text_x - self._pad, self._row_h),
                       Qt.AlignVCenter | Qt.AlignLeft, label)
        p.end()


# ------------------------------------------------------------------ #
#  Анимация иконки трея на время фоновой загрузки (Вставить)
# ------------------------------------------------------------------ #
class TrayAnimator:
    """Пока идёт фоновая загрузка/конвертация, иконка трея — кольцо прогресса,
    заполняющееся по часовой стрелке от 12 часов, с цветом от оранжевого к
    зелёному. По завершении: кольцо -> галочка -> плавно назад к иконке юзера.
    Все переходы — плавные (покадровая перерисовка по таймеру)."""

    RING_ORANGE = QColor("#ff9500")
    RING_GREEN  = QColor("#34c759")
    FAIL_RED    = QColor("#e05a5a")

    def __init__(self, tray):
        self._tray = tray
        self._size = 64
        self._timer = QTimer()
        self._timer.setInterval(40)
        self._timer.timeout.connect(self._tick)
        self._last_ring = -1.0     # последняя отрисованная доля кольца (анти-дребезг)

        self._phase = "idle"       # idle|start|ring|finish|hold|restore
        self._t = 0.0              # прогресс текущего перехода (0..1)
        self._frac = 0.0           # целевая доля кольца
        self._draw_frac = 0.0      # отрисованная доля (плавно догоняет целевую)
        self._base_pm = None       # иконка пользователя (для кроссфейда)
        self._check_pm = None      # заранее отрендеренная галочка/крестик
        self._spin = False         # индикатор без прогресса (вращающийся спиннер)
        self._angle = 0.0          # угол спиннера

    def is_active(self):
        return self._phase != "idle"

    # --- управление ---------------------------------------------------- #
    def start(self, spin=False):
        """spin=False — кольцо-прогресс (Paste, set_fraction). spin=True —
        неопределённый вращающийся спиннер (несколько загрузок Spotlight)."""
        self._base_pm = self._tray.base_pixmap(self._size)
        self._frac = 0.0
        self._draw_frac = 0.0
        self._spin = spin
        self._angle = 0.0
        self._phase = "start"
        self._t = 0.0
        if not self._timer.isActive():
            self._timer.start()

    def set_fraction(self, frac):
        self._frac = max(0.0, min(1.0, frac or 0.0))

    def finish(self, success=True):
        if self._phase == "idle":
            return
        self._check_pm = self._check_pixmap(success)
        self._phase = "finish"
        self._t = 0.0
        if not self._timer.isActive():
            self._timer.start()

    def abort(self):
        """Мгновенно убрать кольцо без галочки (напр., Spotlight снова открыт, а
        загрузка ещё идёт — прогресс теперь виден в окне Spotlight)."""
        if self._phase == "idle":
            return
        self._timer.stop()
        self._phase = "idle"
        self._tray.icon.setIcon(self._tray._resolve_icon())

    # --- покадровая логика --------------------------------------------- #
    def _tick(self):
        dt = self._timer.interval()
        if self._spin:
            self._angle = (self._angle + 4) % 360   # медленное вращение спиннера
        if self._phase == "start":
            self._t += dt / 240.0
            self._draw_frac += (self._frac - self._draw_frac) * 0.25
            self._set(self._crossfade(self._base_pm, self._active_pixmap(),
                                      min(1.0, self._t)))
            if self._t >= 1.0:
                self._phase, self._t = "ring", 0.0
        elif self._phase == "ring":
            if self._spin:
                self._set(self._spin_pixmap(self._angle))   # каждый кадр — вращение
            else:
                self._draw_frac += (self._frac - self._draw_frac) * 0.20
                # Перерисовываем, только когда видимая дуга реально изменилась.
                if abs(self._draw_frac - self._last_ring) >= 0.004:
                    self._last_ring = self._draw_frac
                    self._set(self._ring_pixmap(self._draw_frac))
        elif self._phase == "finish":
            self._t += dt / 280.0
            self._draw_frac += (1.0 - self._draw_frac) * 0.30
            self._set(self._crossfade(self._active_pixmap(), self._check_pm,
                                      min(1.0, self._t)))
            if self._t >= 1.0:
                self._phase, self._t = "hold", 0.0
        elif self._phase == "hold":
            self._t += dt / 560.0
            self._set(self._check_pm)
            if self._t >= 1.0:
                self._phase, self._t = "restore", 0.0
        elif self._phase == "restore":
            self._t += dt / 260.0
            self._set(self._crossfade(self._check_pm, self._base_pm, min(1.0, self._t)))
            if self._t >= 1.0:
                self._timer.stop()
                self._phase = "idle"
                self._tray.icon.setIcon(self._tray._resolve_icon())

    def _set(self, pm):
        self._tray.icon.setIcon(QIcon(pm))

    # --- рендер отдельных состояний ------------------------------------ #
    def _blank(self):
        img = QImage(self._size, self._size, QImage.Format_ARGB32)
        img.fill(Qt.transparent)
        return img

    def _ring_pixmap(self, frac):
        sz = self._size
        img = self._blank()
        p = QPainter(img)
        p.setRenderHint(QPainter.Antialiasing, True)
        m = sz * 0.16
        rect = QRectF(m, m, sz - 2 * m, sz - 2 * m)
        pw = sz * 0.13

        track = QPen(QColor(255, 255, 255, 55), pw)
        track.setCapStyle(Qt.RoundCap)
        p.setPen(track)
        p.setBrush(Qt.NoBrush)
        p.drawArc(rect, 0, 360 * 16)

        if frac > 0.004:
            col = _blend(self.RING_ORANGE, self.RING_GREEN, frac)
            pen = QPen(col, pw)
            pen.setCapStyle(Qt.RoundCap)
            p.setPen(pen)
            p.drawArc(rect, 90 * 16, -int(360 * 16 * frac))   # от 12 часов по часовой
        p.end()
        return QPixmap.fromImage(img)

    def _active_pixmap(self):
        """Текущий «рабочий» кадр: спиннер (spin) или кольцо-прогресс."""
        return (self._spin_pixmap(self._angle) if self._spin
                else self._ring_pixmap(self._draw_frac))

    def _spin_pixmap(self, angle):
        """Неопределённый спиннер: дуга ~110°, вращается по кругу."""
        sz = self._size
        img = self._blank()
        p = QPainter(img)
        p.setRenderHint(QPainter.Antialiasing, True)
        m = sz * 0.16
        rect = QRectF(m, m, sz - 2 * m, sz - 2 * m)
        pw = sz * 0.13
        track = QPen(QColor(255, 255, 255, 55), pw)
        track.setCapStyle(Qt.RoundCap)
        p.setPen(track)
        p.setBrush(Qt.NoBrush)
        p.drawArc(rect, 0, 360 * 16)
        arc = QPen(self.RING_ORANGE, pw)
        arc.setCapStyle(Qt.RoundCap)
        p.setPen(arc)
        p.drawArc(rect, int(-angle) * 16, 110 * 16)
        p.end()
        return QPixmap.fromImage(img)

    def _check_pixmap(self, success):
        sz = self._size
        img = self._blank()
        p = QPainter(img)
        p.setRenderHint(QPainter.Antialiasing, True)
        col = self.RING_GREEN if success else self.FAIL_RED
        m = sz * 0.16
        rect = QRectF(m, m, sz - 2 * m, sz - 2 * m)
        ring = QPen(col, sz * 0.13)
        ring.setCapStyle(Qt.RoundCap)
        p.setPen(ring)
        p.setBrush(Qt.NoBrush)
        p.drawArc(rect, 0, 360 * 16)

        mark = QPen(col, sz * 0.12)
        mark.setCapStyle(Qt.RoundCap)
        mark.setJoinStyle(Qt.RoundJoin)
        p.setPen(mark)
        if success:
            p.drawPolyline([QPointF(sz * 0.34, sz * 0.52),
                            QPointF(sz * 0.45, sz * 0.63),
                            QPointF(sz * 0.67, sz * 0.39)])
        else:
            p.drawLine(QPointF(sz * 0.39, sz * 0.39), QPointF(sz * 0.61, sz * 0.61))
            p.drawLine(QPointF(sz * 0.61, sz * 0.39), QPointF(sz * 0.39, sz * 0.61))
        p.end()
        return QPixmap.fromImage(img)

    def _crossfade(self, pm_a, pm_b, t):
        img = self._blank()
        p = QPainter(img)
        p.setRenderHint(QPainter.SmoothPixmapTransform, True)
        if pm_a is not None and not pm_a.isNull():
            p.setOpacity(1.0 - t)
            p.drawPixmap(0, 0, pm_a.scaled(self._size, self._size,
                                           Qt.KeepAspectRatio, Qt.SmoothTransformation))
        if pm_b is not None and not pm_b.isNull():
            p.setOpacity(t)
            p.drawPixmap(0, 0, pm_b.scaled(self._size, self._size,
                                           Qt.KeepAspectRatio, Qt.SmoothTransformation))
        p.end()
        return QPixmap.fromImage(img)


# ------------------------------------------------------------------ #
#  Показ меню по зажатию ЛКМ на иконке трея (глобальный мышиный хук)
# ------------------------------------------------------------------ #
class TrayHoldWatcher:
    """Открывает контекстное меню, если ЛКМ на иконке трея удерживают дольше
    порога; наведение по кнопкам и выбор ведём прямо из хука (отпустил на кнопке
    — выполнилась функция). Быстрый клик хук не трогает — работает как раньше
    (открыть/закрыть окно). Если хук не установился — просто ничего не делает."""

    HOLD_MS = 200
    LLMHF_INJECTED = 0x00000001

    def __init__(self, tray):
        self._tray = tray
        self._app = tray.app
        self._down = False
        self._menu = None
        self._hook = None
        self._cfunc = None
        self._user32 = None
        self._ctypes = None
        self._MSLL = None

        self._timer = QTimer()
        self._timer.setSingleShot(True)
        self._timer.setInterval(self.HOLD_MS)
        self._timer.timeout.connect(self._maybe_open)
        self._install()

    def _install(self):
        try:
            import ctypes
            from ctypes import wintypes

            class POINT(ctypes.Structure):
                _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]

            class MSLLHOOKSTRUCT(ctypes.Structure):
                _fields_ = [("pt", POINT), ("mouseData", wintypes.DWORD),
                            ("flags", wintypes.DWORD), ("time", wintypes.DWORD),
                            ("dwExtraInfo", ctypes.c_void_p)]

            LRESULT = ctypes.c_ssize_t
            self._PROC = ctypes.CFUNCTYPE(LRESULT, ctypes.c_int,
                                          wintypes.WPARAM, wintypes.LPARAM)
            self._cfunc = self._PROC(self._proc)
            u = ctypes.windll.user32
            u.SetWindowsHookExW.restype = wintypes.HHOOK
            u.SetWindowsHookExW.argtypes = [ctypes.c_int, self._PROC,
                                            wintypes.HINSTANCE, wintypes.DWORD]
            u.CallNextHookEx.restype = LRESULT
            u.CallNextHookEx.argtypes = [wintypes.HHOOK, ctypes.c_int,
                                         wintypes.WPARAM, wintypes.LPARAM]
            self._hook = u.SetWindowsHookExW(WH_MOUSE_LL, self._cfunc, None, 0)
            self._user32 = u
            self._ctypes = ctypes
            self._MSLL = MSLLHOOKSTRUCT
        except Exception:
            self._hook = None

    def _point(self, lParam):
        """(x, y), injected — из MSLLHOOKSTRUCT по указателю lParam."""
        try:
            ms = self._ctypes.cast(
                lParam, self._ctypes.POINTER(self._MSLL)).contents
            return (int(ms.pt.x), int(ms.pt.y)), bool(ms.flags & self.LLMHF_INJECTED)
        except Exception:
            return None, False

    def _proc(self, nCode, wParam, lParam):
        consume = False
        try:
            if nCode >= 0:
                if wParam == WM_LBUTTONDOWN:
                    self._on_down()
                elif wParam == WM_MOUSEMOVE:
                    if self._menu is not None:
                        pt, injected = self._point(lParam)
                        if injected:
                            consume = True     # это наш же SetCursorPos — гасим
                        elif pt is not None:
                            # Пока меню открыто, движение мыши НЕ пропускаем в
                            # оболочку (иначе она таскает иконку в трее), но сами
                            # двигаем курсор и ведём подсветку по кнопкам.
                            self._menu.hover_at(QPoint(*pt))
                            try:
                                self._user32.SetCursorPos(pt[0], pt[1])
                            except Exception:
                                pass
                            consume = True
                elif wParam == WM_LBUTTONUP:
                    self._down = False
                    self._timer.stop()
                    if self._menu is not None:
                        m, self._menu = self._menu, None
                        pt, _ = self._point(lParam)
                        gp = QPoint(*pt) if pt else QCursor.pos()
                        # UP не съедаем (чтобы не рассинхронить кнопку в оболочке);
                        # трей просто пропустит ближайший тоггл окна.
                        self._tray.suppress_next_toggle()
                        m.release_at(gp)
        except Exception:
            pass
        if consume:
            return 1
        return self._user32.CallNextHookEx(None, nCode, wParam, lParam)

    def _on_down(self):
        if self._over_icon():
            self._down = True
            self._timer.start()

    def _maybe_open(self):
        if self._down and self._menu is None:
            self._menu = self._tray.show_menu_hold()

    def _over_icon(self):
        pos = QCursor.pos()
        try:
            g = self._tray.icon.geometry()
            if g.isValid() and g.width() > 0 and g.height() > 0:
                return g.contains(pos)
        except Exception:
            pass
        # Точный прямоугольник иконки недоступен — берём область трея целиком.
        return self._app._cursor_over_tray()

    def stop(self):
        if self._hook and self._user32 is not None:
            try:
                self._user32.UnhookWindowsHookEx(self._hook)
            except Exception:
                pass
            self._hook = None


# ------------------------------------------------------------------ #
#  Кастомный тост у трея (надёжнее нативного балуна Windows)
# ------------------------------------------------------------------ #
class Toast(QWidget):
    """Небольшой тост в правом нижнем углу. Нативные уведомления Windows часто
    не показываются (Focus Assist / настройки), поэтому рисуем свой. Клик —
    выполнить действие, ✕ — закрыть, авто-скрытие через ~7 c."""

    def __init__(self, app, title, subtitle, on_click):
        super().__init__(None, Qt.FramelessWindowHint | Qt.Tool
                         | Qt.WindowStaysOnTopHint | Qt.WindowDoesNotAcceptFocus
                         | Qt.NoDropShadowWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setCursor(Qt.PointingHandCursor)
        self._app = app
        self._on_click = on_click
        s = app._s
        pal = themes.palette(app.settings.get("theme", themes.DEFAULT_THEME))
        self._bg = QColor(pal["card_bg"])
        self._border = QColor(pal["border"])
        self._title_col = QColor(pal["title"])
        self._muted = QColor(pal["muted"])
        self._accent = QColor(pal["accent"])
        self._radius = s(12)
        self._title = title
        self._sub = subtitle
        self._title_font = fonts.font(s(12), "Semibold")
        self._sub_font = fonts.font(s(10), "Regular")
        self._w, self._h = s(252), s(62)
        self._pad = s(16)
        self._close_r = QRectF(self._w - s(24), s(6), s(18), s(18))
        self.resize(self._w, self._h)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(7000)
        self._timer.timeout.connect(self._dismiss)

    def show_at(self, mode):
        """mode='corner' — правый нижний угол монитора, на котором курсор;
        mode='cursor' — рядом с указателем. Учитывает мультимонитор."""
        cur = QCursor.pos()
        screen = QGuiApplication.screenAt(cur) or QGuiApplication.primaryScreen()
        avail = screen.availableGeometry()
        m = self._app._s(14)
        if mode == "cursor":
            x = cur.x() + m
            y = cur.y() + m
            if x + self._w > avail.right():
                x = cur.x() - self._w - m
            if y + self._h > avail.bottom():
                y = cur.y() - self._h - m
        else:  # corner — угол того монитора, где мышь
            x = avail.right() - self._w - m
            y = avail.bottom() - self._h - m
        x = max(avail.left(), min(x, avail.right() - self._w))
        y = max(avail.top(), min(y, avail.bottom() - self._h))
        self.move(x, y)
        self.show()
        self.raise_()
        anim.fade(self, 0.0, 1.0, 200)
        self._timer.start()

    def _dismiss(self):
        self._timer.stop()
        anim.fade(self, 1.0, 0.0, 180, on_finished=self.close)

    def mouseReleaseEvent(self, event):
        self._timer.stop()
        # ПКМ — просто закрыть тост; ✕ — тоже закрыть; ЛКМ — запустить загрузку.
        if event.button() == Qt.RightButton or self._close_r.contains(event.position()):
            self._dismiss()
            return
        cb = self._on_click
        self.close()
        if cb:
            QTimer.singleShot(0, cb)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        s = self._app._s
        w, h = self.width(), self.height()
        p.setPen(QPen(self._border, 1))
        p.setBrush(self._bg)
        p.drawRoundedRect(QRectF(0.5, 0.5, w - 1, h - 1), self._radius, self._radius)
        # акцентная полоска слева
        p.setPen(Qt.NoPen)
        p.setBrush(self._accent)
        p.drawRoundedRect(QRectF(s(7), h / 2 - s(12), s(3), s(24)), s(1.5), s(1.5))
        tx = self._pad + s(2)
        p.setFont(self._title_font)
        p.setPen(self._title_col)
        p.drawText(QRectF(tx, s(9), w - tx - s(22), s(20)),
                   Qt.AlignVCenter | Qt.AlignLeft, self._title)
        p.setFont(self._sub_font)
        p.setPen(self._muted)
        fm = QFontMetrics(self._sub_font)
        sub = fm.elidedText(self._sub, Qt.ElideRight, int(w - tx - self._pad))
        p.drawText(QRectF(tx, s(31), w - tx - self._pad, s(18)),
                   Qt.AlignVCenter | Qt.AlignLeft, sub)
        # ✕ (пожирнее и заметнее)
        cr = self._close_r
        xpen = QPen(self._muted, max(2.0, s(2.2)))
        xpen.setCapStyle(Qt.RoundCap)
        p.setPen(xpen)
        p.drawLine(QPointF(cr.left() + s(4), cr.top() + s(4)),
                   QPointF(cr.right() - s(4), cr.bottom() - s(4)))
        p.drawLine(QPointF(cr.right() - s(4), cr.top() + s(4)),
                   QPointF(cr.left() + s(4), cr.bottom() - s(4)))
        p.end()


# ------------------------------------------------------------------ #
#  Иконка в системном трее
# ------------------------------------------------------------------ #
class TrayIcon:
    def __init__(self, app):
        self.app = app
        self.icon = None
        self.animator = None
        self._menu_popup = None
        self._hold_menu = None
        self._hold_watcher = None
        self._suppress_toggle = False
        self._toast = None             # активный кастомный тост «Скачать это?»
        self._build_icon()

    def _default_icon(self):
        img = QImage(64, 64, QImage.Format_ARGB32)
        img.fill(Qt.transparent)
        painter = QPainter(img)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QBrush(QColor("#3B82F6")))
        painter.drawEllipse(4, 4, 56, 56)
        painter.setBrush(QBrush(QColor("white")))
        tri = QPolygonF([QPointF(22, 20), QPointF(22, 44), QPointF(46, 32)])
        painter.drawPolygon(tri)
        painter.end()
        return QIcon(QPixmap.fromImage(img))

    def _resolve_icon(self):
        """Иконка из icons/<tray_icon>.png, перекрашенная под тему панели задач
        Windows (чёрная на светлой панели, белая на тёмной). Пусто -> дефолт."""
        name = self.app.settings.get("tray_icon", "")
        if name:
            path = os.path.join(ICONS_DIR, name + ".png")
            if os.path.isfile(path):
                if name in COLORED_ICONS:      # цветные (play) — не перекрашиваем
                    pm = raw_pixmap(path, 64)
                else:
                    color = "#000000" if tools.windows_uses_light_theme() else "#ffffff"
                    pm = tint_pixmap(path, color, 64)
                if pm is not None and not pm.isNull():
                    return QIcon(pm)
        return self._default_icon()

    def base_pixmap(self, size):
        """Пиксмап текущей пользовательской иконки (для кроссфейда анимации)."""
        return self._resolve_icon().pixmap(size, size)

    def _build_icon(self):
        self.icon = QSystemTrayIcon(self._resolve_icon(), self.app)
        self.icon.setToolTip("Snatchr")
        # Контекстное меню (ПКМ) рисуем сами — нативное QMenu не ставим.
        self.icon.activated.connect(self._on_activated)
        self.animator = TrayAnimator(self)
        # Меню по зажатию ЛКМ пока отключено (класс TrayHoldWatcher оставлен на
        # будущее). ЛКМ работает как обычно (open/close окна), меню — только ПКМ.
        self._hold_watcher = None

    def set_icon(self, name):
        """Сменить иконку трея на лету (name — имя файла без расширения)."""
        self.app.settings["tray_icon"] = name or ""
        if self.icon is not None and not (self.animator and self.animator.is_active()):
            self.icon.setIcon(self._resolve_icon())

    def notify(self, text, title="Snatchr"):
        try:
            self.icon.showMessage(title, text, QSystemTrayIcon.Information, 3500)
        except Exception:
            pass

    def show_toast(self, title, subtitle, on_click=None, position="corner"):
        """Показать кастомный тост (предыдущий закрывается)."""
        if self._toast is not None:
            try:
                self._toast.close()
            except Exception:
                pass
        self._toast = Toast(self.app, title, subtitle, on_click)
        self._toast.show_at(position)

    def toast_download(self, url, title):
        """Тост «Скачать это?»: клик — фоновая загрузка url, ✕ — закрыть.
        Позиция — по настройке (угол / у курсора)."""
        self.show_toast(title, url, lambda u=url: self.app.on_toast_clicked(u),
                        self.app.settings.get("toast_position", "corner"))

    # --- события трея -------------------------------------------------- #
    def suppress_next_toggle(self):
        """После hold-жеста подавить ближайший тоггл окна (флаг сам сбрасывается,
        если Trigger так и не пришёл — напр., отпустили не над иконкой)."""
        self._suppress_toggle = True
        QTimer.singleShot(350, self._clear_suppress)

    def _clear_suppress(self):
        self._suppress_toggle = False

    def _on_activated(self, reason):
        # ЛКМ (Trigger) — открыть/закрыть окно (режим Pinned/Auto-hide).
        # Быстрый повторный клик во время анимации Windows приходит как
        # DoubleClick — его тоже считаем кликом, иначе второй клик теряется.
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            if self._suppress_toggle:
                self._suppress_toggle = False
                return
            self._toggle_app()
        elif reason == QSystemTrayIcon.Context:
            self._show_menu()

    def _menu_items(self):
        # Во время фоновой загрузки (Paste) первый пункт — Stop (как в окне).
        if self.app.is_tray_downloading():
            first = (tr("Stop"), self._stop_paste)
        else:
            first = (tr("Paste"), self._paste)
        return [first, (tr("Open"), self._show_app), (tr("Exit"), self._quit_app)]

    def _show_menu(self):
        self._menu_popup = TrayMenu(self.app, self._menu_items())
        self._menu_popup.popup_at(QCursor.pos())

    def close_menus(self):
        """Закрыть открытое контекстное меню (напр., по завершении фонового Job —
        иначе останется устаревшая кнопка Stop)."""
        for m in (self._menu_popup, self._hold_menu):
            if m is not None:
                try:
                    m.close()
                except Exception:
                    pass
        self._menu_popup = None
        self._hold_menu = None

    def show_menu_hold(self):
        """Меню по зажатию ЛКМ; ведётся из TrayHoldWatcher. Возвращает виджет."""
        self._hold_menu = TrayMenu(self.app, self._menu_items(), hold=True)
        self._hold_menu.popup_at(QCursor.pos())
        return self._hold_menu

    # --- действия меню ------------------------------------------------- #
    def _paste(self):
        text = ""
        try:
            text = QApplication.clipboard().text() or ""
        except Exception:
            pass
        self.app.start_tray_download(text.strip())

    def _stop_paste(self):
        self.app.stop_tray_download()

    def _toggle_app(self):
        self.app.toggle_window()

    def _show_app(self):
        self.app.show_near_tray()

    def _quit_app(self):
        if self._hold_watcher is not None:
            self._hold_watcher.stop()
        self.icon.hide()
        QApplication.instance().quit()

    def run(self):
        self.icon.show()
