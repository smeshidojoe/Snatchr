import math
import os

from PySide6.QtCore import (
    Qt, QRectF, QPointF, QPoint, QEvent, QPropertyAnimation, QEasingCurve, QTimer
)
from PySide6.QtGui import (
    QPainter, QPainterPath, QRadialGradient, QColor, QPen, QBrush, QGuiApplication,
    QCursor
)
from PySide6.QtWidgets import QWidget, QApplication

import win32gui
import win32api
import win32process

from core import config
from core import fonts
from core import i18n
from core import themes
from ui import anim
from ui.bottom_bar import BottomBar
from ui.main_page import MainPage
from ui.settings_page import SettingsPage
from ui.about_page import AboutPage


class App(QWidget):
    def __init__(self):
        super().__init__()

        # Регистрируем кастомные шрифты до создания виджетов.
        fonts.load()

        # Настройки загружаются с диска (персистентность между сессиями).
        self.settings = config.load()
        i18n.set_language(self.settings.get("language", "English"))

        # Масштаб авто по разрешению экрана. Линейный пересчёт через две точки:
        # 1080p (raw 0.75) -> 0.85, 1440p (raw 1.0) -> 1.0  =>  scale = raw*0.6 + 0.4.
        screen = QGuiApplication.primaryScreen()
        geo = screen.geometry()
        raw = min(geo.width() / 2560, geo.height() / 1440)
        scale = max(0.6, min(raw * 0.6 + 0.4, 1.4))
        # На экранах < 1440p (напр. 1080p) текст выходил слишком мелким (масштаб
        # 0.7–0.85). Поднимаем до ~0.95, но не настолько, чтобы окно настроек не
        # поместилось по высоте (защита для маленьких/сильно масштабированных
        # экранов). 1440p и выше не затрагиваются — там масштаб уже >= 0.95.
        avail_h = screen.availableGeometry().height()
        fit_cap = avail_h / 560.0        # ~высота окна настроек в базовых единицах
        self._base_scale = max(scale, min(0.95, fit_cap))
        self._recompute_dims()

        # Трей выставляется из main.py после создания окна.
        self.tray = None

        # Цвета окна — из палитры выбранной темы.
        self._load_window_colors()

        self.current_page = "main"
        self._nav_busy = False

        # Режим работы окна:
        #   "toggle" (Pinned)   — клик по иконке открывает/закрывает окно;
        #   "focus"  (Auto-hide)— клик открывает, закрытие по Esc / клику вне окна.
        self.usage_mode = self.settings.get("usage_mode", "toggle")
        self._suppress_autohide = False
        # Защита от мгновенного скрытия до того, как окно успело активироваться.
        self._became_active = False

        # Перетаскивание окна (за верхнюю пустую область).
        self.allow_dragging = bool(self.settings.get("allow_dragging", False))
        self._drag_offset = None
        # DRAG_ZONE_H задаётся в _recompute_dims().

        # Состояние показа окна (для устойчивости к быстрым кликам по трею).
        self._shown = False
        self._appearing = False   # идёт анимация появления (можно перебить кликом)
        self._final_pos = None
        self._win_pos_anim = None
        self._win_op_anim = None
        self._tray_edge = "top"   # где панель задач: top|bottom|left|right

        # Обновление yt-dlp (модальный оверлей блокирует окно на время процесса).
        self._update_overlay = None
        self._update_worker = None
        self._updating = False

        # Окно: безрамочное, всегда поверх, без кнопки на панели задач.
        self.setWindowTitle("Snatchr")
        self.setWindowFlags(
            Qt.FramelessWindowHint | Qt.Tool | Qt.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setFixedSize(self.WIN_W, self.WIN_H)

        # Авто-скрытие в режиме Auto-hide: реакция на потерю активности
        # приложения (клик вне окна) и на Esc (через глобальный фильтр событий).
        QApplication.instance().applicationStateChanged.connect(self._on_app_state)
        QApplication.instance().installEventFilter(self)

        # Страницы создаются заранее и прячутся (показывается только активная).
        self._build_pages()

        # Бинарники докачиваем НЕ при старте, а после первого открытия окна
        # (см. _maybe_first_run_setup). Здесь только держим ссылки/флаг.
        self._setup_worker = None
        self._first_run_checked = False

        # Мониторинг буфера обмена (тост «Скачать это?»). Подключаем, если включено.
        self._clip_last = ""
        self._clip_connected = False
        self.apply_clipboard_watch()
        # Отметка времени последнего изменения буфера (для автовставки — вставляем
        # только «свежую» ссылку). Подключение постоянное, не зависит от watch.
        self._clip_change_ts = 0.0
        QApplication.clipboard().dataChanged.connect(self._stamp_clip)

        # Spotlight (Ctrl+Shift+D) — создаётся лениво при первом вызове хоткея.
        self.spotlight = None
        # Координатор спиннера в трее: любая активная загрузка (окно/спотлайт/Paste/
        # Toast) — иконка крутится; когда суммарно 0 — галочка. Каждый источник
        # сообщает своё число активных загрузок.
        self._active_counts = {}
        self._ring_on = False
        self._toast_ring_id = None      # id Toast-загрузки, владеющей детерм. кольцом
        self._ring_cooldown = False     # идёт завершающая анимация (галочка/крестик)
        self._toast_active = False      # идёт Toast-загрузка — новые тосты не показываем
        # Периодически убираем из истории удалённые с диска файлы (для обоих окон).
        self._hist_prune_timer = QTimer(self)
        self._hist_prune_timer.setInterval(1000)
        self._hist_prune_timer.timeout.connect(self._prune_histories)
        self._hist_prune_timer.start()
        self._hotkey = None
        self._apply_hotkey()
        QApplication.instance().aboutToQuit.connect(self._stop_hotkey)

    # ------------------------------------------------------------------ #
    def _s(self, value):
        return int(value * self.scale)

    def save_settings(self):
        config.save(self.settings)

    # ------------------------------------------------------------------ #
    #  Построение страниц / применение темы и языка
    # ------------------------------------------------------------------ #
    def _recompute_dims(self):
        """Пересчёт всех размеров окна по масштабу (авто, по разрешению экрана)."""
        self.scale = self._base_scale

        self.WIN_W          = self._s(492)
        self.WIN_H_FULL     = self._s(480)
        self.WIN_H_SETTINGS = self.WIN_H_FULL + self._s(70)    # фикс.; остальное — скролл
        self.WIN_H_ABOUT    = self._s(480)
        self.WIN_H          = self.WIN_H_FULL
        self.CORNER_R       = self._s(14)
        self.BORDER_W       = max(2, self._s(3))
        self.BAR_H          = self._s(48)
        self.DRAG_ZONE_H    = self._s(64)

        self.content_x = self.BORDER_W
        self.content_y = self.BORDER_W
        self.content_w = self.WIN_W - 2 * self.BORDER_W
        self.content_h = self.WIN_H_SETTINGS - self.BAR_H - self.BORDER_W
        self.about_content_h = self.WIN_H_ABOUT - self.BAR_H - self.BORDER_W

    def _crossfade(self, snap):
        """Плавный кросс-фейд: поверх нового окна показываем снимок старого и гасим."""
        from PySide6.QtWidgets import QLabel
        ov = QLabel(self)
        ov.setPixmap(snap)
        ov.setGeometry(0, 0, snap.width(), snap.height())
        ov.raise_()
        ov.show()
        anim.fade(ov, 1.0, 0.0, 300, on_finished=ov.deleteLater)

    def _load_window_colors(self):
        """Цвета фона/рамки окна из палитры текущей темы."""
        pal = themes.palette(self.settings.get("theme", themes.DEFAULT_THEME))
        self.BORDER_COLOR = pal["border"]
        self.BG_COLOR     = pal["bg"]
        self.GRAD_CENTER  = pal["grad_center"]
        self.GRAD_EDGE    = pal["grad_edge"]
        self.PAGE_BG      = pal["page_bg"]
        self.SEP_COLOR    = pal["separator"]

    def _build_pages(self):
        """Создаёт страницы и нижнюю панель (вызывается при старте и при смене
        темы/языка — тогда старые виджеты предварительно удаляются)."""
        self.settings_page = SettingsPage(self, self, self.settings,
                                          self.content_w, self.content_h)
        self.settings_page.setGeometry(self.content_x, self.content_y,
                                       self.content_w, self.content_h)
        self.settings_page.hide()

        self.about_page = AboutPage(self, self, self.settings,
                                    self.content_w, self.about_content_h)
        self.about_page.setGeometry(self.content_x, self.content_y,
                                    self.content_w, self.about_content_h)
        self.about_page.hide()

        self.main_content_h = self.WIN_H_FULL - self.BAR_H - self.BORDER_W
        self.main_page = MainPage(self, self, self.settings,
                                  self.content_w, self.main_content_h)
        self.main_page.setGeometry(self.content_x, self.content_y,
                                   self.content_w, self.main_content_h)

        self.bottom_bar = BottomBar(self, self, self.settings,
                                    self.WIN_W, self.BAR_H)
        self.bottom_bar.reposition()

        self.settings_page.ensurePolished()
        self.about_page.ensurePolished()
        self.main_page.ensurePolished()

    def apply_appearance(self):
        """Применяет тему/язык немедленно: перестраивает страницы и
        перерисовывает окно. Вызывается из About при смене темы/языка.
        Перестроение откладываем на следующий тик событий — нельзя удалять
        страницу прямо внутри сигнала её же селектора."""
        QTimer.singleShot(0, self._apply_appearance_now)

    def _apply_appearance_now(self):
        if self._updating or self.main_page.is_busy():
            return
        snap = self.grab()                      # кадр старого вида для кросс-фейда
        i18n.set_language(self.settings.get("language", "English"))
        self._load_window_colors()

        # Spotlight кэширует палитру при создании — пересоздадим при смене темы/языка.
        if self.spotlight is not None:
            self.spotlight.hide_spotlight()
            self.spotlight.deleteLater()
            self.spotlight = None

        # Запоминаем позицию прокрутки настроек, чтобы после пересборки (смена
        # темы/языка из блока Usage) остаться на том же месте, а не улететь наверх.
        scroll_pos = 0
        try:
            scroll_pos = self.settings_page._scroll_area.verticalScrollBar().value()
        except Exception:
            pass

        # Удаляем старые страницы и панель. Кнопки нижней панели привязаны к окну,
        # поэтому их убираем отдельно (иначе остаются «фантомные» кнопки).
        self.bottom_bar.teardown()
        for w in (self.settings_page, self.about_page, self.main_page, self.bottom_bar):
            w.setParent(None)
            w.deleteLater()

        self._build_pages()

        # Тему/язык меняют на странице настроек — на ней и остаёмся.
        self.current_page = "settings"
        self.set_window_height(self.WIN_H_SETTINGS)
        self.bottom_bar.set_page_mode("settings")
        self.settings_page.setGeometry(self.content_x, self.content_y,
                                       self.content_w, self.content_h)
        self.settings_page.show()
        self.settings_page.raise_()
        # Восстанавливаем позицию прокрутки на новой (пересобранной) странице.
        try:
            self.settings_page._scroll_area.verticalScrollBar().setValue(scroll_pos)
        except Exception:
            pass
        self.update()
        self._crossfade(snap)                   # плавный переход к новой теме

    # ------------------------------------------------------------------ #
    #  Отрисовка фона: скруглённый прямоугольник + радиальный градиент + рамка
    # ------------------------------------------------------------------ #
    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)

        w, h = self.width(), self.height()
        r = self.CORNER_R

        path = QPainterPath()
        path.addRoundedRect(QRectF(0, 0, w, h), r, r)

        cx, cy = w / 2.0, h / 2.0
        radius = math.hypot(cx, cy)
        grad = QRadialGradient(QPointF(cx, cy), radius)
        grad.setColorAt(0.0, QColor(self.GRAD_CENTER))
        grad.setColorAt(1.0, QColor(self.GRAD_EDGE))
        p.fillPath(path, QBrush(grad))

        bw = self.BORDER_W
        border_path = QPainterPath()
        border_path.addRoundedRect(
            QRectF(bw / 2.0, bw / 2.0, w - bw, h - bw),
            max(1, r - 2), max(1, r - 2)
        )
        pen = QPen(QColor(self.BORDER_COLOR), bw)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        p.drawPath(border_path)

        # Тонкая разделительная линия над нижней панелью (цвет — как у иконок).
        # Позиция: равное расстояние от линии до иконок и от иконок до низа окна.
        sep_y = h - self.BAR_H
        sep_pen = QPen(QColor(self.SEP_COLOR), max(1, self._s(1)))
        p.setPen(sep_pen)
        p.drawLine(int(bw + self._s(6)), int(sep_y),
                   int(w - bw - self._s(6)), int(sep_y))
        p.end()

    # ------------------------------------------------------------------ #
    #  Навигация между экранами (главная -> настройки -> about). Без анимаций.
    # ------------------------------------------------------------------ #
    def on_left_button(self):
        """Левая кнопка панели: шестерёнка на главной, иначе «назад»."""
        if self._nav_busy:
            return
        if self.current_page == "main":
            self.open_settings()
        elif self.current_page == "settings":
            self.close_settings()
        elif self.current_page == "about":
            self.close_about()

    def open_settings(self):
        if self.current_page != "main" or self._nav_busy:
            return
        self._nav_busy = True
        self.current_page = "settings"
        self.bottom_bar.set_page_mode("settings")
        self.about_page.hide()
        self.settings_page.setGeometry(self.content_x, self.content_y,
                                       self.content_w, self.content_h)
        self._animate_height(self.WIN_H_SETTINGS)   # Settings выше базовой
        self.settings_page.show()
        self.settings_page.raise_()
        anim.fade(self.main_page, 1.0, 0.0, 180, on_finished=self.main_page.hide)
        anim.fade(self.settings_page, 0.0, 1.0, 200, on_finished=self._nav_done)

    def close_settings(self):
        if self.current_page != "settings" or self._nav_busy:
            return
        self._nav_busy = True
        self.current_page = "main"
        self.bottom_bar.set_page_mode("main")
        # Учитываем расширение главной (Multiple Links): возвращаемся к нужной высоте.
        extra = self.main_page.expand_extra()
        target = self.WIN_H_FULL + extra
        new_ch = target - self.BAR_H - self.BORDER_W
        self.main_page._extra = extra
        self.main_page.setGeometry(self.content_x, self.content_y, self.content_w, new_ch)
        self.main_page.relayout(new_ch)
        self._animate_height(target)                 # одновременно с фейдом
        self.main_page.show()
        self.main_page.raise_()
        self.bottom_bar.btn_settings.raise_()
        self.bottom_bar.btn_folder.raise_()
        self.bottom_bar.btn_exit.raise_()

        def done():
            self.settings_page.hide()
            self._nav_done()

        anim.fade(self.settings_page, 1.0, 0.0, 200, on_finished=done)
        anim.fade(self.main_page, 0.0, 1.0, 200)

    def open_about(self):
        if self.current_page != "settings" or self._nav_busy:
            return
        self._nav_busy = True
        self.current_page = "about"
        self.bottom_bar.set_page_mode("about")
        self._animate_height(self.WIN_H_ABOUT)       # одновременно с фейдом
        self.about_page.setGeometry(self.content_x, self.content_y,
                                    self.content_w, self.about_content_h)

        def after_out():
            self.settings_page.hide()
            self.about_page.show()
            self.about_page.raise_()
            anim.fade(self.about_page, 0.0, 1.0, 200, on_finished=self._nav_done)

        anim.fade(self.settings_page, 1.0, 0.0, 180, on_finished=after_out)

    def close_about(self):
        if self.current_page != "about" or self._nav_busy:
            return
        self._nav_busy = True
        self.current_page = "settings"
        self.bottom_bar.set_page_mode("settings")
        self._animate_height(self.WIN_H_SETTINGS)    # одновременно с фейдом
        self.settings_page.setGeometry(self.content_x, self.content_y,
                                       self.content_w, self.content_h)

        def after_out():
            self.about_page.hide()
            self.settings_page.show()
            self.settings_page.raise_()
            anim.fade(self.settings_page, 0.0, 1.0, 200, on_finished=self._nav_done)

        anim.fade(self.about_page, 1.0, 0.0, 180, on_finished=after_out)

    def _nav_done(self):
        self._nav_busy = False

    def set_main_expanded(self, expanded):
        """
        Плавно растит/сжимает главное окно на 50px (режим Multiple Links),
        анимируя одновременно высоту окна, поле ввода и сдвиг кнопок —
        одной анимацией, чтобы ничего не «прыгало».
        """
        if self.current_page != "main":
            return
        target_extra = self._s(50) if expanded else 0
        start_extra = self.main_page._extra
        base = self.WIN_H_FULL
        x0 = self.x()
        top0 = self.y()
        bottom0 = self.y() + self.height()
        grow_up = (self._tray_edge == "bottom")

        def apply(e):
            e = int(round(e))
            h = base + e
            self.WIN_H = h
            self.setFixedSize(self.WIN_W, h)
            self.move(x0, bottom0 - h if grow_up else top0)
            self.bottom_bar.reposition()
            new_ch = h - self.BAR_H - self.BORDER_W
            self.main_page._extra = e
            self.main_page.setGeometry(self.content_x, self.content_y,
                                       self.content_w, new_ch)
            self.main_page.relayout(new_ch)
            self.update()

        anim.animate(self, start_extra, target_extra, 300, apply,
                     easing=QEasingCurve.OutCubic, attr="_main_exp_anim")

    def _animate_height(self, target):
        """
        Плавно меняет высоту окна. Если панель задач снизу — окно растёт вверх
        (низ зафиксирован у трея), иначе вниз (верх зафиксирован). Верхние
        элементы остаются привязанными к верхней части окна.
        """
        start = self.height()
        if start == target:
            self.WIN_H = target
            return

        x0 = self.x()
        top0 = self.y()
        bottom0 = self.y() + start
        grow_up = (self._tray_edge == "bottom")

        def apply(h):
            self.WIN_H = h
            self.setFixedSize(self.WIN_W, h)
            self.move(x0, bottom0 - h if grow_up else top0)
            self.bottom_bar.reposition()
            self.update()

        anim.animate(self, start, target, 260, lambda v: apply(int(round(v))),
                     easing=QEasingCurve.OutCubic,
                     on_finished=lambda: apply(target), attr="_h_anim")

    def set_window_height(self, new_h):
        """Мгновенно меняет высоту окна (используется при скрытом окне)."""
        if new_h == self.WIN_H:
            return
        self.WIN_H = new_h
        self.setFixedSize(self.WIN_W, new_h)
        self.bottom_bar.reposition()
        self.update()

    # ------------------------------------------------------------------ #
    #  Режим работы окна + авто-скрытие (Auto-hide)
    # ------------------------------------------------------------------ #
    def set_usage_mode(self, mode):
        """Сменить режим окна (Pinned/Auto-hide) и сохранить выбор."""
        self.usage_mode = mode
        self.settings["usage_mode"] = mode
        self.save_settings()

    def set_allow_dragging(self, value):
        """Включить/выключить перетаскивание окна и сохранить выбор."""
        self.allow_dragging = bool(value)
        self.settings["allow_dragging"] = self.allow_dragging
        self.save_settings()

    def suppress_autohide(self, value):
        """Временно отключить авто-скрытие (например, на время диалога)."""
        self._suppress_autohide = bool(value)

    def _on_app_state(self, state):
        # В режиме Auto-hide прячем окно, когда приложение теряет активность
        # (клик мышью вне окна программы). Но если фокус ушёл из-за клика по
        # самой панели задач/трею — окно НЕ гасим (иконка не должна закрывать).
        if state == Qt.ApplicationActive:
            self._became_active = True
            return
        if (self.usage_mode == "focus" and self.isVisible()
                and self._became_active
                and not self._suppress_autohide
                and not self._cursor_over_tray()
                and state == Qt.ApplicationInactive):
            self.hide_window()

    def _cursor_over_tray(self):
        """
        Курсор над областью значков трея? (Только сам трей, не вся панель —
        клик по Пуску/другому окну окно гасит, клик по нашей иконке — нет.)
        """
        try:
            pos = QCursor.pos()
            tb = win32gui.FindWindow("Shell_TrayWnd", None)
            if not tb:
                return False
            tray = win32gui.FindWindowEx(tb, 0, "TrayNotifyWnd", None) or tb
            l, t, r, b = win32gui.GetWindowRect(tray)
            return l <= pos.x() <= r and t <= pos.y() <= b
        except Exception:
            return False

    def eventFilter(self, obj, event):
        # Глобальный перехват Esc в режиме Auto-hide.
        if (self.usage_mode == "focus" and self.isVisible()
                and event.type() == QEvent.KeyPress
                and event.key() == Qt.Key_Escape):
            if self._updating:
                return True   # во время обновления Esc не закрывает окно
            self.hide_window()
            return True
        return super().eventFilter(obj, event)

    # ------------------------------------------------------------------ #
    #  Показ/скрытие у трея с анимацией (fade + сдвиг). Устойчиво к быстрым
    #  кликам: при перебивании анимация продолжается с текущей точки.
    # ------------------------------------------------------------------ #
    # ------------------------------------------------------------------ #
    #  Обновление yt-dlp по требованию (кнопка в настройках).
    # ------------------------------------------------------------------ #
    def start_ytdlp_update(self):
        from core.workers import UpdateYtdlpWorker
        ch = self.settings.get("ytdlp_channel", "stable")
        self._show_overlay("Updating yt-dlp…", UpdateYtdlpWorker(ch, False, self))

    def set_ytdlp_channel(self, channel):
        """Переключить канал yt-dlp (stable/nightly) и сделать бинарь активным
        (из кэша мгновенно либо докачать)."""
        if channel == self.settings.get("ytdlp_channel"):
            return
        self.settings["ytdlp_channel"] = channel
        self.save_settings()
        from core.workers import UpdateYtdlpWorker
        self._show_overlay("Switching yt-dlp…", UpdateYtdlpWorker(channel, True, self))

    def start_ffmpeg_update(self):
        from core.workers import UpdateFfmpegWorker
        self._show_overlay("Updating ffmpeg…", UpdateFfmpegWorker(self))

    def start_app_update(self, url):
        """Скачивает обновление приложения (с прогрессом в оверлее), затем
        перезапускается через внешнего помощника, который заменит exe."""
        from core.workers import AppUpdateWorker
        from core.i18n import tr
        self._show_overlay(tr("Downloading update…"), AppUpdateWorker(url, self),
                           on_done=self._on_app_update_downloaded)

    def _on_app_update_downloaded(self, ok, err):
        if not ok:
            return
        from core import updater
        if updater.restart_to_update():
            # Немедленно и жёстко выходим: onefile-процесс должен освободить exe,
            # чтобы помощник смог его подменить (обычный quit может задержаться из-за
            # фоновых потоков — тогда файл остаётся залоченным и апдейт не проходит).
            if self.tray is not None:
                self.tray.icon.hide()
            os._exit(0)
        # Если не frozen (разработка) — апдейт применится при следующем ручном запуске.

    # ------------------------------------------------------------------ #
    #  Фоновая проверка обновлений + тост-анонс
    # ------------------------------------------------------------------ #
    def start_update_watch(self):
        """Тихая проверка новых версий: первая через ~8 c после старта, далее
        периодически (раз в 6 ч). Только для собранного exe."""
        from core import updater
        if not updater.is_frozen():
            return
        self._update_timer = QTimer(self)
        self._update_timer.setInterval(6 * 60 * 60 * 1000)   # 6 часов
        self._update_timer.timeout.connect(self._check_update_bg)
        self._update_timer.start()
        QTimer.singleShot(8000, self._check_update_bg)

    def set_update_notify(self, on):
        self.settings["update_notify"] = bool(on)
        self.save_settings()

    def _check_update_bg(self):
        if not self.settings.get("update_notify", True):
            return
        from core.workers import UpdateCheckWorker
        self._upd_check = UpdateCheckWorker(self)
        self._upd_check.done.connect(self._on_update_check)
        self._upd_check.start()

    def _on_update_check(self, result):
        if not isinstance(result, dict) or result.get("status") != "available":
            return
        if not self.settings.get("update_notify", True):
            return
        version = result.get("version") or ""
        url = result.get("download_url")
        if not url:
            return
        if version and version == self.settings.get("update_dismissed_version", ""):
            return                       # этот апдейт уже отклонили
        self._pending_update = {"version": version, "url": url}
        if self.tray is not None:
            from core.i18n import tr
            self.tray.show_toast(tr("Update available"), version,
                                 on_click=self._on_update_toast_click,
                                 position="corner", sticky=True,
                                 on_dismiss=self._on_update_toast_dismiss)

    def _on_update_toast_click(self):
        pu = getattr(self, "_pending_update", None)
        if not pu:
            return
        self.show_near_tray()            # показать окно
        self.open_about()                # перейти на About
        # запустить скачивание с уже известным URL (как кнопка в About)
        QTimer.singleShot(350, lambda u=pu["url"]: self.start_app_update(u))

    def _on_update_toast_dismiss(self):
        pu = getattr(self, "_pending_update", None)
        v = pu.get("version") if pu else ""
        if v:
            self.settings["update_dismissed_version"] = v
            self.save_settings()

    def _maybe_first_run_setup(self):
        """После первого открытия окна: если бинарников нет (yt-dlp/ffmpeg/
        ffprobe) — качаем их в блокирующем оверлее."""
        from core import tools
        from core.workers import SetupWorker
        # Окно успели закрыть до срабатывания таймера — повторим при следующем
        # открытии (чтобы блокирующий оверлей не возник на скрытом окне).
        if not self.isVisible():
            self._first_run_checked = False
            return
        self._maybe_setup_deno()          # тихо докачиваем deno в фоне (JS-движок)
        ch = self.settings.get("ytdlp_channel", "stable")
        if tools.have_ytdlp() and tools.have_ffmpeg():
            self._maybe_autoupdate_ytdlp()
            return
        self._show_overlay("Downloading yt-dlp…", SetupWorker(ch, self),
                           on_done=self._on_setup_done)

    def _maybe_setup_deno(self):
        """Фоновая (не блокирующая) докачка deno, затем — PO-token провайдер (ему
        нужен deno). Всё best-effort."""
        from core import tools
        if tools.have_deno():
            self._maybe_setup_pot()
            return
        from core.workers import EnsureDenoWorker
        self._deno_worker = EnsureDenoWorker(self)
        self._deno_worker.done.connect(lambda ok: self._maybe_setup_pot() if ok else None)
        self._deno_worker.start()

    def _maybe_setup_pot(self):
        """Фоновая установка PO-token провайдера (обход YouTube 403), если его нет
        и есть deno — best-effort."""
        from core import tools
        if tools.have_pot() or not tools.have_deno():
            return
        from core.workers import EnsurePotWorker
        self._pot_worker = EnsurePotWorker(self)
        self._pot_worker.start()

    def _on_setup_done(self, ok, err):
        if ok:
            self._mark_ytdlp_updated()   # только что скачали свежий yt-dlp
        self.main_page.on_tools_ready(ok, err)

    def _maybe_autoupdate_ytdlp(self):
        """Раз в сутки тихо обновляем yt-dlp в фоне: YouTube часто ломает старую
        версию, из-за чего ссылки перестают читаться."""
        import time
        from core import tools
        if not tools.have_ytdlp() or self.main_page.is_busy():
            return
        last = float(self.settings.get("ytdlp_updated", 0) or 0)
        if time.time() - last < 14 * 86400:      # не чаще раза в 2 недели (exe-версия)
            return
        from core.workers import YtdlpAutoUpdateWorker
        ch = self.settings.get("ytdlp_channel", "stable")
        self._ytdlp_upd = YtdlpAutoUpdateWorker(ch, self)
        self._ytdlp_upd.done.connect(lambda ok: self._mark_ytdlp_updated() if ok else None)
        self._ytdlp_upd.start()

    def _mark_ytdlp_updated(self):
        import time
        self.settings["ytdlp_updated"] = int(time.time())
        self.save_settings()

    # ------------------------------------------------------------------ #
    #  Spotlight (Ctrl+Shift+D) + единая история скачиваний
    # ------------------------------------------------------------------ #
    def _apply_hotkey(self):
        """(Пере)регистрирует глобальный хоткей по текущим настройкам."""
        self._stop_hotkey()
        if not self.settings.get("spotlight_enabled", True):
            return
        from core.hotkey import HotkeyManager
        combo = self.settings.get("spotlight_combo", "ctrl+shift+d")
        self._hotkey = HotkeyManager(combo, self)
        self._hotkey.triggered.connect(self.toggle_spotlight)
        self._hotkey.start()

    def _stop_hotkey(self):
        if self._hotkey is not None:
            self._hotkey.stop()
            self._hotkey = None

    def _ensure_spotlight(self):
        """Создаёт (но не показывает) окно Spotlight, если его ещё нет. Нужно для
        фоновых загрузок (Paste/Toast), которые кладут строку в историю, даже
        когда Spotlight не открывали."""
        if self.spotlight is None:
            from ui.spotlight import Spotlight
            self.spotlight = Spotlight(self)
        return self.spotlight

    def toggle_spotlight(self):
        self._ensure_spotlight().toggle()

    # --- настройки Spotlight ------------------------------------------- #
    def set_spotlight_enabled(self, on):
        self.settings["spotlight_enabled"] = bool(on)
        self.save_settings()
        self._apply_hotkey()
        if not on and self.spotlight is not None:
            self.spotlight.hide_spotlight()

    def set_spotlight_dismiss(self, mode):
        self.settings["spotlight_dismiss"] = mode
        self.save_settings()

    def set_spotlight_combo(self, combo):
        self.settings["spotlight_combo"] = combo
        self.save_settings()
        self._apply_hotkey()

    def suspend_hotkey(self):
        """Временно снять глобальный хоткей (на время захвата новой комбинации в
        настройках — иначе нажатие текущего сочетания заодно откроет Spotlight)."""
        self._stop_hotkey()

    def resume_hotkey(self):
        self._apply_hotkey()

    def record_download(self, dest, url, title=None, notify_window=True,
                        thumb_bytes=None, thumb_url=None, uploader=None):
        """Пишет скачанный файл в единую историю и (вживую) отражает его в окне.
        Идущие загрузки зеркалятся отдельно (mirror_*); сюда попадают только
        завершённые (в т.ч. обрезанные клипы). Возвращает запись или None.
        thumb_bytes/thumb_url — обложка-постер (приоритетнее кадра ffmpeg)."""
        from core import history
        entry = history.add(dest, url, title, thumb_bytes=thumb_bytes,
                            thumb_url=thumb_url, uploader=uploader)
        if entry is not None and notify_window:
            self.main_page.on_external_download(entry)
        return entry

    # ------------------------------------------------------------------ #
    #  Фоновая загрузка из трея («Вставить»): Best Quality, окно не нужно.
    # ------------------------------------------------------------------ #
    def start_tray_download(self, url):
        """Paste из трея: фоновая загрузка в Best Quality → строка в истории
        Spotlight. Подчиняется общему лимиту очереди (managed); без копии в буфер и
        без тоста (это привилегия Toast)."""
        self._bg_download(url, copy_on_done=False, announce=False, managed=True)

    def _bg_download(self, url, copy_on_done, announce=False, managed=True):
        """Общий путь фоновой загрузки (Paste/Toast): кладёт строку-прогресс в
        историю Spotlight (окно можно не открывать). copy_on_done — скопировать
        файл в буфер по завершении (для Toast, если включено в настройках)."""
        from core.i18n import tr
        from core import downloader, tools
        url = (url or "").strip()
        if not (url.startswith("http://") or url.startswith("https://")):
            if self.tray is not None:
                self.tray.notify(tr("Paste a video link first"))
            return
        # Плейлист/канал/ссылка с посторонним текстом — в фон не льём.
        if not downloader.is_downloadable_single(url):
            if self.tray is not None:
                self.tray.notify(tr("Link not supported."))
            return
        # Без бинарников фоновая загрузка невозможна — открываем окно (там докачка).
        if not (tools.have_ytdlp() and tools.have_ffmpeg()):
            self.show_near_tray()
            return
        self._ensure_spotlight().start_bg_download(
            url, copy_on_done=copy_on_done, announce=announce, managed=managed)

    # ------------------------------------------------------------------ #
    #  Мониторинг буфера обмена
    # ------------------------------------------------------------------ #
    def set_clipboard_watch(self, on):
        self.settings["clipboard_watch"] = bool(on)
        self.save_settings()
        self.apply_clipboard_watch()

    def set_toast_position(self, mode):
        self.settings["toast_position"] = mode
        self.save_settings()

    def set_toast_copy_file(self, on):
        """Копировать ли скачанный по Toast файл в буфер обмена."""
        self.settings["toast_copy_file"] = bool(on)
        self.save_settings()

    def set_autostart(self, on):
        """Автозапуск при старте Windows (ключ реестра HKCU\\...\\Run)."""
        from core import autostart
        on = bool(on)
        autostart.set_enabled(on)
        self.settings["autostart"] = on
        self.save_settings()

    def sync_autostart(self):
        """Настройка — источник истины: на старте приводим реестр в соответствие
        галочке. Выкл → запись удаляется (чинит «застрявший» автозапуск при
        выключенной галочке); вкл → перезаписываем на актуальный путь к exe."""
        from core import autostart
        autostart.set_enabled(self.settings.get("autostart", False))

    def set_parallel_downloads(self, n):
        self.settings["parallel_downloads"] = max(1, min(3, int(n)))
        self.save_settings()

    def reset_and_restart(self):
        """Удаляет ТОЛЬКО config.json и перезапускает Snatchr."""
        from core import updater, config
        try:
            if os.path.isfile(config.CONFIG_PATH):
                os.remove(config.CONFIG_PATH)
        except OSError:
            pass
        updater.relaunch_app()
        # Немедленно завершаемся, чтобы освободить мьютекс single-instance.
        os._exit(0)

    def _stamp_clip(self):
        import time
        self._clip_change_ts = time.monotonic()

    def _maybe_autopaste(self):
        """При открытии окна вставляет ссылку из буфера в одиночное поле, если:
        включена настройка, буфер обновлён недавно, ссылка одиночная и её сайт
        выбран в списке автовставки."""
        if not self.settings.get("autopaste", False):
            return
        import time
        if time.monotonic() - self._clip_change_ts > 30:   # только свежий буфер
            return
        from core import downloader
        try:
            text = (QApplication.clipboard().text() or "").strip()
        except Exception:
            return
        if not text or "\n" in text:
            return
        site = downloader.link_site(text)
        enabled = self.settings.get("autopaste_sites", downloader.AUTOPASTE_SITES)
        if site and site in enabled:
            self.main_page.autofill_url(text)

    def apply_clipboard_watch(self):
        """Подключает/отключает слежение за буфером по настройке."""
        clip = QApplication.clipboard()
        if self._clip_connected:
            try:
                clip.dataChanged.disconnect(self._on_clipboard_changed)
            except (TypeError, RuntimeError):
                pass
            self._clip_connected = False
        if self.settings.get("clipboard_watch", False):
            # Запоминаем текущее содержимое, чтобы не всплыть на «старой» ссылке.
            try:
                self._clip_last = (clip.text() or "").strip()
            except Exception:
                self._clip_last = ""
            clip.dataChanged.connect(self._on_clipboard_changed)
            self._clip_connected = True

    def _on_clipboard_changed(self):
        from core import downloader
        try:
            text = (QApplication.clipboard().text() or "").strip()
        except Exception:
            return
        if not text or text == self._clip_last:
            return
        self._clip_last = text
        if self._toast_active:
            return          # уже качается ролик по тосту — новый тост не показываем
        # Строгая проверка: одна ссылка на ОДНО видео, без лишнего текста рядом,
        # без плейлиста и без страницы канала/профиля (иначе тост не всплывает).
        if self.tray is not None and downloader.is_downloadable_single(text):
            from core.i18n import tr
            self.tray.toast_download(text, tr("Download this?"))

    def on_toast_clicked(self, url):
        """Клик по тосту «Скачать это?» — фоновая загрузка с приоритетом (managed=
        False, стартует сразу, минуя очередь) + копия файла в буфер по завершении
        (если включено) + тост «Downloaded» по готовности."""
        if url:
            self._bg_download(url, copy_on_done=self.settings.get("toast_copy_file", True),
                              announce=True, managed=False)

    def show_download_toast(self, dest):
        """Тост «Downloaded» с открытием папки по клику (после фоновой Toast-загрузки)."""
        from core.i18n import tr
        if self.tray is not None and dest:
            name = os.path.basename(dest)
            self.tray.show_toast(tr("Downloaded"), name,
                                 lambda d=dest: self._open_in_folder(d), position="corner")

    # --- живое зеркало идущих загрузок между окном и Spotlight ---------- #
    def _views(self):
        vs = [self.main_page]
        if self.spotlight is not None:
            vs.append(self.spotlight)
        return vs

    def _other_views(self, source):
        return [v for v in self._views() if getattr(v, "_dl_source", "") != source]

    def mirror_start(self, source, dl_id, entry):
        for v in self._other_views(source):
            v.add_mirror(dl_id, dict(entry))

    def mirror_progress(self, source, dl_id, frac):
        for v in self._other_views(source):
            v.update_mirror(dl_id, frac)

    def mirror_finish(self, source, dl_id, entry):
        for v in self._other_views(source):
            v.finish_mirror(dl_id, entry)

    def mirror_remove(self, source, dl_id):
        for v in self._other_views(source):
            v.remove_mirror(dl_id)

    def mirror_meta(self, source, dl_id, thumb_bytes, title, uploader):
        """Постер/название/автор, добытые для строки-загрузки, — в строку-зеркало."""
        for v in self._other_views(source):
            v.set_mirror_meta(dl_id, thumb_bytes, title, uploader)

    def request_cancel(self, dl_id):
        """Stop нажали на строке-зеркале — просим владельца загрузки отменить её."""
        for v in self._views():
            if v.cancel_own(dl_id):
                return

    def sync_view_mirrors(self, target):
        """При открытии окна/Spotlight — подтянуть уже идущие загрузки из другого
        места как строки-зеркала (чтобы они были видны сразу, а не после старта)."""
        for v in self._views():
            if v is target:
                continue
            for dl_id, entry, frac in v.active_downloads():
                target.add_mirror(dl_id, entry)
                target.update_mirror(dl_id, frac)

    def refresh_histories(self):
        """Немедленно пересобирает историю в окне и Spotlight из файла (после
        удаления/переименования ролика в одном из мест — отражается в обоих)."""
        from core import history
        items = history.load()
        try:
            self.main_page.history.rebuild(items)
        except Exception:
            pass
        if self.spotlight is not None:
            try:
                self.spotlight.history.rebuild(items)
            except Exception:
                pass

    def _prune_histories(self):
        """Тик (1с): если файлы истории удалили с диска — убираем ТОЛЬКО пропавшие
        строки (не пересобирая весь список) и стираем их из файла истории. Работает
        лишь когда что-то показано (иначе при показе история и так пересоберётся)."""
        win = self.main_page.isVisible()
        spot = self.spotlight is not None and self.spotlight.isVisible()
        if not win and not spot:
            return
        from core import history
        gone = set()
        if win:
            gone.update(self.main_page.history.drop_missing())
        if spot:
            gone.update(self.spotlight.history.drop_missing())
        for entry_id in gone:
            if entry_id:
                history.remove(entry_id)

    def report_active_downloads(self, source, n):
        """Источник (spotlight/window) сообщает число своих активных загрузок."""
        self._active_counts[source] = int(n)
        self._apply_ring()

    def _apply_ring(self):
        """Спиннер в трее, пока суммарно есть активные загрузки (даже одна). Если
        идёт детерминированное кольцо Toast или его завершающая анимация
        (галочка/крестик) — оно владеет иконкой, спиннер не вмешивается."""
        if (self.tray is None or self._toast_ring_id is not None
                or self._ring_cooldown):
            return
        total = sum(self._active_counts.values())
        if total > 0 and not self._ring_on:
            self.tray.animator.start(spin=True)
            self._ring_on = True
        elif total == 0 and self._ring_on:
            self._ring_on = False
            self.tray.animator.finish(True)

    # --- детерминированное кольцо Toast-загрузки (когда она одна) -------- #
    def toast_ring_begin(self, dl_id):
        """Старт Toast-загрузки: если сейчас нет других активных загрузок —
        занимаем иконку детерминированным кольцом (заполняется по прогрессу).
        Возвращает True, если кольцо занято под этот Toast."""
        if self.tray is None or sum(self._active_counts.values()) > 0:
            return False                 # уже есть загрузки -> общий спиннер
        self._toast_ring_id = dl_id
        self._ring_on = True
        self.tray.animator.start(spin=False)
        return True

    def toast_ring_progress(self, dl_id, frac):
        if dl_id == self._toast_ring_id and self.tray is not None:
            self.tray.animator.set_fraction(frac)

    def toast_ring_end(self, dl_id, success):
        if dl_id != self._toast_ring_id:
            return
        self._toast_ring_id = None
        self._ring_on = False
        self._ring_cooldown = True               # не даём спиннеру перебить галочку
        if self.tray is not None:
            self.tray.animator.finish(success)   # галочка/крестик -> назад к иконке
        QTimer.singleShot(1400, self._ring_cooldown_done)

    def _ring_cooldown_done(self):
        self._ring_cooldown = False
        self._apply_ring()                       # остались загрузки — вернуть спиннер

    def _copy_file_to_clipboard(self, path):
        """Кладёт скачанный файл в буфер как «файл» (Qt отдаёт CF_HDROP на Windows)
        — сразу вставляется (Ctrl+V) в мессенджер/проводник."""
        if not path or not os.path.isfile(path):
            return
        try:
            from PySide6.QtCore import QMimeData, QUrl
            mime = QMimeData()
            mime.setUrls([QUrl.fromLocalFile(os.path.abspath(path))])
            QApplication.clipboard().setMimeData(mime)
        except Exception:
            pass

    def open_logs_folder(self):
        """Открыть папку с логами (%APPDATA%/Snatchr/logs)."""
        from core.logbook import LOG_DIR
        try:
            os.makedirs(LOG_DIR, exist_ok=True)
            os.startfile(LOG_DIR)
        except Exception:
            pass

    def play_file(self, path):
        """Открыть видео системным плеером по умолчанию (двойной клик Проводника)."""
        try:
            if path and os.path.isfile(path):
                os.startfile(path)          # noqa: запуск ассоциированного приложения
        except Exception:
            pass

    def delete_file(self, entry):
        """Удаляет файл ролика с диска и запись из истории. Возвращает True/False.
        Если файл открыт в панели обрезки Spotlight — сначала жёстко освобождаем его
        (Qt держит демуксер открытым), затем удаляем с несколькими попытками, давая
        бэкенду доснять хендл (иначе тихий PermissionError и «удаление не сработало»)."""
        from core import history
        path = (entry or {}).get("path") or ""
        if path and self.spotlight is not None:
            try:
                self.spotlight.release_trim_file(path)
            except Exception:
                pass
        if not self._remove_file(path):
            return False
        history.remove((entry or {}).get("id", ""))
        return True

    def _remove_file(self, path):
        if not path:
            return False
        if not os.path.exists(path):
            return True                      # уже удалён — считаем успехом
        import time
        for _ in range(5):
            try:
                os.remove(path)
                return True
            except OSError:
                QApplication.processEvents()  # дать Qt/FFmpeg-бэкенду отпустить файл
                time.sleep(0.07)
        try:
            os.remove(path)
            return True
        except OSError:
            return False

    def _open_in_folder(self, path):
        """Клик по тосту об успехе — открыть файл в проводнике (или папку загрузок)."""
        import subprocess
        try:
            if path and os.path.isfile(path):
                subprocess.Popen('explorer /select,"%s"' % os.path.normpath(path))
                return
        except Exception:
            pass
        folder = self.settings.get("download_path")
        try:
            if folder and os.path.isdir(folder):
                os.startfile(folder)
        except Exception:
            pass

    def _show_overlay(self, title, worker, on_done=None):
        """Показывает модальный оверлей с заголовком и общей полосой прогресса,
        привязанный к worker (signals: progress[, status], done(ok, err))."""
        if self._updating:
            return
        self._updating = True
        self.suppress_autohide(True)

        from ui.widgets import UpdateOverlay

        pal = themes.palette(self.settings.get("theme", themes.DEFAULT_THEME))
        ov = UpdateOverlay(self, self, title,
                           fonts.font(self._s(13), "Semibold"), self.CORNER_R, pal)
        ov.setGeometry(0, 0, self.width(), self.height())
        ov.show()
        ov.raise_()
        ov.appear()
        self._update_overlay = ov

        worker.progress.connect(ov.set_progress)
        if hasattr(worker, "status"):
            worker.status.connect(ov.set_status)
        worker.done.connect(lambda ok, err: self._finish_overlay(ok, err, on_done))
        self._update_worker = worker
        worker.start()

    def _finish_overlay(self, ok, err, on_done=None):
        ov = self._update_overlay
        if ov is not None:
            ov.set_progress(1.0)

        def finish():
            if ov is not None:
                ov.hide()
                ov.deleteLater()
            self._update_overlay = None
            self._update_worker = None
            self._updating = False
            self.suppress_autohide(False)
            if on_done is not None:
                on_done(ok, err)

        # Дать полосе «доехать» до 100%, затем плавно убрать оверлей.
        QTimer.singleShot(280, lambda: ov.disappear(finish) if ov else finish())

    def toggle_window(self):
        if self._updating:
            return
        if self.usage_mode == "focus":
            # Auto-hide: иконка ОТКРЫВАЕТ скрытое окно. Если поймали во время
            # анимации появления — сворачиваем обратно (из текущей точки).
            # Полностью открытое окно иконкой не закрывается (только фокус/Esc).
            if not self._shown:
                self.show_near_tray()
            elif self._appearing:
                self.hide_window()
            return
        # Pinned: обычный тогл открыть/закрыть.
        if self._shown:
            self.hide_window()
        else:
            self.show_near_tray()

    def _stop_win_anims(self):
        for a in (self._win_pos_anim, self._win_op_anim):
            if a is not None:
                a.stop()

    def _run_win_anim(self, p_from, p_to, o_from, o_to, on_finished=None):
        self._stop_win_anims()
        showing = o_to >= 1.0
        self._appearing = showing   # перебить кликом можно, пока окно появляется

        pos_anim = QPropertyAnimation(self, b"pos", self)
        pos_anim.setDuration(220)
        pos_anim.setStartValue(p_from)
        pos_anim.setEndValue(p_to)
        pos_anim.setEasingCurve(QEasingCurve.OutCubic)

        op_anim = QPropertyAnimation(self, b"windowOpacity", self)
        op_anim.setDuration(200)
        op_anim.setStartValue(float(o_from))
        op_anim.setEndValue(float(o_to))
        op_anim.setEasingCurve(QEasingCurve.OutCubic)

        def _fin():
            if showing:
                self._appearing = False
            if on_finished is not None:
                on_finished()

        op_anim.finished.connect(_fin)

        self._win_pos_anim = pos_anim
        self._win_op_anim = op_anim
        pos_anim.start()
        op_anim.start()

    def _slide_offset(self):
        # Снизу панель -> окно выезжает снизу вверх (старт ниже), иначе сверху вниз.
        rise = self._s(16)
        return rise if self._tray_edge == "bottom" else -rise

    def show_near_tray(self):
        self._shown = True
        fresh = not self.isVisible()

        if fresh:
            # Всегда открываемся на стартовой (главной) странице.
            self._reset_to_main()
            fx, fy = self._compute_tray_pos()    # выставляет self._tray_edge
            self._final_pos = QPoint(fx, fy)
            off = self._slide_offset()
            self.setWindowOpacity(0.0)
            self.move(fx, fy + off)
            self.show()
            self.repaint()        # отрисовать главное состояние, пока прозрачно
            self._maybe_autopaste()   # вставить свежую ссылку из буфера, если включено
            start_pos = QPoint(fx, fy + off)
            start_op = 0.0
        else:
            # Перебиваем идущее скрытие — продолжаем с текущей точки к финалу.
            fx, fy = self._compute_tray_pos()
            self._final_pos = QPoint(fx, fy)
            start_pos = self.pos()
            start_op = self.windowOpacity()

        self._became_active = False
        self._run_win_anim(start_pos, QPoint(fx, fy), start_op, 1.0)
        self.raise_()
        # Фокус забираем только в режиме Auto-hide (нужен для Esc/клика-вне).
        # В Pinned это лишний перехват фокуса -> иногда моргает панель задач.
        if self.usage_mode == "focus":
            self.activateWindow()
            self.setFocus()
            self._force_foreground()      # реально выводим окно на передний план
            self._became_active = True    # авто-скрытие вооружено сразу

        # Первое открытие окна -> через 0.5с проверяем/докачиваем бинарники.
        if not self._first_run_checked:
            self._first_run_checked = True
            QTimer.singleShot(500, self._maybe_first_run_setup)

    def _force_foreground(self):
        """Надёжно делает окно активным (SetForegroundWindow с обходом блокировки)."""
        try:
            hwnd = int(self.winId())
            fg = win32gui.GetForegroundWindow()
            if fg == hwnd:
                return
            cur = win32api.GetCurrentThreadId()
            other = win32process.GetWindowThreadProcessId(fg)[0] if fg else 0
            if other and other != cur:
                win32process.AttachThreadInput(other, cur, True)
                try:
                    win32gui.SetForegroundWindow(hwnd)
                finally:
                    win32process.AttachThreadInput(other, cur, False)
            else:
                win32gui.SetForegroundWindow(hwnd)
        except Exception:
            pass

    def hide_window(self):
        if self._updating:
            return
        if not self.isVisible():
            self._shown = False
            return
        self._shown = False
        cur = self.pos()              # текущая позиция (учитывает перетаскивание)
        off = self._slide_offset()

        def fin():
            # Финализируем только если за время анимации не запросили показ.
            if not self._shown:
                self.hide()
                self.setWindowOpacity(1.0)
                self.main_page.on_window_hidden()   # очистить поле ввода ссылки
                self._reset_to_main()

        self._run_win_anim(cur, QPoint(cur.x(), cur.y() + off),
                           self.windowOpacity(), 0.0, on_finished=fin)

    def _reset_to_main(self):
        """Сбросить навигацию на главную страницу (главная высота)."""
        if self.current_page != "main":
            self.current_page = "main"
            self.bottom_bar.set_page_mode("main")
        self.settings_page.setGraphicsEffect(None)
        self.about_page.setGraphicsEffect(None)
        self.main_page.setGraphicsEffect(None)
        self.settings_page.hide()
        self.about_page.hide()
        self.main_page.show()
        self.main_page.on_window_shown()      # обновить историю окна (убрать удалённые)
        self.main_page.raise_()
        self.bottom_bar.btn_settings.raise_()
        self.bottom_bar.btn_folder.raise_()
        self.bottom_bar.btn_exit.raise_()
        # Учитываем расширение главной (режим Multiple Links).
        extra = self.main_page.expand_extra()
        target = self.WIN_H_FULL + extra
        new_ch = target - self.BAR_H - self.BORDER_W
        self.main_page._extra = extra
        self.main_page.setGeometry(self.content_x, self.content_y, self.content_w, new_ch)
        self.main_page.relayout(new_ch)
        if self.WIN_H != target:
            self.set_window_height(target)

    # ------------------------------------------------------------------ #
    #  Перетаскивание окна (если включено) за верхнюю пустую область.
    #  Старт инициирует тот виджет, который получил клик: само окно (на главной
    #  странице) или активная страница (см. WindowDragMixin) — оба зовут хелперы.
    # ------------------------------------------------------------------ #
    def window_drag_press(self, global_pos):
        self._drag_offset = global_pos - self.frameGeometry().topLeft()

    def window_drag_move(self, global_pos):
        if self._drag_offset is None:
            return
        target = global_pos - self._drag_offset
        avail = QGuiApplication.primaryScreen().availableGeometry()
        w, h = self.width(), self.height()
        x = max(avail.left(), min(target.x(), avail.right() - w + 1))
        y = max(avail.top(), min(target.y(), avail.bottom() - h + 1))
        self.move(x, y)

    def window_drag_release(self):
        self._drag_offset = None

    def mousePressEvent(self, event):
        # Клики доходят сюда только для областей окна, не закрытых страницей
        # (главная страница, рамка). Тащим за пустое место в верхней зоне.
        if (event.button() == Qt.LeftButton and self.allow_dragging
                and event.position().toPoint().y() < self.DRAG_ZONE_H
                and self.childAt(event.position().toPoint()) is None):
            self.window_drag_press(event.globalPosition().toPoint())
        else:
            self._drag_offset = None
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._drag_offset is not None and (event.buttons() & Qt.LeftButton):
            self.window_drag_move(event.globalPosition().toPoint())
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._drag_offset = None
        super().mouseReleaseEvent(event)

    def _compute_tray_pos(self):
        w, h = self.WIN_W, self.WIN_H
        screen = QGuiApplication.primaryScreen().geometry()
        sw, sh = screen.width(), screen.height()

        taskbar = win32gui.FindWindow("Shell_TrayWnd", None)
        taskbar_rect = win32gui.GetWindowRect(taskbar)

        tb_left   = taskbar_rect[0]
        tb_top    = taskbar_rect[1]
        tb_right  = taskbar_rect[2]
        tb_bottom = taskbar_rect[3]
        tb_width  = tb_right - tb_left
        tb_height = tb_bottom - tb_top

        tray_hwnd = win32gui.FindWindowEx(taskbar, 0, "TrayNotifyWnd", None)
        toolbar   = win32gui.FindWindowEx(tray_hwnd, 0, "ToolbarWindow32", None)

        if toolbar:
            tray_rect = win32gui.GetWindowRect(toolbar)
        else:
            tray_rect = win32gui.GetWindowRect(tray_hwnd)

        tray_cx = (tray_rect[0] + tray_rect[2]) // 2
        tray_cy = (tray_rect[1] + tray_rect[3]) // 2

        margin = self._s(12)

        if tb_height < tb_width:
            position = "top" if tb_top < sh // 2 else "bottom"
        else:
            position = "left" if tb_left < sw // 2 else "right"

        self._tray_edge = position   # для направления анимаций/роста высоты

        if position == "bottom":
            x = tray_cx - w // 2
            y = tb_top - h - margin
        elif position == "top":
            x = tray_cx - w // 2
            y = tb_bottom + margin
        elif position == "left":
            x = tb_right + margin
            y = tray_cy - h // 2
        else:  # right
            x = tb_left - w - margin
            y = tray_cy - h // 2

        x = max(margin, min(x, sw - w - margin))
        y = max(margin, min(y, sh - h - margin))

        return x, y
