import os

from PySide6.QtWidgets import QWidget, QLabel, QFrame, QFileDialog

from core import fonts, themes
from core.i18n import tr
from core.icons import themed_icon
from ui.widgets import (
    IconButton, LinkButton, CheckBox, SegmentedControl, WindowDragMixin
)


class SettingsPage(WindowDragMixin, QWidget):
    """Экран настроек: выбор папки загрузок + пост-процессинг."""

    POST_PROCESSING = [
        ("Embed Thumbnail",  "embed_thumbnail"),
        ("Embed Metadata",   "embed_metadata"),
    ]

    CONVERT_TIP = ("Re-encode YouTube videos into an editor-friendly format\n"
                   "(SDR → H.264, HDR → HEVC 10-bit, mp4) so they import\n"
                   "cleanly into video editing software. Uses the GPU when\n"
                   "available, with a CPU fallback.")
    USAGE_TIP = ("Pinned: the tray icon opens and closes the window.\n"
                 "Auto-hide: the tray icon opens the window; it closes\n"
                 "on Esc or when you click outside it.")
    DRAG_TIP = ("Drag the window by holding an empty area at the top.\n"
                "The position resets the next time the window is shown.")

    def __init__(self, parent, app, settings, width, height):
        super().__init__(parent)
        self.app = app
        self.settings = settings
        self.width_ = width
        self.height_ = height
        self._checks = {}
        self._load_theme()
        self.init_window_drag(app)
        self.resize(width, height)
        self._build()

    def _load_theme(self):
        p = themes.palette(self.settings.get("theme", themes.DEFAULT_THEME))
        self._pal = p
        self.CARD_BG       = p["card_bg"]
        self.TITLE_COLOR   = p["title"]
        self.SECTION_COLOR = p["icon"]
        self.TEXT_COLOR    = p["text"]
        self.MUTED_COLOR   = p["muted"]
        self.ACCENT        = p["accent"]
        self.ACCENT_HOVER  = p["accent_hover"]
        self.BORDER        = p["border"]
        self.LINK          = p["link"]
        self.LINK_HOVER    = p["link_hover"]
        self.CHOOSE        = p["choose"]
        self.CHOOSE_BG     = p["choose_bg"]
        self.CHOOSE_BG_H   = p["choose_bg_h"]
        self.CB_OFF        = p["cb_off"]
        self.CB_ON         = p["cb_on"]
        self.SEG_BG        = p["seg_bg"]
        self.SEG_SEL       = p["seg_sel"]
        self.ON_ACCENT     = p["on_accent"]

    # ------------------------------------------------------------------ #
    def _label(self, text, font, color, x, y, w=None, h=None):
        lbl = QLabel(text, self)
        lbl.setFont(font)
        lbl.setStyleSheet(f"color: {color}; background: transparent;")
        if w is not None and h is not None:
            lbl.setGeometry(x, y, w, h)
        else:
            lbl.move(x, y)
            lbl.adjustSize()
        return lbl

    def _build(self):
        s = self.app._s
        pad = s(16)
        card_w = self.width_ - 2 * pad

        # --- заголовок + инфо-кнопка ------------------------------------ #
        self._label(tr("Settings"), fonts.font(s(14), "Semibold"),
                    self.TITLE_COLOR, pad, s(12))

        theme = self.settings.get("theme", themes.DEFAULT_THEME)
        ic_info   = themed_icon(theme, "info.png", self._pal["icon"], s(19))
        ic_info_h = themed_icon(theme, "info.png", self._pal["icon_hover"], s(19))
        info_x = self.width_ - pad - s(26)
        self.btn_about = IconButton(self, ic_info, ic_info_h, s(19), self.app.open_about)
        self.btn_about.setGeometry(info_x, s(10), s(26), s(26))

        # --- секция: Download Folder ------------------------------------ #
        sec1_y = s(42)
        self._section_title(tr("Download Folder"), pad, sec1_y)
        card1_y = sec1_y + s(16)
        card1_h = self._build_folder_card(pad, card1_y, card_w)

        # --- секция: Post-Processing ------------------------------------ #
        sec2_y = card1_y + card1_h + s(14)
        self._section_title(tr("Post-Processing"), pad, sec2_y)
        card2_y = sec2_y + s(16)
        card2_h = self._build_postproc_card(pad, card2_y, card_w)

        # --- секция: Processing ----------------------------------------- #
        sec3_y = card2_y + card2_h + s(14)
        self._section_title(tr("Processing"), pad, sec3_y)
        card3_y = sec3_y + s(16)
        card3_h = self._build_processing_card(pad, card3_y, card_w)

        # --- секция: Usage ---------------------------------------------- #
        sec4_y = card3_y + card3_h + s(14)
        self._section_title(tr("Usage"), pad, sec4_y)
        card4_y = sec4_y + s(24)   # больше воздуха под заголовком Usage
        card4_h = self._build_usage_card(pad, card4_y, card_w)

        # --- Кнопки обновления yt-dlp / ffmpeg (под блоком Usage) ------- #
        self._build_update_buttons(pad, card4_y + card4_h + s(16))

    # ------------------------------------------------------------------ #
    def _section_title(self, text, x, y):
        s = self.app._s
        self._label(text, fonts.font(s(10), "Medium"),
                    self.SECTION_COLOR, x, y)

    def _card(self, x, y, w, h):
        # Подложка карточек убрана (прозрачный контейнер для дочерних виджетов).
        card = QFrame(self)
        card.setGeometry(x, y, w, h)
        card.setStyleSheet("background: transparent;")
        return card

    def _build_folder_card(self, x, y, card_w):
        s = self.app._s
        card_h = s(42)
        card = self._card(x, y, card_w, card_h)

        # Иконка папки убрана — оставлен только путь (с прямыми слешами).
        self.path_lbl = QLabel(self._short_path(self.settings["download_path"]), card)
        self.path_lbl.setFont(fonts.mono(s(11)))
        self.path_lbl.setStyleSheet(f"color: {self.TEXT_COLOR}; background: transparent;")
        self.path_lbl.setGeometry(s(4), card_h // 2 - s(9), card_w - s(90), s(18))

        browse = LinkButton(card, tr("Choose"), fonts.font(s(10), "Semibold"),
                            self.CHOOSE, self.CHOOSE, self._choose_folder,
                            hover_bg=self.CHOOSE_BG_H, radius=s(6),
                            base_bg=self.CHOOSE_BG, press_pop=True)
        browse.setGeometry(card_w - s(64) - s(8), card_h // 2 - s(12), s(64), s(24))

        return card_h

    def _build_postproc_card(self, x, y, card_w):
        s = self.app._s
        row_h = s(30)
        top_pad = s(8)
        card_h = top_pad * 2 + row_h * len(self.POST_PROCESSING)
        card = self._card(x, y, card_w, card_h)

        ry = top_pad
        for label, key in self.POST_PROCESSING:
            cb = CheckBox(card, tr(label), fonts.font(s(12), "Regular"),
                          self.TEXT_COLOR, self.CB_OFF, self.CB_ON, s(17), s(5))
            cb.setChecked(bool(self.settings.get(key, False)))
            cb.setGeometry(s(14), ry, card_w - s(28), row_h)
            cb.toggled.connect(lambda checked, k=key: self._set_flag(k, checked))
            self._checks[key] = cb
            ry += row_h

        return card_h

    def _build_processing_card(self, x, y, card_w):
        s = self.app._s
        row_h = s(30)
        top_pad = s(8)
        card_h = top_pad * 2 + row_h
        card = self._card(x, y, card_w, card_h)

        cb = CheckBox(card, tr("Convert Youtube Videos"), fonts.font(s(12), "Regular"),
                      self.TEXT_COLOR, self.CB_OFF, self.CB_ON, s(17), s(5))
        cb.setChecked(bool(self.settings.get("convert_yt", False)))
        cb.setGeometry(s(14), top_pad, card_w - s(28), row_h)
        cb.setToolTip(tr(self.CONVERT_TIP))
        cb.toggled.connect(lambda checked: self._set_flag("convert_yt", checked))
        self._checks["convert_yt"] = cb

        return card_h

    def _build_usage_card(self, x, y, card_w):
        s = self.app._s
        card_h = s(38)   # повыше — чтобы пилюля segmented могла «выходить» за блок
        card = self._card(x, y, card_w, card_h)

        # Segmented занимает левую половину карточки.
        seg_w = card_w // 2 - s(6)
        seg = SegmentedControl(
            card,
            [(tr("Pinned"), "toggle"), (tr("Auto-hide"), "focus")],
            self.settings.get("usage_mode", "toggle"),
            fonts.font(s(11), "Medium"),
            self.SEG_BG, self.SEG_SEL,
            self.MUTED_COLOR, self.ON_ACCENT, s(9)
        )
        seg.setGeometry(0, 0, seg_w, card_h)
        seg.setToolTip(tr(self.USAGE_TIP))
        seg.changed.connect(self._on_usage_change)
        self._usage_seg = seg

        # Справа — чекбокс перетаскивания окна.
        drag_x = card_w // 2 + s(8)
        cb = CheckBox(card, tr("Allow Dragging"), fonts.font(s(12), "Regular"),
                      self.TEXT_COLOR, self.CB_OFF, self.CB_ON, s(17), s(5))
        cb.setChecked(bool(self.settings.get("allow_dragging", False)))
        cb.setGeometry(drag_x, 0, card_w - drag_x, card_h)
        cb.setToolTip(tr(self.DRAG_TIP))
        cb.toggled.connect(self._on_drag_change)
        self._checks["allow_dragging"] = cb

        return card_h

    def _build_update_buttons(self, x, y):
        from PySide6.QtGui import QFontMetrics
        s = self.app._s
        bh, gap = s(28), s(12)
        font = fonts.font(s(11), "Semibold")
        fm = QFontMetrics(font)
        t1, t2 = tr("Update yt-dlp"), tr("Update ffmpeg")
        # Ширина под текст (минимум s(120)) — на русском надписи длиннее.
        bw1 = max(s(120), fm.horizontalAdvance(t1) + s(24))
        bw2 = max(s(120), fm.horizontalAdvance(t2) + s(24))

        self.btn_update = LinkButton(
            self, t1, font, self.CHOOSE, self.LINK_HOVER, self.app.start_ytdlp_update,
            hover_bg=self.CHOOSE_BG_H, radius=s(6), base_bg=self.CHOOSE_BG)
        self.btn_update.setGeometry(x, y, bw1, bh)

        self.btn_update_ff = LinkButton(
            self, t2, font, self.CHOOSE, self.LINK_HOVER, self.app.start_ffmpeg_update,
            hover_bg=self.CHOOSE_BG_H, radius=s(6), base_bg=self.CHOOSE_BG)
        self.btn_update_ff.setGeometry(x + bw1 + gap, y, bw2, bh)

    def _on_usage_change(self, value):
        self.app.set_usage_mode(value)

    def _on_drag_change(self, value):
        self.app.set_allow_dragging(value)

    # ------------------------------------------------------------------ #
    def _choose_folder(self):
        initial = self.settings.get("download_path") or os.path.expanduser("~")
        if not os.path.isdir(initial):
            initial = os.path.expanduser("~")

        # В режиме Auto-hide диалог не должен прятать окно при потере фокуса.
        self.app.suppress_autohide(True)
        try:
            path = QFileDialog.getExistingDirectory(self, "Choose download folder", initial)
        finally:
            self.app.suppress_autohide(False)
        if path:
            path = os.path.normpath(path)
            self.settings["download_path"] = path
            self.path_lbl.setText(self._short_path(path))
            self.app.save_settings()

    def _set_flag(self, key, value):
        self.settings[key] = bool(value)
        self.app.save_settings()

    def _short_path(self, path, limit=46):
        # Показываем путь с прямыми слешами (как в macOS).
        path = path.replace("\\", "/")
        if len(path) <= limit:
            return path
        return "…" + path[-(limit - 1):]
