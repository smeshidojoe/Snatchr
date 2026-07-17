from PySide6.QtCore import Qt, QTimer, QEasingCurve
from PySide6.QtGui import QPalette, QColor, QPixmap, QImage
from PySide6.QtWidgets import QWidget, QLineEdit, QTextEdit, QLabel

import os
import uuid

from core import fonts, downloader, tools, cache, themes, history
from core.i18n import tr
from core.icons import themed_icon, themed_pixmap
from core.workers import (
    ProbeWorker, ThumbWorker, SetupWorker,
    MultiProbeWorker, PlaylistProbeWorker,
)
from ui.widgets import (
    IconButton, LinkButton, CheckBox, SegmentedControl, Selector, WindowDragMixin,
    DownloadButton, Spinner, ScrollList, InfoCardRow,
    PlaylistHeader, TimeCodeEdit, rounded_pixmap,
)
from ui.spotlight_history import HistoryList
from ui.download_scheduler import DownloadScheduler
from ui import anim


class MainPage(WindowDragMixin, QWidget):
    """Главный экран: ввод ссылки(ок), анализ, выбор формата, Download."""

    def _load_theme(self):
        """Загружает цвета текущей темы в атрибуты экземпляра."""
        p = themes.palette(self.settings.get("theme", themes.DEFAULT_THEME))
        self._pal = p
        self.FIELD_BG    = p["field_bg"]
        self.TITLE_COLOR = p["title"]
        self.TEXT_COLOR  = p["text"]
        self.MUTED_COLOR = p["muted"]
        self.CB_OFF      = p["cb_off"]
        self.CB_ON       = p["cb_on"]
        self.SEG_BG      = p["seg_bg"]
        self.SEG_SEL     = p["seg_sel"]
        self.SEL_CHIP    = p["sel_chip"]
        self.SEL_CHEVRON = p["sel_chevron"]
        self.DL_BG       = p["download_bg"]
        self.DL_BG_HOVER = p["download_bg_hover"]
        self.ANALYZE_BG  = p["analyze_bg"]
        self.ANALYZE_HOV = p["analyze_bg_hover"]
        self.STOP_BG     = p["stop_bg"]
        self.PROG_TRACK  = p["prog_track"]
        self.OK_COLOR    = p["ok"]
        self.ERR_COLOR   = p["error"]
        self.ON_ACCENT   = p["on_accent"]

    def __init__(self, parent, app, settings, width, height):
        super().__init__(parent)
        self.app = app
        self.settings = settings
        self.width_ = width
        self.height_ = height
        self.init_window_drag(app)
        self.resize(width, height)
        self._load_theme()

        self._info = None
        self._opt_by_label = {}
        self._selected = self._default_option("video")
        # Если бинарники уже на месте (не первый запуск) — готовы сразу.
        self._tools_ready = tools.have_ytdlp() and tools.have_ffmpeg()
        self._pending_url = None
        self._state = "idle"
        self._extra = 0                # доп. высота в режиме Multiple Links
        self._multi_analyzed = False
        self._multi_jobs = []          # [(url, option)] для пакетной загрузки
        self._probe = self._thumb = self._dl = self._setup = None
        self._mprobe = self._mdl = None
        self._active_worker = None     # активный probe/playlist worker (анти-гонка)
        self._analyzing_url = ""
        # История окна + параллельный планировщик (общий с логикой Spotlight).
        self._sched = DownloadScheduler(app, self)
        self._sched.progress.connect(self._on_row_progress)
        self._sched.finished.connect(self._on_row_done)
        self._sched.failed.connect(self._on_row_failed)
        self._dl_source = "window"     # тег источника для зеркала загрузок
        self._dls = {}                 # dl_id -> {row,frac,convert,url,title,thumb_url}
        self._mirrors = {}             # dl_id -> row (зеркала загрузок из Spotlight)
        self._pending_row = None       # проанализированная строка (ждёт Download)
        self._pending_entry = None
        self._fetching_row = None      # строка идущего анализа ссылки
        self._collapsing = False       # идёт плавное сворачивание режима мультиссылок

        self._analyze_timer = QTimer(self)
        self._analyze_timer.setSingleShot(True)
        self._analyze_timer.setInterval(600)
        self._analyze_timer.timeout.connect(self._start_analyze)
        self._status_timer = QTimer(self)
        self._status_timer.setSingleShot(True)
        self._status_timer.timeout.connect(self._hide_status)

        self._build()
        self._layout()
        self._set_state("idle")
        self.history.rebuild(history.load())      # показать накопленную историю

    @staticmethod
    def _default_option(mode):
        if mode == "audio":
            return {"label": tr("Best Quality"), "fmt": "ba/b", "mp3": True}
        return {"label": tr("Best Quality"), "fmt": downloader.BEST_VIDEO_FMT, "mp3": False}

    @staticmethod
    def _multi_options(mode):
        """Варианты формата для режима Multiple Links (одни для всех ссылок)."""
        if mode == "audio":
            return [{"label": tr("Best Quality"), "fmt": "ba/b", "mp3": True}]
        return [
            {"label": tr("Best Quality"), "fmt": downloader.BEST_VIDEO_FMT, "mp3": False},
            {"label": tr("Best Compatibility (1080p)"),
             "fmt": downloader.AVC_VIDEO_FMT, "mp3": False},
            {"label": tr("Thumbnail"), "thumbnail": True, "mp3": False},
        ]

    def _populate_multi_selector(self, mode=None):
        mode = mode or self.seg_type.value()
        opts = self._multi_options(mode)
        self.sel_format.clear()
        self._opt_by_label = {}
        for o in opts:
            self.sel_format.add_item(o["label"])
            self._opt_by_label[o["label"]] = o
        self.sel_format.set_current(opts[0]["label"])
        self._selected = opts[0]

    def _rebuild_multi_jobs(self):
        self._multi_jobs = [(j[0], self._selected, j[2] if len(j) > 2 else None)
                            for j in self._multi_jobs]

    def _is_multi(self):
        return self.cb_multi.isChecked()

    def is_busy(self):
        """Идёт ли загрузка/конвертация (тогда не пересобираем UI на лету)."""
        return self._state == "downloading"

    def on_window_hidden(self):
        """Окно свернули: очищаем поле(я) ввода, если не идёт загрузка."""
        if self._state == "downloading":
            return
        self.url_text.blockSignals(True)
        self.url_text.clear()
        self.url_text.blockSignals(False)
        self.tc_start.clear_code()
        self.tc_end.clear_code()
        self.url_edit.clear()      # textChanged -> сброс состояния в idle

    def expand_extra(self):
        return self.app._s(50) if self._is_multi() else 0

    # ------------------------------------------------------------------ #
    def _build(self):
        s = self.app._s
        theme = self.settings.get("theme", "Deep Ocean")

        # Одиночное поле + многострочное поле (Multiple Links).
        self.url_edit = QLineEdit(self)
        self.url_edit.setFont(fonts.font(s(13), "Regular"))
        self.url_edit.setPlaceholderText(tr("Paste video link here..."))
        self.url_edit.setStyleSheet(
            f"QLineEdit {{ background-color: {self.FIELD_BG}; border: none; "
            f"border-radius: {s(8)}px; color: {self.TITLE_COLOR}; "
            f"padding-left: {s(10)}px; padding-right: {s(30)}px; }}")
        pal = self.url_edit.palette()
        pal.setColor(QPalette.PlaceholderText, QColor(self.MUTED_COLOR))
        self.url_edit.setPalette(pal)
        self.url_edit.textChanged.connect(self._on_text_changed)

        self.url_text = QTextEdit(self)
        self.url_text.setFont(fonts.font(s(13), "Regular"))
        self.url_text.setPlaceholderText(tr("Paste video links (one per line)…"))
        self.url_text.setStyleSheet(
            f"QTextEdit {{ background-color: {self.FIELD_BG}; border: none; "
            f"border-radius: {s(8)}px; color: {self.TITLE_COLOR}; "
            f"padding: {s(6)}px {s(8)}px; }}")
        self.url_text.textChanged.connect(self._on_multi_text_changed)
        self.url_text.hide()

        ic_cancel = themed_icon(theme, "cancel.png", self._pal["icon"], s(16))
        ic_cancel_h = themed_icon(theme, "cancel.png", self._pal["icon_hover"], s(16))
        self.btn_cancel = IconButton(self, ic_cancel, ic_cancel_h, s(16), self._clear_url)
        self.btn_cancel.hide()

        self.cb_multi = CheckBox(self, tr("Multiple Links"), fonts.font(s(12), "Regular"),
                                 self.TEXT_COLOR, self.CB_OFF, self.CB_ON, s(17), s(5))
        self.cb_multi.toggled.connect(self._on_multi_toggle)

        # Таймкоды (только одиночные ссылки): метка From/To + жёсткий блок 00:00:00.
        # Метки всегда «жирные» и основного цвета текста — неактивность касается
        # только полей ввода, но не подписей.
        tc_lbl_font = fonts.font(s(12), "Semibold")
        tc_lbl_css = f"color: {self.TITLE_COLOR}; background: transparent;"
        self.tc_from_lbl = QLabel(tr("From"), self)
        self.tc_from_lbl.setFont(tc_lbl_font)
        self.tc_from_lbl.setStyleSheet(tc_lbl_css)
        self.tc_from_lbl.setAlignment(Qt.AlignVCenter | Qt.AlignRight)
        self.tc_to_lbl = QLabel(tr("To"), self)
        self.tc_to_lbl.setFont(tc_lbl_font)
        self.tc_to_lbl.setStyleSheet(tc_lbl_css)
        self.tc_to_lbl.setAlignment(Qt.AlignVCenter | Qt.AlignRight)
        tc_font = fonts.font(s(12), "Medium")   # SF Pro (а не моно) — как в остальном UI
        self.tc_start = TimeCodeEdit(self, tc_font, self.FIELD_BG, self.TITLE_COLOR, s(7),
                                     self._pal["disabled_bg"], self._pal["disabled_text"])
        self.tc_end = TimeCodeEdit(self, tc_font, self.FIELD_BG, self.TITLE_COLOR, s(7),
                                   self._pal["disabled_bg"], self._pal["disabled_text"])
        self.tc_start.setEnabled(False)
        self.tc_end.setEnabled(False)

        self.seg_type = SegmentedControl(
            self, [(tr("Video"), "video"), (tr("Audio"), "audio")], "video",
            fonts.font(s(11), "Medium"),
            self.SEG_BG, self.SEG_SEL, self.MUTED_COLOR, self.ON_ACCENT, s(9))
        self.seg_type.changed.connect(self._on_mode_change)

        self.sel_format = Selector(self, fonts.font(s(11), "Regular"),
                                   self.FIELD_BG, self.SEL_CHIP, self.TEXT_COLOR,
                                   self.SEL_CHEVRON, s(7), s(22),
                                   accent=self.SEG_SEL, border=self._pal["border"],
                                   on_accent=self.ON_ACCENT)
        self.sel_format.add_item(tr("Best Quality"))
        self.sel_format.set_current(tr("Best Quality"))
        self.sel_format.changed.connect(self._on_format_change)

        # Карточка одиночного видео.
        self._thumb_w, self._thumb_h, self._thumb_r = s(116), s(66), s(8)
        self.thumb = QLabel(self)
        self.thumb.setStyleSheet("background: transparent;")
        self.lbl_title = QLabel(self)
        self.lbl_title.setFont(fonts.font(s(12), "Medium"))
        self.lbl_title.setStyleSheet(f"color: {self.TITLE_COLOR}; background: transparent;")
        self.lbl_title.setWordWrap(True)
        self.lbl_uploader = QLabel(self)
        self.lbl_uploader.setFont(fonts.font(s(10), "Regular"))
        self.lbl_uploader.setStyleSheet(f"color: {self.TEXT_COLOR}; background: transparent;")
        self.lbl_duration = QLabel(self)
        self.lbl_duration.setFont(fonts.mono(s(10)))
        self.lbl_duration.setStyleSheet(f"color: {self.MUTED_COLOR}; background: transparent;")

        self.spinner = Spinner(self, themed_pixmap(theme, "fetching.png",
                                                   self.MUTED_COLOR, s(16)), s(16))
        self.spinner.hide()
        self.lbl_msg = QLabel(self)
        self.lbl_msg.setFont(fonts.font(s(11), "Regular"))
        self.lbl_msg.setStyleSheet(f"color: {self.MUTED_COLOR}; background: transparent;")
        self.lbl_msg.setWordWrap(True)

        # Кнопка «свои cookies» — появляется, когда даже куки браузера не помогли.
        self.btn_cookies = LinkButton(self, tr("Use cookies file…"),
                                      fonts.font(s(10), "Semibold"),
                                      self._pal["link"], self._pal["link_hover"],
                                      self._on_pick_cookies)
        self.btn_cookies.hide()

        # Скроллируемый список (Multiple Links / плейлист).
        self.list = ScrollList(self, self.PROG_TRACK, self.MUTED_COLOR)
        self.list.hide()

        # История окна (общая с Spotlight по содержимому). Одиночная ссылка после
        # анализа выезжает сюда pending-строкой; Download превращает её в загрузку.
        self.history = HistoryList(self.app, self, allow_trim=False, draw_bg=False)
        self.history.stopClicked.connect(self._cancel_row)
        self.history.copyClicked.connect(self._copy_row)
        self.history.moreClicked.connect(self._more_row)

        # Статус + Download + прогресс.
        self.status_box = QWidget(self)
        self.status_icon = QLabel(self.status_box)
        self.status_icon.setAlignment(Qt.AlignCenter)
        self.status_text = QLabel(self.status_box)
        self.status_text.setFont(fonts.font(s(10), "Regular"))
        self._ok_pm = themed_pixmap(theme, "green-check.png", self.OK_COLOR, s(13))
        self._err_pm = themed_pixmap(theme, "red-cancel.png", self.ERR_COLOR, s(13))
        self.status_box.hide()

        self.btn_download = DownloadButton(self, tr("Download"), fonts.font(s(13), "Semibold"),
                                           self.DL_BG, self.DL_BG_HOVER, s(8),
                                           fg=self.ON_ACCENT,
                                           disabled_bg=self._pal["disabled_bg"],
                                           disabled_text=self._pal["disabled_text"])
        self.btn_download.clicked.connect(self._on_download_click)

    # ------------------------------------------------------------------ #
    def _layout(self):
        s = self.app._s
        pad = s(12)
        w = self.width_
        extra = self._extra
        fy, fh = s(12), s(34)

        # Доп. высоту окна (+50) в режиме мультиссылок отдаём БОЛЬШОМУ полю ввода
        # (растёт под много ссылок). В плейлисте поле одиночное и фиксированное —
        # там +50 должны достаться СПИСКУ (низ якорится к Download и растёт с окном),
        # иначе верхний блок просто съезжает вниз, а список не увеличивается.
        multi_input = self._is_multi() or self._collapsing
        input_extra = extra if multi_input else 0
        if multi_input:
            self.url_text.setGeometry(pad, fy, w - 2 * pad, fh + input_extra)
            self.url_text.show()
            self.url_edit.hide()
            self.btn_cancel.hide()
        else:
            self.url_edit.setGeometry(pad, fy, w - 2 * pad, fh)
            self.url_edit.show()
            self.url_text.hide()
            cw = s(22)
            self.btn_cancel.setGeometry(pad + (w - 2 * pad) - cw - s(6),
                                        fy + (fh - cw) // 2, cw, cw)
            self.btn_cancel.setVisible(bool(self.url_edit.text()))

        input_bottom = fy + fh + input_extra
        ml_y = input_bottom + s(8)
        # [From] [00:00:00]  [To] [00:00:00] — справа; чекбокс Multiple Links на
        # остатке слева. Всё в одном ряду — при разворачивании Multiple Links ряд
        # (метки и поля) едет вниз вместе с ним.
        from PySide6.QtGui import QFontMetrics
        lfm = QFontMetrics(self.tc_from_lbl.font())
        from_lw = lfm.horizontalAdvance(self.tc_from_lbl.text())
        to_lw = lfm.horizontalAdvance(self.tc_to_lbl.text())
        fw, lgap, gap, th = s(92), s(6), s(12), s(26)
        ty = ml_y
        te_x = w - pad - fw
        self.tc_end.setGeometry(te_x, ty, fw, th)
        to_x = te_x - lgap - to_lw
        self.tc_to_lbl.setGeometry(to_x, ty, to_lw, th)
        fe_x = to_x - gap - fw
        self.tc_start.setGeometry(fe_x, ty, fw, th)
        from_x = fe_x - lgap - from_lw
        self.tc_from_lbl.setGeometry(from_x, ty, from_lw, th)
        self.cb_multi.setGeometry(pad, ml_y, from_x - s(8) - pad, s(26))

        row_y = ml_y + s(26) + s(10)
        row_h = s(30)
        seg_w = s(120)
        self.seg_type.setGeometry(pad, row_y, seg_w, row_h + s(4))
        sel_x = pad + seg_w + s(8)
        self.sel_format.setGeometry(sel_x, row_y + s(2), w - pad - sel_x, row_h)

        info_y = row_y + row_h + s(14)
        self._info_y = info_y
        self.thumb.setGeometry(pad, info_y, self._thumb_w, self._thumb_h)
        meta_x = pad + self._thumb_w + s(10)
        self.lbl_title.setGeometry(meta_x, info_y, w - pad - meta_x, s(34))
        self.lbl_uploader.setGeometry(meta_x, info_y + s(36), w - pad - meta_x, s(16))
        self.lbl_duration.setGeometry(meta_x, info_y + s(52), w - pad - meta_x, s(14))
        self.spinner.move(pad, info_y + s(2))
        self.lbl_msg.setGeometry(pad + s(22), info_y, w - 2 * pad - s(22), s(20))

        self._dl_h = s(40)
        self._dl_y = self.height_ - self._dl_h - s(8)
        self._dl_full_w = w - 2 * pad
        self._dl_pad = pad
        self._stop_w = s(110)
        self._status_y = self._dl_y - s(22)

        # История/список тянутся почти до кнопки Download — зону статус-строки
        # (она нужна лишь изредка для мульти/ошибок) историей перекрываем.
        base_rect = (pad, info_y, w - 2 * pad, self._dl_y - s(10) - info_y)
        self.history.setGeometry(*base_rect)
        # Плейлист: закреплённая шапка над списком; сам список ниже на её высоту.
        ph = getattr(self, "pl_header", None)
        if self._state == "playlist_ready" and ph is not None:
            hh = s(30)
            ph.setGeometry(pad, info_y, w - 2 * pad, hh)
            ph.show()
            ph.raise_()
            self.list.setGeometry(pad, info_y + hh + s(6), w - 2 * pad,
                                  base_rect[3] - hh - s(6))
        else:
            if ph is not None:
                ph.hide()
            self.list.setGeometry(*base_rect)
        # Спиннер/надпись «Fetching…» держим поверх истории (иначе она их закрывает).
        self.spinner.raise_()
        self.lbl_msg.raise_()

        # «Use cookies file…» — в правом нижнем углу инфо-блока (над статусом).
        cw = s(130)
        self.btn_cookies.setGeometry(w - pad - cw, self._status_y - s(26), cw, s(22))

        self.status_box.setGeometry(pad, self._status_y, w - 2 * pad, s(18))
        self.status_icon.setGeometry(0, 0, s(15), s(18))
        self.status_text.setGeometry(s(19), 0, w - 2 * pad - s(19), s(18))

        if self.btn_download.width() <= 0 or self._state != "downloading":
            self.btn_download.setGeometry(pad, self._dl_y, self._dl_full_w, self._dl_h)

    def relayout(self, new_h):
        self.height_ = new_h
        self._layout()

    # ------------------------------------------------------------------ #
    #  Provisioning
    # ------------------------------------------------------------------ #
    def on_tools_ready(self, ok, err):
        self._tools_ready = ok
        self.btn_download.set_text(tr("Analyze") if self._is_multi() else tr("Download"))
        if not ok:
            self._show_status(f"{tr('Setup failed')}: {err}", self.ERR_COLOR, self._err_pm)
        if ok and self._pending_url:
            self._analyze_timer.start()
        self._refresh_download_enabled()

    # ------------------------------------------------------------------ #
    #  Single-link ввод
    # ------------------------------------------------------------------ #
    def _on_text_changed(self, text):
        if self._is_multi():
            return
        self.btn_cancel.setVisible(bool(text))
        url = text.strip()
        self._pending_url = url or None
        if not url:
            self._reset_info()
            self._analyze_timer.stop()
            self._set_state("idle")
            return
        # Текст изменился относительно проанализированной ссылки — прежняя карточка
        # устарела: убираем её и блокируем Download до новой проверки.
        if url != self._analyzing_url and self._state in ("ready", "error"):
            self._reset_info()
            self._set_state("idle")
        self._analyze_timer.start()

    def _clear_url(self):
        self.url_edit.clear()
        self.url_edit.setFocus()

    def _start_analyze(self):
        if self._is_multi():
            return
        url = (self.url_edit.text() or "").strip()
        if not (url.startswith("http://") or url.startswith("https://")):
            # Невалидный ввод (например, ссылка без http) — сбрасываем устаревшее
            # состояние «ready», чтобы Download не остался активным.
            self._reset_info()
            self._set_state("idle")
            return
        # Ссылка на канал/профиль (yt-dlp иначе перечисляет ВСЕ видео канала) —
        # не анализируем, показываем понятную ошибку.
        if downloader.is_channel_url(url):
            self._reset_info()
            self.lbl_msg.setText(tr("Channel links aren't supported — paste a video link."))
            self._set_state("error")
            return
        if not (tools.have_ytdlp() and tools.have_ffmpeg()):
            self._ensure_tools(then_analyze=True)
            return
        self.list.clear()
        self._analyzing_url = url
        # Кэш: если ссылку уже анализировали — без повторного запроса.
        cached = cache.get(url)
        if cached is not None and not downloader.is_playlist_url(url):
            self._active_worker = None
            self._apply_info(cached)
            return
        # Плейлист -> список с выбором; иначе одиночный разбор форматов.
        if downloader.is_playlist_url(url):
            self._set_state("playlist_fetching")
            self.lbl_msg.setText(tr("Fetching playlist…"))
            self._pl = PlaylistProbeWorker(url, self.settings, self)
            self._active_worker = self._pl
            self._pl.done.connect(self._on_playlist_done)
            self._pl.error.connect(self._on_probe_error)
            self._pl.start()
            return
        # Строка «Fetching…» появляется прямо в истории (блок с спиннером); после
        # анализа она превратится в pending (обложка + инфо).
        self._remove_pending()
        self._fetching_row = self.history.insert_fetching(
            {"id": uuid.uuid4().hex[:12], "url": url, "host": history.host_label(url),
             "title": "", "path": None, "thumb": "", "ts": 0})
        self._set_state("fetching")
        # Старые воркеры не убиваем (terminate падает) — игнорируем их результат.
        self._probe = ProbeWorker(url, self.settings, self)
        self._active_worker = self._probe
        self._probe.done.connect(self._on_probe_done)
        self._probe.error.connect(self._on_probe_error)
        self._probe.start()

    # --- Плейлист ------------------------------------------------------- #
    def _on_playlist_done(self, info):
        if self.sender() is not self._active_worker:
            return
        s = self.app._s
        entries = downloader.playlist_entries(info)
        if not entries:
            # Не плейлист (или пусто) — пробуем как одиночное видео.
            self._set_state("fetching")
            self.lbl_msg.setText(tr("Fetching info…"))
            url = (self.url_edit.text() or "").strip()
            self._probe = ProbeWorker(url, self.settings, self)
            self._active_worker = self._probe
            self._probe.done.connect(self._on_probe_done)
            self._probe.error.connect(self._on_probe_error)
            self._probe.start()
            return

        self._pl_entries = entries
        self.list.clear()
        # Шапка ЗАКРЕПЛЕНА над списком (дочерний виджет страницы, не строка скролла)
        # — тайтл/счётчик/Select-All всегда видны при прокрутке. Позиция — в _layout.
        self._clear_pl_header()
        self.pl_header = PlaylistHeader(
            self, info.get("title") or tr("Playlist"), len(entries),
            fonts.font(s(11), "Semibold"), fonts.font(s(10), "Medium"),
            fonts.font(s(10), "Regular"),
            self.TITLE_COLOR, self.TEXT_COLOR, self.TITLE_COLOR, self.MUTED_COLOR, s(30))
        self.pl_header.toggled.connect(self._on_pl_toggle)
        self.pl_header.show()

        self._pl_rows = []
        self._pl_thumb_workers = []
        placeholder = QPixmap(s(80), s(45))
        placeholder.fill(QColor(self.FIELD_BG))
        placeholder = rounded_pixmap(placeholder, s(80), s(45), s(6))
        for e in entries:
            row = InfoCardRow(
                self.list, e["title"], e.get("uploader") or "",
                self._fmt_duration(e["duration"]),
                fonts.font(s(11), "Medium"), fonts.font(s(10), "Regular"),
                fonts.mono(s(10)), self.TITLE_COLOR, self.TEXT_COLOR, self.MUTED_COLOR,
                s(80), s(45), s(6), s(52),
                with_check=True, cb_colors=(self.CB_OFF, self.CB_ON))
            row.set_thumb(placeholder)
            row.cb.toggled.connect(self._on_pl_check_changed)
            self.list.add_row(row)
            self._pl_rows.append(row)
            turl = e.get("thumbnail")
            if turl:
                tw = ThumbWorker(turl, self)
                tw.done.connect(lambda data, r=row: self._set_row_thumb(r, data))
                tw.start()
                self._pl_thumb_workers.append(tw)

        # Плейлист: Best Quality (+ Thumbnail в режиме Video).
        opts = [self._default_option(self.seg_type.value())]
        if self.seg_type.value() == "video":
            opts.append({"label": tr("Thumbnail"), "thumbnail": True, "mp3": False})
        self.sel_format.clear()
        self._opt_by_label = {}
        for o in opts:
            self.sel_format.add_item(o["label"])
            self._opt_by_label[o["label"]] = o
        self.sel_format.set_current(opts[0]["label"])
        self._selected = opts[0]
        self._set_state("playlist_ready")
        self._layout()                   # разместить закреплённую шапку сразу

    def _clear_pl_header(self):
        ph = getattr(self, "pl_header", None)
        if ph is not None:
            ph.setParent(None)
            ph.deleteLater()
        self.pl_header = None

    def _on_pl_toggle(self):
        """Кнопка Deselect All / Select All: инвертирует выбор всех клипов."""
        rows = getattr(self, "_pl_rows", [])
        target = not all(r.is_checked() for r in rows) if rows else True
        for r in rows:
            r.cb.blockSignals(True)          # счётчик обновим разом, не построчно
            r.set_checked(target, animate=True)
            r.cb.blockSignals(False)
        self._update_pl_count()
        self._refresh_download_enabled()

    def _on_pl_check_changed(self):
        self._update_pl_count()
        self._refresh_download_enabled()

    def _update_pl_count(self):
        rows = getattr(self, "_pl_rows", [])
        if getattr(self, "pl_header", None) is not None:
            sel = sum(1 for r in rows if r.is_checked())
            self.pl_header.set_state(sel, len(rows))

    def _start_playlist_download(self):
        opt = self._selected or self._default_option(self.seg_type.value())
        sel = [(i, r) for i, r in enumerate(self._pl_rows) if r.is_checked()]
        if not sel:
            return
        items = [(self._pl_entries[i], opt) for i, _ in sel]
        self._enqueue_history(items)
        self._finish_batch_enqueue()

    def _ensure_tools(self, then_analyze=False):
        self._tools_ready = False
        self._set_state("libraries")
        self.lbl_msg.setText(tr("Downloading required libraries…"))
        self.btn_download.set_text(tr("Downloading Libraries…"))
        self.btn_download.setEnabled(False)
        self._setup = SetupWorker(self)
        self._setup.status.connect(lambda t: self.lbl_msg.setText(t))

        def done(ok, err):
            self._tools_ready = ok
            self.btn_download.set_text(tr("Analyze") if self._is_multi() else tr("Download"))
            if ok and then_analyze:
                self._analyze_timer.start()
            elif not ok:
                self._set_state("error")
                self.lbl_msg.setText(tr("Failed to download libraries."))
            self._refresh_download_enabled()
        self._setup.done.connect(done)
        self._setup.start()

    def _on_probe_done(self, info):
        if self.sender() is not self._active_worker:
            return     # устаревший результат — игнорируем
        cache.put(self._analyzing_url, downloader.slim_info(info))
        self._apply_info(info)

    def _apply_info(self, info):
        self._info = info
        self._populate_selector()
        # Вместо карточки — pending-строка в истории (подсвечена; ждёт Download).
        url = self._analyzing_url
        entry = {"id": uuid.uuid4().hex[:12], "url": url,
                 "host": history.host_label(url), "title": info.get("title") or "",
                 "uploader": info.get("uploader") or info.get("channel") or "",
                 "duration": info.get("duration") or 0,
                 "height": info.get("height") or 0, "fps": info.get("fps") or 0,
                 "path": None, "thumb": "", "ts": 0, "_thumb_url": info.get("thumbnail")}
        if self._fetching_row is not None:
            entry["id"] = self._fetching_row.entry.get("id", entry["id"])
            self._fetching_row.to_pending(entry)      # «Fetching…» -> обложка+инфо
            self._pending_row = self._fetching_row
            self._fetching_row = None
        else:
            # Мог остаться прежний pending (повторный анализ той же кэш-ссылки) —
            # убираем его, иначе появится дубль строки.
            self._remove_pending()
            self._pending_row = self.history.insert_pending(entry)
        self._pending_entry = entry                   # после _remove_pending (тот его чистит)
        self._fetch_row_thumb(info.get("thumbnail"), url, self._pending_row)
        self._set_state("ready")

    def _remove_pending(self):
        """Убирает pending/fetching-строку (сменили/стёрли ссылку до старта)."""
        for attr in ("_pending_row", "_fetching_row"):
            row = getattr(self, attr, None)
            if row is not None:
                try:
                    self.history.remove_row(row)
                except Exception:
                    pass
                setattr(self, attr, None)
        self._pending_entry = None

    def _fetch_row_thumb(self, thumb_url, url, row):
        """Тянет постер в pending/загрузочную строку (и запоминает байты для истории)."""
        if not thumb_url or row is None:
            return
        tw = ThumbWorker(thumb_url, self)

        def on_done(data, r=row):
            if not data:
                return
            pm = QPixmap()
            if not pm.loadFromData(data):
                return
            try:
                r._thumb_bytes = data
                r.set_preview(pm)
            except RuntimeError:
                pass
        tw.done.connect(on_done)
        tw.finished.connect(tw.deleteLater)   # не копим воркеры — чистим по завершении
        tw.start()

    def on_window_shown(self):
        """Окно показали: обновляем историю (rebuild сохраняет pending/загрузки)."""
        if self._state not in ("multi_idle", "multi_fetching", "multi_ready",
                               "playlist_fetching", "playlist_ready"):
            self.history.rebuild(history.prune_missing())
        self.app.sync_view_mirrors(self)     # подтянуть идущие загрузки из Spotlight
        # Поле ввода при возврате не должно оказываться «выделенным» (Qt по фокусу
        # окна выделяет весь текст) — снимаем выделение, курсор в конец.
        QTimer.singleShot(0, self._deselect_url)

    def _deselect_url(self):
        self.url_edit.deselect()
        self.url_edit.setCursorPosition(len(self.url_edit.text()))

    def autofill_url(self, text):
        """Автовставка ссылки в одиночное поле при открытии окна (только если
        поле пустое, не мультирежим и мы не заняты). Дальше — как обычная вставка:
        пользователь жмёт Download/Analyze сам."""
        if self._is_multi() or self.is_busy():
            return
        if self.url_edit.text().strip():
            return
        self.url_edit.setText(text)

    def on_external_download(self, entry):
        """Ролик, скачанный в другом месте (Spotlight/Paste/Toast), — вживую
        наезжает в историю окна, если оно открыто (иначе подхватится при показе)."""
        if entry is not None and self.isVisible():
            self.history.insert_new(entry)

    def _on_probe_error(self, msg):
        if self.sender() is not self._active_worker:
            return
        # Лог неудачного анализа в %APPDATA%/Snatchr/logs (для диагностики).
        try:
            from core import logbook
            log = logbook.Log(self._analyzing_url)
            log.event("Analyze failed")
            log.raw(msg)
            log.save_error()
        except Exception:
            pass
        # Вместо надписи — строка анализа превращается в красный крестик и уезжает.
        row = self._fetching_row or self._pending_row
        self._fetching_row = None
        self._pending_row = None
        self._pending_entry = None
        if row is not None:
            row.to_error()
            QTimer.singleShot(2600, lambda r=row: self.history.remove_row(r))
        self.lbl_msg.setText("")
        self._set_state("error")
        # Куки браузера не помогли/не читаются (бот-чек или Chrome-шифрование) —
        # предлагаем указать свой файл cookies.
        if ((downloader.is_auth_error(msg) or downloader.is_cookie_error(msg))
                and not self.settings.get("cookies_file")):
            self.btn_cookies.show()
            self.btn_cookies.raise_()

    def _on_pick_cookies(self):
        from PySide6.QtWidgets import QFileDialog
        self.app.suppress_autohide(True)
        try:
            path, _ = QFileDialog.getOpenFileName(
                self, tr("Choose cookies file"), "",
                "Cookies (*.txt);;All files (*.*)")
        finally:
            self.app.suppress_autohide(False)
        if path:
            self.settings["cookies_file"] = path
            self.app.save_settings()
            self.btn_cookies.hide()
            self._analyze_timer.start()   # повторный анализ уже с файлом кук

    # ------------------------------------------------------------------ #
    #  Multiple Links
    # ------------------------------------------------------------------ #
    def _on_multi_toggle(self, checked):
        self._multi_analyzed = False
        self._info = None
        self.list.clear()
        # При смене режима поле ввода очищается (свежий старт).
        self.url_text.blockSignals(True)
        self.url_text.clear()
        self.url_text.blockSignals(False)
        # Смена режима = свежий старт: поле чистим и убираем проанализированную,
        # но не запущенную (pending/fetching) строку — иначе она «зависала» бы в
        # истории (в т.ч. после скрытия/открытия окна).
        self.url_edit.blockSignals(True)
        self.url_edit.clear()
        self.url_edit.blockSignals(False)
        self._remove_pending()
        if checked:
            self._animate_history_out()       # история уезжает вниз с прозрачностью
            self._populate_multi_selector()
            self.btn_download.fade_text(tr("Analyze"))
            self.btn_download.animate_bg(self.ANALYZE_BG)
            self._set_state("multi_idle")
        else:
            self.btn_download.fade_text(tr("Download"))
            self.btn_download.animate_bg(self.DL_BG)
            self._collapsing = True           # держим большое поле, пока окно ужимается
            QTimer.singleShot(310, self._end_collapse)
            self._set_state("idle")
        self._apply_expand()                  # рост/сжатие окна под список выбора

    def _end_collapse(self):
        self._collapsing = False
        self._layout()                        # переключаем на одиночное поле ввода

    def _apply_expand(self):
        """Окно выше на 50px, пока показан список выбора (мультиссылки/плейлист).
        Для плейлиста растим только когда список ГОТОВ (не во время «Fetching…»),
        иначе окно дёргается вверх ещё до появления списка."""
        want = self._is_multi() or self._state == "playlist_ready"
        if want != (self._extra > 0):
            self.app.set_main_expanded(want)   # смена высоты сама релэйаутит
        else:
            # Высота та же (напр. плейлист +50 -> мультиссылки +50), но раскладка
            # под новый режим (поле/список/шапка) должна перестроиться.
            self._layout()

    def _animate_history_out(self):
        """История плавно уезжает вниз с opacity (при входе в режим списка выбора)."""
        h = self.history
        if not h.isVisible():
            return
        y0 = h.y()
        h.raise_()

        def restore():
            h.setGraphicsEffect(None)
            h.hide()
            h.move(h.x(), y0)
        anim.fade(h, 1.0, 0.0, 220, on_finished=restore)
        anim.animate(self, 0, self.app._s(30), 220,
                     lambda v: h.move(h.x(), y0 + int(v)),
                     easing=QEasingCurve.InCubic, attr="_hist_out_anim")

    def _multi_urls(self):
        out = []
        for ln in self.url_text.toPlainText().splitlines():
            ln = ln.strip()
            if ln.startswith("http://") or ln.startswith("https://"):
                out.append(ln)
        return out

    def _on_multi_text_changed(self):
        if not self._is_multi():
            return
        # Правка ссылок сбрасывает анализ.
        if self._multi_analyzed:
            self._multi_analyzed = False
            self.btn_download.fade_text(tr("Analyze"))
            self.btn_download.animate_bg(self.ANALYZE_BG)
        self._set_state("multi_idle")

    def _start_multi_analyze(self):
        urls = self._multi_urls()
        if not urls:
            return
        if not (tools.have_ytdlp() and tools.have_ffmpeg()):
            self._ensure_tools()
            return
        # Один общий Fetching — без построчного списка во время анализа.
        self._set_state("multi_fetching")
        self.lbl_msg.setText(tr("Fetching info…"))
        self.list.clear()
        self._multi_url_list = urls
        self._multi_infos = [None] * len(urls)
        self._mprobe = MultiProbeWorker(urls, self.settings, self)
        self._mprobe.item.connect(self._on_multi_item)
        self._mprobe.done.connect(self._on_multi_analyze_done)
        self._mprobe.start()

    def _on_multi_item(self, idx, info, err):
        if info:
            self._multi_infos[idx] = info

    def _on_multi_analyze_done(self):
        ok = [i for i, info in enumerate(self._multi_infos) if info]
        if not ok:
            self.lbl_msg.setText(tr("Could not read any of the links."))
            self._set_state("error")
            return
        # Строим карточки (как одиночная) для каждой успешной ссылки.
        s = self.app._s
        self.list.clear()
        self._thumb_workers = []
        placeholder = QPixmap(s(80), s(45))
        placeholder.fill(QColor(self.FIELD_BG))
        placeholder = rounded_pixmap(placeholder, s(80), s(45), s(6))
        for i in ok:
            info = self._multi_infos[i]
            row = InfoCardRow(
                self.list,
                info.get("title") or tr("Unknown"),
                info.get("uploader") or info.get("channel") or tr("Unknown"),
                self._fmt_duration(info.get("duration")),
                fonts.font(s(11), "Medium"), fonts.font(s(10), "Regular"),
                fonts.mono(s(10)), self.TITLE_COLOR, self.TEXT_COLOR, self.MUTED_COLOR,
                s(80), s(45), s(6), s(52))
            row.set_thumb(placeholder)
            self.list.add_row(row)
            turl = info.get("thumbnail")
            if turl:
                tw = ThumbWorker(turl, self)
                tw.done.connect(lambda data, r=row: self._set_row_thumb(r, data))
                tw.start()
                self._thumb_workers.append(tw)
        self._multi_jobs = [
            (self._multi_url_list[i], self._selected,
             (self._multi_infos[i] or {}).get("title")) for i in ok]
        self._multi_analyzed = True
        self.btn_download.fade_text(tr("Download"))
        self.btn_download.animate_bg(self.DL_BG)
        self._set_state("multi_ready")

    def _set_row_thumb(self, row, data):
        if not data:
            return
        img = QImage.fromData(data)
        if img.isNull():
            return
        pm = rounded_pixmap(QPixmap.fromImage(img), row._tw, row._th, row._r)
        row.set_thumb(pm)

    # ------------------------------------------------------------------ #
    #  Селектор форматов
    # ------------------------------------------------------------------ #
    def _populate_selector(self, mode=None):
        if self._is_multi():
            return
        mode = mode or self.seg_type.value()
        if self._info is None:
            opts = [self._default_option(mode)]
        elif mode == "audio":
            opts = downloader.audio_formats(self._info)
        else:
            opts = downloader.video_formats(
                self._info, downloader.is_youtube(self._analyzing_url),
                settings=self.settings)
        self.sel_format.clear()
        self._opt_by_label = {}
        for o in opts:
            self.sel_format.add_item(o["label"])
            self._opt_by_label[o["label"]] = o
        self.sel_format.set_current(opts[0]["label"])
        self._selected = opts[0]

    def _on_mode_change(self, mode):
        if self._is_multi():
            self._populate_multi_selector(mode)
            self._rebuild_multi_jobs()
        else:
            self._populate_selector(mode)

    def _on_format_change(self, label):
        self._selected = self._opt_by_label.get(label) or self._default_option(
            self.seg_type.value())
        if self._is_multi():
            self._rebuild_multi_jobs()
        self._update_timecodes_enabled()   # для Thumbnail таймкоды недоступны

    # ------------------------------------------------------------------ #
    #  Состояния
    # ------------------------------------------------------------------ #
    def _set_state(self, state):
        self._state = state
        # Карточки одиночного видео больше нет — вместо неё pending-строка в истории.
        for wdg in (self.thumb, self.lbl_title, self.lbl_uploader, self.lbl_duration):
            wdg.setVisible(False)
        if state != "error":
            self.btn_cookies.hide()   # кнопка кук — только в состоянии ошибки
        # Одиночный анализ («fetching») теперь показывается строкой в самой истории
        # (спиннер в блоке), поэтому общий lbl_msg/спиннер для него не нужен.
        self.lbl_msg.setVisible(state in ("error", "libraries",
                                          "multi_fetching", "playlist_fetching"))
        # Список выбора (мульти/плейлист) и история окна взаимоисключающи: пока виден
        # список — историю прячем; в остальных «одиночных» состояниях — показываем.
        list_visible = (state in ("multi_ready", "playlist_ready", "downloading")
                        and len(self.list.rows()) > 0)
        self.list.setVisible(list_visible)
        self.history.setVisible(not list_visible and state not in (
            "multi_idle", "multi_fetching", "playlist_fetching", "libraries"))
        if state in ("libraries", "multi_fetching", "playlist_fetching"):
            self.spinner.start()
        else:
            self.spinner.stop()
        # Селектор качества бесполезен, пока форматы не получены (идёт анализ) —
        # блокируем, чтобы не путать пользователя выбором «до Fetching».
        self.sel_format.setEnabled(
            state not in ("fetching", "multi_fetching", "playlist_fetching"))
        self._refresh_download_enabled()
        self._update_timecodes_enabled()
        self._apply_expand()          # плейлист/мульти-выбор — окно выше на 50px

    def _update_timecodes_enabled(self):
        """Таймкоды доступны для одиночного видео (state=ready) вне режима
        Multiple Links; иначе — выключены (и сброшены). Лимит минимальной
        длины видео снят."""
        en = (not self._is_multi() and self._state == "ready"
              and not (self._selected or {}).get("thumbnail"))   # обложка — без обрезки
        # Метки цвет не меняют (всегда основной) — инактив только на полях ввода.
        for f in (self.tc_start, self.tc_end):
            f.setEnabled(en)
            if not en:
                f.clear_code()

    def _section_arg(self):
        """«*START-END» для yt-dlp по введённым таймкодам, None (вся длина) или
        'invalid' (показана ошибка). 00:00:00 в поле = «не задано». Страхуемся,
        даже если UI-защита (выключение полей) не сработала."""
        if not self.tc_start.isEnabled():
            return None
        s0, e0 = self.tc_start.seconds(), self.tc_end.seconds()
        if s0 == 0 and e0 == 0:
            return None                       # оба по нулям — вся длина
        dur = int((self._info or {}).get("duration") or 0)
        start = s0
        end = e0 if e0 > 0 else dur           # To=00:00:00 -> до конца видео
        if dur and end > dur:
            end = dur
        if start < 0 or end <= start or (dur and start >= dur):
            self._show_status(tr("Invalid time range"), self.ERR_COLOR, self._err_pm)
            return "invalid"
        return f"*{start}-{end}"

    def _refresh_download_enabled(self):
        if self._state == "downloading":
            return
        if self._is_multi():
            en = (self._tools_ready and bool(self._multi_urls())
                  and self._state in ("multi_idle", "multi_ready"))
        elif self._state == "playlist_ready":
            en = self._tools_ready and any(r.is_checked()
                                           for r in getattr(self, "_pl_rows", []))
        else:
            # Download активен только если ввод совпадает с проанализированной ссылкой.
            cur = (self.url_edit.text() or "").strip()
            en = (self._state == "ready" and self._tools_ready
                  and cur == self._analyzing_url)
        self.btn_download.setEnabled(en)

    def _reset_info(self):
        self._info = None
        self._active_worker = None
        self._remove_pending()             # проанализированная строка уезжает обратно
        self._clear_pl_header()            # убрать закреплённую шапку плейлиста
        self.lbl_msg.setText("")
        self.list.clear()
        # Селектор форматов возвращаем в дефолт.
        self.sel_format.clear()
        self.sel_format.add_item(tr("Best Quality"))
        self.sel_format.set_current(tr("Best Quality"))
        self._selected = self._default_option(self.seg_type.value())

    # ------------------------------------------------------------------ #
    #  Скачивание
    # ------------------------------------------------------------------ #
    def _on_download_click(self):
        if self._is_multi():
            if self._multi_analyzed:
                self._start_multi_download()
            else:
                self._start_multi_analyze()
        elif self._state == "playlist_ready":
            self._start_playlist_download()
        elif self._state == "ready":
            self._start_download()

    def _start_download(self):
        """Download по одиночной ссылке: pending-строка становится загрузкой
        (свой прогресс + стоп), планировщик уважает лимит. Поле ввода очищается —
        можно кидать следующую ссылку, история копится."""
        if self._pending_row is None or self._pending_entry is None or self._selected is None:
            return
        entry = self._pending_entry
        url = entry["url"]
        sec = self._section_arg()
        if sec == "invalid":
            return                       # ошибка интервала уже показана
        option = self._selected
        if sec:
            option = dict(self._selected)
            option["section"] = sec
        row = self._pending_row
        self._pending_row = None
        self._pending_entry = None
        # Аудио — пилюля разрешения не нужна (по переключателю Video/Audio).
        entry["is_audio"] = (self.seg_type.value() == "audio")
        entry["is_image"] = bool(option.get("thumbnail"))   # обложка — без анимации
        row.start_downloading()
        dl_id = entry["id"]
        convert = bool(downloader.should_convert(option, url, self.settings))
        self._dls[dl_id] = {"row": row, "frac": 0.0, "convert": convert, "url": url,
                            "title": entry.get("title") or None,
                            "uploader": entry.get("uploader") or None,
                            "thumb_url": entry.get("_thumb_url")}
        self._sched.submit(dl_id, option, url, entry.get("title") or None, managed=True)
        self.app.mirror_start("window", dl_id, entry)
        self.app.report_active_downloads("window", len(self._dls))
        # Поле свободно для следующей ссылки; состояние — в idle (история видна).
        self.url_edit.blockSignals(True)
        self.url_edit.clear()
        self.url_edit.blockSignals(False)
        self.btn_cancel.hide()
        self._info = None
        self._analyzing_url = ""
        self._reset_info()
        self._set_state("idle")

    # --- построчные загрузки в окне (общий планировщик) ----------------- #
    def _on_row_progress(self, dl_id, p):
        d = self._dls.get(dl_id)
        if d is None:
            return
        # С конвертацией постобработку не показываем отдельным этапом: полоса
        # уже на 50% после скачивания, дальше её продолжит сама конвертация.
        if p.get("stage") == "post" and d["convert"]:
            return
        frac, pct = downloader.overall_progress(p, d["convert"])
        if pct is not None:
            p = dict(p)
            p["percent_str"] = pct       # процент в тексте — по той же шкале
        d["frac"] = frac
        if d["row"] is not None:
            d["row"].set_progress(frac, p)
        self.app.mirror_progress("window", dl_id, frac, p)

    def _on_row_done(self, dl_id, dest):
        d = self._dls.pop(dl_id, None)
        tb = None
        if d is not None and d.get("row") is not None:
            try:
                tb = getattr(d["row"], "_thumb_bytes", None)
            except RuntimeError:
                tb = None
        # Пишем в историю без авто-уведомлений — зеркало финиширует строку в
        # Spotlight (mirror_finish).
        entry = self.app.record_download(
            dest, d["url"] if d else "", d.get("title") if d else None,
            notify_window=False, thumb_bytes=tb,
            thumb_url=d.get("thumb_url") if d else None,
            uploader=d.get("uploader") if d else None)
        if entry is None:
            # «Завершилось», но файла нет (пропал/не записался) — трактуем как сбой:
            # убираем строку и зеркало, показываем ошибку.
            if d is not None and d.get("row") is not None:
                self.history.remove_row(d["row"])
            self.app.mirror_remove("window", dl_id)
            self.app.report_active_downloads("window", len(self._dls))
            self._show_status(tr("Download failed"), self.ERR_COLOR, self._err_pm)
            return
        if d is not None and d.get("row") is not None:
            d["row"].finish(entry, pulse=True)
        self.app.mirror_finish("window", dl_id, entry)
        self.app.report_active_downloads("window", len(self._dls))

    def _on_row_failed(self, dl_id, msg):
        d = self._dls.pop(dl_id, None)
        if d is None:
            return                       # уже отменено пользователем (Stop)
        if d.get("row") is not None:
            self.history.remove_row(d["row"])
        self.app.mirror_remove("window", dl_id)
        self.app.report_active_downloads("window", len(self._dls))
        if (msg or "").strip() and msg.strip() != "Stopped":
            self._show_status(msg, self.ERR_COLOR, self._err_pm)

    def _cancel_row(self, entry):
        dl_id = entry.get("id")
        if self.cancel_own(dl_id):
            return
        if dl_id in self._mirrors:
            self.app.request_cancel(dl_id)   # владелец — Spotlight

    def cancel_own(self, dl_id):
        d = self._dls.pop(dl_id, None)
        if d is None:
            return False
        self._sched.cancel(dl_id)
        if d.get("row") is not None:
            self.history.remove_row(d["row"])
        self.app.mirror_remove("window", dl_id)
        self.app.report_active_downloads("window", len(self._dls))
        return True

    # --- зеркала загрузок из Spotlight --------------------------------- #
    def add_mirror(self, dl_id, entry):
        if dl_id in self._mirrors:
            return
        row = self.history.insert_downloading(entry)
        self._mirrors[dl_id] = row
        self._fetch_row_thumb(entry.get("_thumb_url"), entry.get("url", ""), row)

    def update_mirror(self, dl_id, frac, info=None):
        r = self._mirrors.get(dl_id)
        if r is not None:
            r.set_progress(frac, info)

    def finish_mirror(self, dl_id, entry):
        r = self._mirrors.pop(dl_id, None)
        if r is None:
            return
        if entry is not None:
            r.finish(entry, pulse=True)
        else:
            self.history.remove_row(r)

    def remove_mirror(self, dl_id):
        r = self._mirrors.pop(dl_id, None)
        if r is not None:
            self.history.remove_row(r)

    def set_mirror_meta(self, dl_id, thumb_bytes, title, uploader, height=0, fps=0):
        r = self._mirrors.get(dl_id)
        if r is None:
            return
        try:
            if thumb_bytes:
                pm = QPixmap()
                if pm.loadFromData(thumb_bytes):
                    r._thumb_bytes = thumb_bytes
                    r.set_preview(pm)
            if title and not r.entry.get("title"):
                r.entry["title"] = title
            if uploader and not r.entry.get("uploader"):
                r.entry["uploader"] = uploader
            if height:
                r.entry["height"] = height
            if fps:
                r.entry["fps"] = fps
            r._sub = r._make_sub()
            r.update()
        except RuntimeError:
            pass

    def active_downloads(self):
        """Снимок своих идущих загрузок (для зеркалирования при открытии другого
        окна): [(dl_id, entry, frac)]."""
        out = []
        for dl_id, d in self._dls.items():
            out.append((dl_id, {
                "id": dl_id, "url": d["url"], "host": history.host_label(d["url"]),
                "title": d.get("title") or "", "uploader": d.get("uploader") or "",
                "path": None, "thumb": "", "ts": 0,
                "_thumb_url": d.get("thumb_url")}, d.get("frac", 0.0)))
        return out

    def _copy_row(self, entry):
        path = entry.get("path", "")
        if path and os.path.isfile(path):
            self.app._copy_file_to_clipboard(path)

    def _more_row(self, entry, gpos):
        import time as _t
        if _t.monotonic() - getattr(self.app, "_menu_closed_ts", 0.0) < 0.25:
            return                       # повторный клик — Popup уже закрылся
        from tray import TrayMenu
        items = [
            (tr("Open"), lambda e=entry: self.app.play_file(e.get("path", ""))),
            (tr("Remove From List"), lambda e=entry: self._remove_entry(e)),
            (tr("Delete"), lambda e=entry: self._delete_entry(e), "danger"),
        ]
        self._more_menu = TrayMenu(self.app, items)
        self._more_menu.popup_at(gpos)

    def _remove_entry(self, entry):
        history.remove(entry.get("id", ""))
        self.app.refresh_histories()

    def _delete_entry(self, entry):
        if self.app.delete_file(entry):
            self.app.refresh_histories()
        else:
            self.history.flash_error(entry.get("id", ""),
                                     tr("Couldn't delete — file in use"))

    def _start_multi_download(self):
        if not self._multi_jobs:
            return
        opt = self._selected
        infos = getattr(self, "_multi_infos", [])
        items = []
        for j, job in enumerate(self._multi_jobs):
            url = job[0]
            title = job[2] if len(job) > 2 else None
            info = infos[j] if j < len(infos) and infos[j] else {}
            items.append(({"url": url, "title": title,
                           "thumbnail": info.get("thumbnail"),
                           "uploader": info.get("uploader") or info.get("channel"),
                           "duration": info.get("duration")}, opt))
        self._enqueue_history(items)
        self._finish_batch_enqueue()

    def _enqueue_history(self, items):
        """Ставит выбранные ролики (мульти/плейлист) в историю окна на загрузку
        через общий планировщик (лимит parallel_downloads)."""
        for e, opt in items:
            url = e["url"]
            entry = {"id": uuid.uuid4().hex[:12], "url": url,
                     "host": history.host_label(url), "title": e.get("title") or "",
                     "uploader": e.get("uploader") or "", "duration": e.get("duration") or 0,
                     "is_audio": bool(opt.get("audio") or opt.get("mp3")),
                     "is_image": bool(opt.get("thumbnail")),
                     "path": None, "thumb": "", "ts": 0, "_thumb_url": e.get("thumbnail")}
            row = self.history.insert_downloading(entry)
            dl_id = entry["id"]
            convert = bool(downloader.should_convert(opt, url, self.settings))
            self._dls[dl_id] = {"row": row, "frac": 0.0, "convert": convert, "url": url,
                                "title": entry["title"] or None,
                                "uploader": entry["uploader"] or None,
                                "thumb_url": entry.get("_thumb_url")}
            self._sched.submit(dl_id, opt, url, entry["title"] or None, managed=True)
            self._fetch_row_thumb(entry.get("_thumb_url"), url, row)
            self.app.mirror_start("window", dl_id, entry)
        self.app.report_active_downloads("window", len(self._dls))

    def _finish_batch_enqueue(self):
        """После постановки пачки в историю — вернуть окно к виду истории."""
        self.list.clear()
        self.url_edit.blockSignals(True)
        self.url_edit.clear()
        self.url_edit.blockSignals(False)
        self.btn_cancel.hide()
        if self._is_multi():
            self.cb_multi.setChecked(False)   # выключит Multiple Links + свернёт окно
        self._reset_info()
        self._set_state("idle")

    # ------------------------------------------------------------------ #
    #  Статус
    # ------------------------------------------------------------------ #
    def _show_status(self, text, color, icon_pm):
        s = self.app._s
        if icon_pm is not None:
            self.status_icon.setPixmap(icon_pm)
            self.status_icon.show()
            self.status_text.setGeometry(s(19), 0, self.width_ - 2 * s(12) - s(19), s(18))
        else:
            self.status_icon.hide()
            self.status_text.setGeometry(0, 0, self.width_ - 2 * s(12), s(18))
        self.status_text.setText(text)
        self.status_text.setStyleSheet(f"color: {color}; background: transparent;")
        self.status_box.show()
        anim.fade(self.status_box, 0.0, 1.0, 200)
        anim.animate(self, self._status_y + s(8), self._status_y, 200,
                     lambda v: self.status_box.move(self._dl_pad, int(round(v))),
                     easing=QEasingCurve.OutCubic, attr="_status_anim")
        self._status_timer.start(3000)

    def _hide_status(self):
        s = self.app._s
        anim.fade(self.status_box, 1.0, 0.0, 200, on_finished=self.status_box.hide)
        anim.animate(self, self._status_y, self._status_y + s(8), 200,
                     lambda v: self.status_box.move(self._dl_pad, int(round(v))),
                     easing=QEasingCurve.InCubic, attr="_status_anim")

    # ------------------------------------------------------------------ #
    @staticmethod
    def _fmt_duration(secs):
        if not secs:
            return "--:--"
        secs = int(secs)
        h, rem = divmod(secs, 3600)
        m, ss = divmod(rem, 60)
        return f"{h}:{m:02d}:{ss:02d}" if h else f"{m:02d}:{ss:02d}"
