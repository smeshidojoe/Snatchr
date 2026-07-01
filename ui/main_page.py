from PySide6.QtCore import Qt, QTimer, QEasingCurve
from PySide6.QtGui import QPalette, QColor, QPixmap, QImage
from PySide6.QtWidgets import QWidget, QLineEdit, QTextEdit, QLabel

from core import fonts, downloader, tools, cache, themes
from core.i18n import tr
from core.icons import themed_icon, themed_pixmap
from core.workers import (
    ProbeWorker, ThumbWorker, DownloadWorker, SetupWorker,
    MultiProbeWorker, MultiDownloadWorker, PlaylistProbeWorker,
)
from ui.widgets import (
    IconButton, LinkButton, CheckBox, SegmentedControl, Selector, WindowDragMixin,
    DownloadButton, ProgressBar, Spinner, ScrollList, InfoCardRow,
    PlaylistHeader, rounded_pixmap,
)
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

        self.lbl_size = QLabel(self)
        self.lbl_size.setFont(fonts.mono(s(9)))
        self.lbl_size.setStyleSheet(f"color: {self.MUTED_COLOR}; background: transparent;")
        self.lbl_size.setAlignment(Qt.AlignVCenter | Qt.AlignRight)
        self.lbl_size.hide()
        self.lbl_progress = QLabel(self)
        self.lbl_progress.setFont(fonts.mono(s(10)))
        self.lbl_progress.setStyleSheet(f"color: {self.TEXT_COLOR}; background: transparent;")
        self.lbl_progress.setAlignment(Qt.AlignVCenter | Qt.AlignRight)
        self.lbl_progress.hide()
        self.progress_bar = ProgressBar(self, self.PROG_TRACK, self.DL_BG, s(3))
        self.progress_bar.hide()

    # ------------------------------------------------------------------ #
    def _layout(self):
        s = self.app._s
        pad = s(12)
        w = self.width_
        extra = self._extra
        fy, fh = s(12), s(34)

        if self._is_multi():
            self.url_text.setGeometry(pad, fy, w - 2 * pad, fh + extra)
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

        input_bottom = fy + fh + extra
        ml_y = input_bottom + s(8)
        self.cb_multi.setGeometry(pad, ml_y, w - 2 * pad, s(26))

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

        self.list.setGeometry(pad, info_y, w - 2 * pad, self._status_y - s(6) - info_y)

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
        self._set_state("fetching")
        self.lbl_msg.setText(tr("Fetching info…"))
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
        # Шапка: слева — название плейлиста, справа — Deselect All + счётчик.
        self.pl_header = PlaylistHeader(
            self.list, info.get("title") or tr("Playlist"), len(entries),
            fonts.font(s(11), "Semibold"), fonts.font(s(10), "Medium"),
            fonts.font(s(10), "Regular"),
            self.TITLE_COLOR, self.TEXT_COLOR, self.TITLE_COLOR, self.MUTED_COLOR, s(30))
        self.pl_header.toggled.connect(self._on_pl_toggle)
        self.list.add_row(self.pl_header)

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

        # Плейлист скачивается в Best Quality (как мульти).
        self.sel_format.clear()
        self.sel_format.add_item(tr("Best Quality"))
        self.sel_format.set_current(tr("Best Quality"))
        self._selected = self._default_option(self.seg_type.value())
        self._set_state("playlist_ready")

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
        opt = self._default_option(self.seg_type.value())
        sel = [(i, r) for i, r in enumerate(self._pl_rows) if r.is_checked()]
        if not sel:
            return
        jobs = [(self._pl_entries[i]["url"], opt, self._pl_entries[i].get("title"))
                for i, _ in sel]
        self._dl_context = "playlist"
        self._dl_rows = [r for _, r in sel]   # строки, выровненные с jobs
        # Для плейлистов Embed-опции принудительно выключаем (даже если галочки
        # включены) — массовая выгрузка субтитров/обложек ненадёжна.
        self._run_batch(jobs, settings=self._no_embed_settings())

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
        self.lbl_title.setText(info.get("title") or tr("Unknown"))
        self.lbl_uploader.setText(info.get("uploader") or info.get("channel") or tr("Unknown"))
        self.lbl_duration.setText(self._fmt_duration(info.get("duration")))
        self._set_placeholder_thumb()
        turl = info.get("thumbnail")
        if turl:
            self._thumb = ThumbWorker(turl, self)
            self._thumb.done.connect(self._on_thumb)
            self._thumb.start()
        self._populate_selector()
        self._set_state("ready")

    def _on_probe_error(self, msg):
        if self.sender() is not self._active_worker:
            return
        # Показываем конкретную причину (бот-чек / регион / приватное / …),
        # а не общее «не удалось прочитать» — ссылка часто читается, но заблокирована.
        self.lbl_msg.setText(
            downloader.friendly_error(msg, default="Could not read this link."))
        self._set_state("error")
        # Куки браузера уже не помогли (или их нет) — предлагаем указать свой файл.
        if downloader.is_auth_error(msg) and not self.settings.get("cookies_file"):
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

    def _on_thumb(self, data):
        if not data:
            return
        img = QImage.fromData(data)
        if img.isNull():
            return
        pm = rounded_pixmap(QPixmap.fromImage(img), self._thumb_w, self._thumb_h, self._thumb_r)
        if pm:
            self.thumb.setPixmap(pm)

    def _set_placeholder_thumb(self):
        base = QPixmap(self._thumb_w, self._thumb_h)
        base.fill(QColor(self.FIELD_BG))
        self.thumb.setPixmap(rounded_pixmap(base, self._thumb_w, self._thumb_h, self._thumb_r))

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
        self.url_edit.clear()
        self.app.set_main_expanded(checked)   # анимация роста/сжатия окна + relayout
        if checked:
            self._populate_multi_selector()
            self.btn_download.fade_text(tr("Analyze"))
            self.btn_download.animate_bg(self.ANALYZE_BG)
            self._set_state("multi_idle")
        else:
            self.btn_download.fade_text(tr("Download"))
            self.btn_download.animate_bg(self.DL_BG)
            self._set_state("idle")

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
                self._info, downloader.is_youtube(self._analyzing_url))
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

    # ------------------------------------------------------------------ #
    #  Состояния
    # ------------------------------------------------------------------ #
    def _set_state(self, state):
        self._state = state
        single_info = (state == "ready")
        for wdg in (self.thumb, self.lbl_title, self.lbl_uploader, self.lbl_duration):
            wdg.setVisible(single_info)
        if state != "error":
            self.btn_cookies.hide()   # кнопка кук — только в состоянии ошибки
        self.lbl_msg.setVisible(state in ("fetching", "error", "libraries",
                                          "multi_fetching", "playlist_fetching"))
        self.list.setVisible(state in ("multi_ready", "playlist_ready", "downloading")
                             and len(self.list.rows()) > 0)
        if state in ("fetching", "libraries", "multi_fetching", "playlist_fetching"):
            self.spinner.start()
        else:
            self.spinner.stop()
        self._refresh_download_enabled()

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
        if self._state == "downloading":
            self._stop_download()
        elif self._is_multi():
            if self._multi_analyzed:
                self._start_multi_download()
            else:
                self._start_multi_analyze()
        elif self._state == "playlist_ready":
            self._start_playlist_download()
        elif self._state == "ready":
            self._start_download()

    def _start_download(self):
        url = (self.url_edit.text() or "").strip()
        if not url or self._selected is None:
            return
        self._set_state("downloading")
        self.btn_download.setEnabled(True)
        self._animate_button_to_stop()
        title = self._info.get("title") if self._info else None
        self._dl = DownloadWorker(self._selected, url, self.settings, title, self)
        self._dl.progress.connect(self._on_progress)
        self._dl.status.connect(self._on_status)
        self._dl.finished_ok.connect(self._on_dl_finished)
        self._dl.failed.connect(self._on_dl_failed)
        self._dl.start()

    def _start_multi_download(self):
        if not self._multi_jobs:
            return
        self._dl_context = "multi"
        self._dl_rows = None      # карточки без построчного статуса
        self._run_batch(self._multi_jobs)

    def _no_embed_settings(self):
        """Копия настроек с выключенными Embed-опциями (для плейлистов)."""
        s = dict(self.settings)
        s["embed_thumbnail"] = False
        s["embed_metadata"] = False
        return s

    def _run_batch(self, jobs, settings=None):
        self._set_state("downloading")
        self.btn_download.setEnabled(True)
        self._animate_button_to_stop()
        self._mdl_total = len(jobs)
        self._mdl_done = 0
        self._mdl_fail = 0
        self._batch_stopped = False
        self._mdl = MultiDownloadWorker(jobs, settings or self.settings, self)
        self._mdl.item_progress.connect(self._on_multi_progress)
        self._mdl.item_status.connect(self._on_multi_status)
        self._mdl.item_done.connect(self._on_multi_item_done)
        self._mdl.all_done.connect(self._on_multi_all_done)
        self._mdl.start()

    def _stop_download(self):
        if self._dl is not None:
            self._dl.stop()
        if self._mdl is not None:
            self._batch_stopped = True
            self._mdl.stop()

    def _on_progress(self, p):
        parts = [x for x in (p.get("percent_str"), p.get("speed"),
                             ("ETA " + p["eta"]) if p.get("eta") else "") if x]
        self.lbl_progress.setText("   ·   ".join(parts))
        if p.get("size"):
            self.lbl_size.setText(tr("File Size ~") + p["size"])
        if p.get("frac") is not None:
            self.progress_bar.set_value(p["frac"])

    def _on_status(self, text):
        self.lbl_progress.setText(text)

    def _on_multi_status(self, idx, text):
        self.lbl_progress.setText(f"{idx + 1}/{self._mdl_total}   ·   {text}")

    def _on_multi_progress(self, idx, p):
        # Этап конвертации: не двигаем общую полосу назад — только подпись.
        if p.get("stage") == "convert":
            self.lbl_progress.setText(f"{idx + 1}/{self._mdl_total}   ·   "
                                      f"{p.get('percent_str', 'Converting…')}")
            return
        self.lbl_progress.setText(f"{idx + 1}/{self._mdl_total}   ·   "
                                  f"{p.get('percent_str','')}   ·   {p.get('speed','')}")
        if p.get("size"):
            self.lbl_size.setText(tr("File Size ~") + p["size"])
        frac = (idx + (p.get("frac") or 0.0)) / max(1, self._mdl_total)
        self.progress_bar.set_value(frac)

    def _on_multi_item_done(self, idx, ok, info):
        self._mdl_done += 1
        if not ok:
            self._mdl_fail += 1
        rows = getattr(self, "_dl_rows", None)
        if rows and idx < len(rows):
            rows[idx].set_detail("✓" if ok else "✗",
                                 self.OK_COLOR if ok else self.ERR_COLOR)

    def _on_multi_all_done(self):
        total = self._mdl_total
        fail = getattr(self, "_mdl_fail", 0)
        folder = self._display_path(self.settings.get("download_path", ""))
        if getattr(self, "_batch_stopped", False):
            self._show_status(tr("Stopped"), self.MUTED_COLOR, None)
        elif fail and fail >= total:
            self._show_status(tr("All downloads failed"), self.ERR_COLOR, self._err_pm)
        elif fail:
            self._show_status(f"{tr('Saved with errors')} ({total - fail}/{total})",
                              self.ERR_COLOR, self._err_pm)
        else:
            self._show_status(f"{tr('Saved to')} {folder}", self.OK_COLOR, self._ok_pm)
        ctx = getattr(self, "_dl_context", "multi")
        self._set_state("playlist_ready" if ctx == "playlist" else "multi_ready")
        self._animate_button_to_download(text=tr("Download"))

    def _on_dl_finished(self, dest):
        self._show_status(f"{tr('Saved to')} {self._display_path(self.settings.get('download_path',''))}",
                          self.OK_COLOR, self._ok_pm)
        self._set_state("ready")
        self._animate_button_to_download(text=tr("Download"))

    def _on_dl_failed(self, msg):
        if (msg or "").strip() == "Stopped":
            self._show_status(tr("Stopped"), self.MUTED_COLOR, None)
        else:
            # msg уже человекочитаемое объяснение (401/403/private/…).
            self._show_status(msg or tr("Download failed"), self.ERR_COLOR, self._err_pm)
        self._set_state("ready")
        self._animate_button_to_download(text=tr("Download"))

    # ------------------------------------------------------------------ #
    #  Анимации Download <-> Stop + прогресс
    # ------------------------------------------------------------------ #
    def _animate_button_to_stop(self):
        self.btn_download.fade_text(tr("Stop"))
        self.btn_download.animate_bg(self.STOP_BG, 320)
        full, stop_w = self._dl_full_w, self._stop_w
        x, y, h = self._dl_pad, self._dl_y, self._dl_h

        def shrink(v):
            self.btn_download.setGeometry(x, y, int(round(v)), h)

        def show_progress():
            self._reveal_progress()

        # Сжимаем кнопку, и сразу после — показываем прогресс (snizu-вверх + opacity).
        anim.animate(self, full, stop_w, 320, shrink,
                     easing=QEasingCurve.InOutCubic, on_finished=show_progress,
                     attr="_btn_anim")

    def _reveal_progress(self):
        s = self.app._s
        x, y = self._dl_pad, self._dl_y
        px = x + self._stop_w + s(10)
        pw = self._dl_full_w - self._stop_w - s(10)
        size_y = y + s(2)          # «File Size ~…» сверху
        bar_y = y + s(13)          # полоса толщиной 12
        text_y = y + s(27)         # проценты/скорость/ETA снизу
        self.lbl_size.setText("")
        self.lbl_size.setGeometry(px, size_y, pw, s(10))
        self.progress_bar.set_value(0.0)
        self.progress_bar.setGeometry(px, bar_y, pw, s(12))
        self.lbl_progress.setText("")
        self.lbl_progress.setGeometry(px, text_y, pw, s(11))
        for wdg in (self.lbl_size, self.progress_bar, self.lbl_progress):
            wdg.show()
            anim.fade(wdg, 0.0, 1.0, 220)

        def slide(v):
            o = int(round(v))
            self.lbl_size.move(px, size_y + o)
            self.progress_bar.move(px, bar_y + o)
            self.lbl_progress.move(px, text_y + o)
        anim.animate(self, s(12), 0, 220, slide,
                     easing=QEasingCurve.OutCubic, attr="_prog_anim")

    def _animate_button_to_download(self, text="Download"):
        x, y, h = self._dl_pad, self._dl_y, self._dl_h
        cur = self.btn_download.width()
        self.btn_download.fade_text(text)
        self.btn_download.animate_bg(self.ANALYZE_BG if text == tr("Analyze") else self.DL_BG, 320)
        for wdg in (self.lbl_size, self.lbl_progress, self.progress_bar):
            anim.fade(wdg, 1.0, 0.0, 180, on_finished=wdg.hide)

        def done():
            self.btn_download.setGeometry(x, y, self._dl_full_w, h)
            self._refresh_download_enabled()
        anim.animate(self, cur, self._dl_full_w, 340,
                     lambda v: self.btn_download.setGeometry(x, y, int(round(v)), h),
                     easing=QEasingCurve.InOutCubic, on_finished=done, attr="_btn_anim")

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

    @staticmethod
    def _display_path(path, limit=44):
        p = path or ""
        if len(p) >= 2 and p[1] == ":":
            p = p[2:]
        p = p.replace("\\", "/")
        if len(p) > limit:
            p = "…" + p[-(limit - 1):]
        return p
