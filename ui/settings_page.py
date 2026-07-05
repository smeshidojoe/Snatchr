import os

from PySide6.QtCore import Qt, QRectF, Signal
from PySide6.QtGui import QFontMetrics, QColor, QPainter, QPen, QKeySequence
from PySide6.QtWidgets import QWidget, QLabel, QFrame, QFileDialog, QScrollArea

from core import fonts, themes, i18n
from core.constants import ICONS_DIR, THEMES, LANGUAGES, DEFAULT_LANGUAGE
from core.i18n import tr
from core.icons import themed_icon
from ui.widgets import (
    IconButton, LinkButton, CheckBox, SegmentedControl, Selector, WindowDragMixin,
    SmoothScroll
)

# Отображаемая подпись -> значение cookies_browser в конфиге.
_COOKIE_CHOICES = [
    ("Auto", "auto"), ("Chrome", "chrome"), ("Edge", "edge"),
    ("Firefox", "firefox"), ("Brave", "brave"), ("Opera", "opera"),
    ("Vivaldi", "vivaldi"), ("Chromium", "chromium"),
]

# Токены модификаторов для формата библиотеки keyboard (combo) и отображения.
_MOD_DISPLAY = {"ctrl": "Ctrl", "alt": "Alt", "shift": "Shift", "windows": "Win"}


def combo_to_display(combo):
    """'ctrl+shift+d' -> 'Ctrl+Shift+D' (для показа в кнопке)."""
    out = []
    for tok in (combo or "").split("+"):
        tok = tok.strip()
        if not tok:
            continue
        if tok in _MOD_DISPLAY:
            out.append(_MOD_DISPLAY[tok])
        elif len(tok) == 1:
            out.append(tok.upper())
        else:
            out.append(tok.capitalize())
    return "+".join(out)


class HotkeyEdit(QWidget):
    """Чип-кнопка для смены сочетания: клик -> «Нажмите клавиши…» -> ловит
    следующую комбинацию (хотя бы один модификатор + клавиша). Esc отменяет."""

    changed = Signal(str)             # combo в формате keyboard ('ctrl+shift+d')

    def __init__(self, app, combo, parent, pal):
        super().__init__(parent)
        self.app = app
        self._combo = combo or "ctrl+shift+d"
        self._capturing = False
        self._bg = QColor(pal["sel_chip"])
        self._border = QColor(pal["border"])
        self._text = QColor(pal["text"])
        self._accent = QColor(pal["seg_sel"])
        self._muted = QColor(pal["muted"])
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.StrongFocus)

    def mouseReleaseEvent(self, e):
        self._capturing = True
        self.setFocus()
        self.grabKeyboard()
        # Пока ловим новое сочетание — снимаем текущий глобальный хоткей, иначе
        # нажатие уже назначенной комбинации заодно откроет Spotlight.
        try:
            self.app.suspend_hotkey()
        except Exception:
            pass
        self.update()

    def focusOutEvent(self, e):
        if self._capturing:
            self._cancel()

    def _cancel(self):
        self._capturing = False
        try:
            self.releaseKeyboard()
        except Exception:
            pass
        # Восстанавливаем хоткей (при коммите set_spotlight_combo сам перерегистрирует).
        try:
            self.app.resume_hotkey()
        except Exception:
            pass
        self.update()

    def keyPressEvent(self, e):
        if not self._capturing:
            return super().keyPressEvent(e)
        key = e.key()
        if key == Qt.Key_Escape:
            self._cancel()
            return
        if key in (Qt.Key_Control, Qt.Key_Shift, Qt.Key_Alt, Qt.Key_Meta,
                   Qt.Key_unknown, 0):
            return                        # ждём «настоящую» клавишу
        mods = e.modifiers()
        parts = []
        if mods & Qt.ControlModifier:
            parts.append("ctrl")
        if mods & Qt.AltModifier:
            parts.append("alt")
        if mods & Qt.ShiftModifier:
            parts.append("shift")
        if mods & Qt.MetaModifier:
            parts.append("windows")
        if not parts:
            return                        # без модификатора глобальный хоткей опасен
        name = QKeySequence(key).toString().lower()
        if not name:
            return
        self._combo = "+".join(parts + [name])
        self._cancel()
        self.changed.emit(self._combo)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        s = self.app._s
        w, h = self.width(), self.height()
        p.setPen(QPen(self._accent if self._capturing else self._border, 1))
        p.setBrush(self._bg)
        p.drawRoundedRect(QRectF(0.5, 0.5, w - 1, h - 1), s(7), s(7))
        p.setFont(fonts.font(s(11), "Medium"))
        if self._capturing:
            p.setPen(self._muted)
            text = tr("Press keys…")
        else:
            p.setPen(self._text)
            text = combo_to_display(self._combo)
        p.drawText(QRectF(s(10), 0, w - s(20), h),
                   Qt.AlignVCenter | Qt.AlignLeft, text)
        p.end()


class SettingsPage(WindowDragMixin, QWidget):
    """Экран настроек: папка загрузок, обработка, куки, режим работы."""

    CONVERT_TIP = ("Re-encode YouTube videos into an editor-friendly format\n"
                   "(SDR → H.264, HDR → HEVC 10-bit, mp4) so they import\n"
                   "cleanly into video editing software. Uses the GPU when\n"
                   "available, with a CPU fallback.")
    CLIPBOARD_TIP = ("When you copy a link from a supported site, a toast\n"
                     "appears offering to download it in the background.")
    SPOTLIGHT_TIP = ("A quick launcher (global shortcut) to paste a link, download\n"
                     "it, and trim clips. Auto-hide closes it when it loses focus;\n"
                     "Pinned keeps it open until you press the shortcut again.")
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
        self._icon_map = {}        # {отображаемое имя: имя файла иконки трея}
        self._host = self          # родитель строящихся виджетов (self или скролл-контент)
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
        lbl = QLabel(text, self._host)
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
        self._host = self                     # статическая часть — на самой странице

        # --- заголовок + инфо-кнопка (статично) ------------------------- #
        self._label(tr("Settings"), fonts.font(s(14), "Semibold"),
                    self.TITLE_COLOR, pad, s(12))
        theme = self.settings.get("theme", themes.DEFAULT_THEME)
        ic_info   = themed_icon(theme, "info.png", self._pal["icon"], s(19))
        ic_info_h = themed_icon(theme, "info.png", self._pal["icon_hover"], s(19))
        info_x = self.width_ - pad - s(26)
        self.btn_about = IconButton(self, ic_info, ic_info_h, s(19), self.app.open_about)
        self.btn_about.setGeometry(info_x, s(10), s(26), s(26))

        # --- секция: Download Folder (статично, не скроллится) ---------- #
        sec1_y = s(42)
        self._section_title(tr("Download Folder"), pad, sec1_y)
        card1_y = sec1_y + s(16)
        card1_h = self._build_folder_card(pad, card1_y, card_w)

        # --- всё ниже — в прокручиваемой области (от Processing) -------- #
        area_top = card1_y + card1_h + s(12)
        self._build_scroll_area(area_top, pad, card_w)
        self._host = self

    def _build_scroll_area(self, top, pad, card_w):
        s = self.app._s
        area = QScrollArea(self)
        area.setWidgetResizable(False)
        area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        area.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        area.setFrameShape(QFrame.NoFrame)
        area.viewport().setStyleSheet("background: transparent;")
        area.setStyleSheet(
            "QScrollArea { background: transparent; border: none; }"
            "QScrollBar:vertical { background: transparent; width: 7px; margin: 2px; }"
            f"QScrollBar::handle:vertical {{ background: {self.MUTED_COLOR};"
            "  border-radius: 3px; min-height: 24px; }"
            "QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }"
            "QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }")
        area.setGeometry(0, top, self.width_, self.height_ - top)
        self._scroll_area = area
        self._smooth_scroll = SmoothScroll(area, parent=self)

        content = QWidget()
        content.setStyleSheet("background: transparent;")
        self._host = content

        y = s(2)
        self._section_title(tr("General"), pad, y)
        y += s(18)
        # yt-dlp: слева название, по центру переключатель канала, справа обновление.
        self._build_ytdlp_row(pad, y, card_w)
        y += s(34) + s(12)
        # Tools: слева название, справа обновление ffmpeg + очистка кэша.
        self._build_tools_row(pad, y, card_w)
        y += s(34) + s(18)                    # чуть больше воздуха до clipboard
        # Буфер обмена (+ режим тоста справа).
        self._build_clipboard_row(pad, y, card_w)
        y += s(30) + s(6)
        # Конвертация YouTube-видео.
        self._build_convert_checkbox(pad, y, card_w)
        y += s(30) + s(22)                    # отступ между блоками

        # Spotlight (Ctrl+Shift+D): вкл/выкл + режим скрытия + смена сочетания
        self._section_title(tr("Spotlight"), pad, y)
        y += s(18)
        self._build_spotlight_row(pad, y, card_w)
        y += s(30) + s(10)
        self._build_hotkey_row(pad, y, card_w)
        y += s(34) + s(22)

        # Cookies
        self._section_title(tr("Cookies"), pad, y)
        y += s(20) + self._build_cookies_card(pad, y + s(20), card_w) + s(22)

        # Usage (+ перенесённые из About: иконка трея, тема, язык)
        self._section_title(tr("Usage"), pad, y)
        y += s(24)
        y += self._build_usage_card(pad, y, card_w) + s(14)   # воздух под pinned/auto-hide
        self._build_select_row(tr("Menu Bar Icon"), pad, y, self._icon_values(),
                               self._current_icon_display(), self._on_icon_change,
                               icons=self._icon_icons())
        y += s(34)
        self._build_select_row(tr("Theme"), pad, y, list(THEMES),
                               self.settings.get("theme", THEMES[0]), self._on_theme_change)
        y += s(34)
        self._build_select_row(tr("Language"), pad, y, list(LANGUAGES),
                               self.settings.get("language", DEFAULT_LANGUAGE),
                               self._on_language_change)
        y += s(34) + s(22)                    # отступ между блоками

        # Advanced (внизу): встраивание обложки
        self._section_title(tr("Advanced"), pad, y)
        y += s(18)
        self._build_embed_checkbox(pad, y, card_w)
        y += s(30) + s(30)                    # + увеличенный нижний отступ

        content.resize(self.width_, y)
        area.setWidget(content)

    # --- строки блоков ------------------------------------------------- #
    def _build_ytdlp_row(self, x, y, card_w):
        s = self.app._s
        rh = s(30)
        self._label("yt-dlp", fonts.font(s(12), "Medium"), self.TEXT_COLOR, x, y + s(6))
        # Переключатель канала — справа (старое положение).
        seg_w = s(160)
        seg = SegmentedControl(
            self._host, [("Stable", "stable"), ("Nightly", "nightly")],
            self.settings.get("ytdlp_channel", "stable"), fonts.font(s(11), "Medium"),
            self.SEG_BG, self.SEG_SEL, self.MUTED_COLOR, self.ON_ACCENT, s(9))
        seg.setGeometry(self.width_ - x - seg_w, y, seg_w, rh)
        seg.changed.connect(self.app.set_ytdlp_channel)
        self._ytdlp_seg = seg

    def _build_tools_row(self, x, y, card_w):
        s = self.app._s
        rh, bh, gap = s(30), s(26), s(8)
        self._label(tr("Tools"), fonts.font(s(12), "Medium"), self.TEXT_COLOR, x, y + s(6))
        font = fonts.font(s(11), "Semibold")
        fm = QFontMetrics(font)
        t_yt, t_ff, t_cc = tr("Update yt-dlp"), tr("Update ffmpeg"), tr("Clear Cache")
        bw_yt = max(s(104), fm.horizontalAdvance(t_yt) + s(18))
        bw_ff = max(s(104), fm.horizontalAdvance(t_ff) + s(18))
        bw_cc = max(s(112), fm.horizontalAdvance(t_cc) + s(24),
                    fm.horizontalAdvance(tr("Cache cleared")) + s(24))
        by = y + (rh - bh) // 2
        # Три кнопки в один ряд, выровнены вправо: yt-dlp | ffmpeg | Clear Cache.
        cc_x = self.width_ - x - bw_cc
        ff_x = cc_x - gap - bw_ff
        yt_x = ff_x - gap - bw_yt
        self.btn_update = LinkButton(
            self._host, t_yt, font, self.CHOOSE, self.LINK_HOVER, self.app.start_ytdlp_update,
            hover_bg=self.CHOOSE_BG_H, radius=s(6), base_bg=self.CHOOSE_BG)
        self.btn_update.setGeometry(yt_x, by, bw_yt, bh)
        self.btn_update_ff = LinkButton(
            self._host, t_ff, font, self.CHOOSE, self.LINK_HOVER, self.app.start_ffmpeg_update,
            hover_bg=self.CHOOSE_BG_H, radius=s(6), base_bg=self.CHOOSE_BG)
        self.btn_update_ff.setGeometry(ff_x, by, bw_ff, bh)
        self.btn_clear_cache = LinkButton(
            self._host, t_cc, font, self.CHOOSE, self.LINK_HOVER, self._clear_cache,
            hover_bg=self.CHOOSE_BG_H, radius=s(6), base_bg=self.CHOOSE_BG)
        self.btn_clear_cache.setGeometry(cc_x, by, bw_cc, bh)

    def _build_clipboard_row(self, x, y, card_w):
        s = self.app._s
        rh = s(30)
        seg_w = s(168)
        seg_x = self.width_ - x - seg_w
        cb = CheckBox(self._host, tr("Watch clipboard for links"), fonts.font(s(12), "Regular"),
                      self.TEXT_COLOR, self.CB_OFF, self.CB_ON, s(17), s(5))
        cb.setChecked(bool(self.settings.get("clipboard_watch", False)))
        cb.setGeometry(x, y, seg_x - x - s(8), rh)
        cb.setToolTip(tr(self.CLIPBOARD_TIP))
        cb.toggled.connect(self.app.set_clipboard_watch)
        self._checks["clipboard_watch"] = cb
        seg = SegmentedControl(
            self._host, [(tr("Corner"), "corner"), (tr("At cursor"), "cursor")],
            self.settings.get("toast_position", "corner"), fonts.font(s(11), "Medium"),
            self.SEG_BG, self.SEG_SEL, self.MUTED_COLOR, self.ON_ACCENT, s(9))
        seg.setGeometry(seg_x, y, seg_w, rh)
        seg.changed.connect(self.app.set_toast_position)
        self._toast_seg = seg

    def _build_spotlight_row(self, x, y, card_w):
        s = self.app._s
        rh = s(30)
        seg_w = s(168)
        seg_x = self.width_ - x - seg_w
        cb = CheckBox(self._host, tr("Enable Spotlight"), fonts.font(s(12), "Regular"),
                      self.TEXT_COLOR, self.CB_OFF, self.CB_ON, s(17), s(5))
        cb.setChecked(bool(self.settings.get("spotlight_enabled", True)))
        cb.setGeometry(x, y, seg_x - x - s(8), rh)
        cb.setToolTip(tr(self.SPOTLIGHT_TIP))
        cb.toggled.connect(self.app.set_spotlight_enabled)
        self._checks["spotlight_enabled"] = cb
        # Режим скрытия: Auto-hide (по потере фокуса) | Pinned (пока не нажмёшь снова).
        seg = SegmentedControl(
            self._host, [(tr("Auto-hide"), "focus"), (tr("Pinned"), "manual")],
            self.settings.get("spotlight_dismiss", "focus"), fonts.font(s(11), "Medium"),
            self.SEG_BG, self.SEG_SEL, self.MUTED_COLOR, self.ON_ACCENT, s(9))
        seg.setGeometry(seg_x, y, seg_w, rh)
        seg.changed.connect(self.app.set_spotlight_dismiss)
        self._spotlight_seg = seg

    def _build_hotkey_row(self, x, y, card_w):
        s = self.app._s
        self._label(tr("Shortcut"), fonts.font(s(12), "Medium"), self.TEXT_COLOR,
                    x, y + s(6))
        hk_w = s(180)
        hk = HotkeyEdit(self.app, self.settings.get("spotlight_combo", "ctrl+shift+d"),
                        self._host, self._pal)
        hk.setGeometry(self.width_ - x - hk_w, y, hk_w, s(30))
        hk.changed.connect(self.app.set_spotlight_combo)
        self._hotkey_edit = hk

    def _build_convert_checkbox(self, x, y, card_w):
        s = self.app._s
        cb = CheckBox(self._host, tr("Convert Youtube Videos"), fonts.font(s(12), "Regular"),
                      self.TEXT_COLOR, self.CB_OFF, self.CB_ON, s(17), s(5))
        cb.setChecked(bool(self.settings.get("convert_yt", False)))
        cb.setGeometry(x, y, card_w, s(30))
        cb.setToolTip(tr(self.CONVERT_TIP))
        cb.toggled.connect(lambda v: self._set_flag("convert_yt", v))
        self._checks["convert_yt"] = cb

    def _build_embed_checkbox(self, x, y, card_w):
        s = self.app._s
        cb = CheckBox(self._host, tr("Embed Thumbnail"), fonts.font(s(12), "Regular"),
                      self.TEXT_COLOR, self.CB_OFF, self.CB_ON, s(17), s(5))
        cb.setChecked(bool(self.settings.get("embed_thumbnail", False)))
        cb.setGeometry(x, y, card_w, s(30))
        cb.toggled.connect(lambda v: self._set_flag("embed_thumbnail", v))
        self._checks["embed_thumbnail"] = cb

    def _build_select_row(self, label, x, y, values, current, command, icons=None):
        s = self.app._s
        menu_w = s(140)
        lbl = QLabel(label, self._host)
        lbl.setFont(fonts.font(s(12), "Medium"))
        lbl.setStyleSheet(f"color: {self.TEXT_COLOR}; background: transparent;")
        lbl.move(x, y + s(5))
        lbl.adjustSize()
        combo = Selector(self._host, fonts.font(s(11), "Regular"),
                         self._pal["card_bg"], self._pal["sel_chip"], self.TEXT_COLOR,
                         self._pal["sel_chevron"], s(7), s(22),
                         accent=self._pal["seg_sel"], border=self._pal["border"],
                         on_accent=self._pal["on_accent"])
        for v in values:
            combo.add_item(v, icons.get(v) if icons else None)
        if current in values:
            combo.set_current(current)
        combo.setGeometry(self.width_ - x - menu_w, y, menu_w, s(26))
        combo.changed.connect(command)
        return combo

    # --- перенесено из About: иконка трея / тема / язык ---------------- #
    def _icon_values(self):
        self._icon_map = {}
        names = []
        if os.path.isdir(ICONS_DIR):
            for fname in sorted(os.listdir(ICONS_DIR)):
                stem, ext = os.path.splitext(fname)
                if ext.lower() in (".png", ".ico"):
                    disp = stem.replace("_", " ").title()
                    self._icon_map[disp] = stem
                    names.append(disp)
        if not names:
            self._icon_map["Default"] = ""
            names = ["Default"]
        return names

    def _icon_icons(self):
        from core import tools
        from core.icons import tint_pixmap, raw_pixmap, COLORED_ICONS
        color = "#000000" if tools.windows_uses_light_theme() else "#ffffff"
        result = {}
        for disp, stem in self._icon_map.items():
            if stem:
                path = os.path.join(ICONS_DIR, stem + ".png")
                pm = raw_pixmap(path, 48) if stem in COLORED_ICONS else tint_pixmap(path, color, 48)
                if pm is not None:
                    result[disp] = pm
        return result

    def _current_icon_display(self):
        stem = self.settings.get("tray_icon", "")
        for disp, st in self._icon_map.items():
            if st == stem:
                return disp
        return next(iter(self._icon_map), "Default")

    def _on_icon_change(self, choice):
        stem = self._icon_map.get(choice, "")
        self.settings["tray_icon"] = stem
        if self.app.tray is not None:
            self.app.tray.set_icon(stem)
        self.app.save_settings()

    def _on_theme_change(self, choice):
        if choice == self.settings.get("theme"):
            return
        self.settings["theme"] = choice
        self.app.save_settings()
        self.app.apply_appearance()

    def _on_language_change(self, choice):
        if choice == self.settings.get("language"):
            return
        self.settings["language"] = choice
        i18n.set_language(choice)
        self.app.save_settings()
        self.app.apply_appearance()

    def _build_cookies_card(self, x, y, card_w):
        s = self.app._s
        self._label(tr("Browser for cookies"), fonts.font(s(11), "Regular"),
                    self.TEXT_COLOR, x, y + s(3))
        cur_val = self.settings.get("cookies_browser", "auto")
        cur_label = next((lab for lab, v in _COOKIE_CHOICES if v == cur_val), "Auto")
        self._cookie_val = {lab: v for lab, v in _COOKIE_CHOICES}

        sel = Selector(self._host, fonts.font(s(11), "Regular"),
                       self._pal["field_bg"], self._pal["sel_chip"], self.TEXT_COLOR,
                       self._pal["sel_chevron"], s(7), s(22),
                       accent=self.SEG_SEL, border=self._pal["border"],
                       on_accent=self.ON_ACCENT)
        for lab, _ in _COOKIE_CHOICES:
            sel.add_item(lab)
        sel.set_current(cur_label)
        sel.changed.connect(self._on_cookie_browser_change)
        sel.setGeometry(card_w - s(120), y, s(120) + x, s(26))
        self._cookie_sel = sel
        return s(26)

    def _on_cookie_browser_change(self, label):
        self.settings["cookies_browser"] = self._cookie_val.get(label, "auto")
        self.app.save_settings()

    # ------------------------------------------------------------------ #
    def _section_title(self, text, x, y):
        s = self.app._s
        self._label(text, fonts.font(s(10), "Medium"),
                    self.SECTION_COLOR, x, y)

    def _card(self, x, y, w, h):
        # Подложка карточек убрана (прозрачный контейнер для дочерних виджетов).
        card = QFrame(self._host)
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

    def _clear_cache(self):
        """Очищает cache.json в %APPDATA%/Snatchr и плавно подтверждает на кнопке."""
        from core import cache
        cache.clear()
        if getattr(self, "_cc_flashing", False):
            return
        self._cc_flashing = True
        self._flash_button_text(self.btn_clear_cache,
                                tr("Cache cleared"), tr("Clear Cache"))

    def _flash_button_text(self, btn, temp, restore, hold_ms=1500):
        """Плавно (через прозрачность) меняет текст кнопки на temp, держит и
        возвращает restore."""
        from ui import anim
        from PySide6.QtCore import QTimer

        def fade_to(text, on_done=None):
            def swapped():
                btn.setText(text)
                anim.fade(btn, 0.0, 1.0, 160, on_finished=on_done)
            anim.fade(btn, 1.0, 0.0, 160, on_finished=swapped)

        def finish():
            self._cc_flashing = False
        fade_to(temp, on_done=lambda: QTimer.singleShot(
            hold_ms, lambda: fade_to(restore, on_done=finish)))

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
