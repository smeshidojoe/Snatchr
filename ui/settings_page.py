import os

from PySide6.QtCore import Qt, QRectF, Signal, QTimer
from PySide6.QtGui import QFontMetrics, QColor, QPainter, QPen, QKeySequence
from PySide6.QtWidgets import QWidget, QLabel, QFrame, QFileDialog, QScrollArea

from core import fonts, themes, i18n
from core.constants import ICONS_DIR, THEMES, LANGUAGES, DEFAULT_LANGUAGE
from core.i18n import tr
from core.icons import themed_icon
from ui.widgets import (
    IconButton, LinkButton, CheckBox, SegmentedControl, Selector, WindowDragMixin,
    SmoothScroll, ThemedOwner, Switch, SettingsGroup,
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

    # F13–F24 на обычной клавиатуре отсутствуют — их шлют макропады и
    # программируемые клавиатуры. Такие клавиши разрешаем назначать БЕЗ
    # модификатора: отобрать чужое сочетание нечем (нажать их случайно тоже),
    # а нажатие одной кнопкой — ровно то, ради чего макропад и держат.
    _PAD_KEYS = frozenset(getattr(Qt, "Key_F%d" % n) for n in range(13, 25))

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

    def apply_theme(self, pal):
        """Живая смена темы."""
        self._bg = QColor(pal["sel_chip"])
        self._border = QColor(pal["border"])
        self._text = QColor(pal["text"])
        self._accent = QColor(pal["seg_sel"])
        self._muted = QColor(pal["muted"])
        self.update()

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
        if not parts and key not in self._PAD_KEYS:
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


class SettingsPage(ThemedOwner, WindowDragMixin, QWidget):
    """Экран настроек: папка загрузок, обработка, куки, режим работы."""

    THEME_ATTRS = {
        "CARD_BG": "card_bg",
        "TITLE_COLOR": "title",
        "SECTION_COLOR": "icon",
        "TEXT_COLOR": "text",
        "MUTED_COLOR": "muted",
        "ACCENT": "accent",
        "ACCENT_HOVER": "accent_hover",
        "BORDER": "border",
        "LINK": "link",
        "LINK_HOVER": "link_hover",
        "CHOOSE": "choose",
        "CHOOSE_BG": "choose_bg",
        "CHOOSE_BG_H": "choose_bg_h",
        "CB_OFF": "cb_off",
        "CB_ON": "cb_on",
        "SEG_BG": "seg_bg",
        "SEG_SEL": "seg_sel",
        "ON_ACCENT": "on_accent",
    }


    CONVERT_TIP = ("Videos above 1080p come in a codec that many editors\n"
                   "and messengers can't open. Convert them to H.264 mp4\n"
                   "so they work everywhere. Uses the GPU when available,\n"
                   "with a CPU fallback.")
    CLIPBOARD_TIP = ("When you copy a link from a supported site, a toast\n"
                     "appears offering to download it in the background.")
    AUTOPASTE_TIP = ("When you open the window, a freshly copied link is\n"
                     "pasted into the field automatically — for the sites\n"
                     "you tick below.")
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
        self._labels = []          # (QLabel, ключ палитры) — для смены темы
        self._sep_lines = []       # разделительные линии
        self._icon_map = {}        # {отображаемое имя: имя файла иконки трея}
        self._host = self          # родитель строящихся виджетов (self или скролл-контент)
        self._load_theme()
        self.init_window_drag(app)
        self.resize(width, height)
        self._build()


    def rebuild(self):
        """Пересобирает страницу НА МЕСТЕ (смена языка).

        Раскладка настроек считается прямо в _build_* и заново не проигрывается,
        поэтому после смены языка виджеты надо создать заново. Страницу при этом
        не удаляем и не пересоздаём: сохраняются геометрия, родитель и порядок
        наложения, а окно не мигает — всё идёт под setUpdatesEnabled(False) на окне.

        Пересобираем ОБЯЗАТЕЛЬНО у скрытой страницы: виджет, созданный у уже
        видимого родителя, получает от Qt флаг WA_WState_Hidden, и снять его
        может только явный show() — показ родителя такого ребёнка не покажет.
        При старте страница строится скрытой, здесь это повторяем.
        """
        try:
            scroll_pos = self._scroll_area.verticalScrollBar().value()
        except (AttributeError, RuntimeError):
            scroll_pos = 0
        win = self.window()
        was_shown = not self.isHidden()
        win.setUpdatesEnabled(False)
        try:
            if was_shown:
                self.hide()
            for ch in list(self.children()):
                if isinstance(ch, QWidget):
                    ch.setParent(None)
                    ch.deleteLater()
            self._checks = {}
            self._labels = []
            self._sep_lines = []
            self._icon_map = {}
            self._themed_labels = []
            self._themed_widgets = []
            self._scroll_area = None
            self._host = self
            self._load_theme()
            self._build()
            try:
                self._scroll_area.verticalScrollBar().setValue(scroll_pos)
            except (AttributeError, RuntimeError):
                pass
            if was_shown:
                self.show()
                self.raise_()
        finally:
            win.setUpdatesEnabled(True)
        self.update()

    def _on_theme_applied(self, pal):
        """Живая смена темы: подписи, разделители, стили и иконки."""
        self._load_theme()
        self._themed_labels = self._labels
        self.recolor_labels(self._pal)
        self._labels = self._themed_labels
        for ln in self._sep_lines:
            try:
                ln.setStyleSheet("background: %s; border: none;"
                                 % self._pal["separator"])
            except RuntimeError:
                pass
        if getattr(self, "_scroll_area", None) is not None:
            self._style_scroll_area(self._scroll_area)
        for hk in self.findChildren(HotkeyEdit):
            hk.apply_theme(self._pal)
        # Превью иконок трея тонируются по СИСТЕМНОЙ теме Windows, а не по теме
        # приложения, — их при смене темы трогать не нужно.

    def _load_theme(self):
        """Цвета текущей темы в атрибуты (таблица THEME_ATTRS)."""
        self.load_theme_attrs()
    # ------------------------------------------------------------------ #
    def _style_scroll_area(self, area):
        """Стиль полосы прокрутки (цвет ручки — из палитры)."""
        area.setStyleSheet(
            "QScrollArea { background: transparent; border: none; }"
            "QScrollBar:vertical { background: transparent; width: 7px; margin: 2px; }"
            f"QScrollBar::handle:vertical {{ background: {self.MUTED_COLOR};"
            "  border-radius: 3px; min-height: 24px; }"
            "QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }"
            "QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }")

    def _label(self, text, font, color, x, y, w=None, h=None, key=None):
        """Подпись, участвующая в живой смене темы.

        key — ключ палитры, ОБЯЗАТЕЛЕН. Раньше он подбирался по значению цвета
        (искали атрибут с таким же значением), и это тихо ломалось: в теме цвета
        разных ключей совпадают — в Blackout `title` и `text` оба #e0e0e0, в
        Dark Pulse восемь ключей делят #f5d020. Подбор брал первый попавшийся, и
        при переходе на тему, где эти цвета разные, подпись красилась не тем.
        """
        assert key, "у подписи должен быть явный ключ палитры: %r" % (text,)
        lbl = QLabel(text, self._host)
        lbl.setFont(font)
        lbl.setStyleSheet(f"color: {color}; background: transparent;")
        self._labels.append((lbl, key))
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
                    self.TITLE_COLOR, pad, s(12), key="title")
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
        self._style_scroll_area(area)
        area.setGeometry(0, top, self.width_, self.height_ - top)
        self._scroll_area = area
        self._smooth_scroll = SmoothScroll(area, parent=self)

        content = QWidget()
        content.setStyleSheet("background: transparent;")
        self._host = content

        # Каждый блок — карточка-группа (как в Scribe/Clipr): подпись слева,
        # элемент управления справа, строки разделены волосяной линией.
        gap = s(18)                           # воздух между группами
        y = s(2)
        y = self._group(tr("Authentification (For Restricted Videos)"), pad, y, card_w, [
            self._row_cookies,
        ]) + gap
        y = self._group(tr("General"), pad, y, card_w, [
            self._switch_row("convert_yt", tr("Convert Youtube Videos"), False,
                             lambda v: self._set_flag("convert_yt", v), self.CONVERT_TIP),
            self._switch_row("embed_thumbnail", tr("Embed Thumbnail"), False,
                             lambda v: self._set_flag("embed_thumbnail", v)),
            self._switch_row("clipboard_watch", tr("Watch clipboard for links"), False,
                             self.app.set_clipboard_watch, self.CLIPBOARD_TIP),
            self._row_toast_position,
            self._switch_row("toast_copy_file", tr("Copy downloaded file to clipboard"),
                             True, self.app.set_toast_copy_file),
            self._switch_row("autopaste", tr("Paste link on open"), False,
                             lambda v: self._set_flag("autopaste", v), self.AUTOPASTE_TIP),
            self._row_autopaste_sites,
            self._row_parallel,
            self._row_speed_limit,
        ]) + gap
        y = self._group(tr("Hotkey Starts Download"), pad, y, card_w, [
            self._switch_row("hk_download_enabled", tr("Enable Feature"), False,
                             self.app.set_hk_download_enabled, self.HK_DOWNLOAD_TIP),
            self._switch_row("hk_download_notify", tr("Show notification"), True,
                             self.app.set_hk_download_notify, self.HK_NOTIFY_TIP),
            self._hotkey_row(tr("Download Video"), "hk_download_video", "ctrl+alt+v",
                             self.app.set_hk_download_video),
            self._hotkey_row(tr("Download Audio"), "hk_download_audio", "ctrl+alt+a",
                             self.app.set_hk_download_audio),
        ]) + gap
        y = self._group(tr("Spotlight"), pad, y, card_w, [
            self._switch_row("spotlight_enabled", tr("Enable Spotlight"), True,
                             self.app.set_spotlight_enabled, self.SPOTLIGHT_TIP),
            self._row_spotlight_dismiss,
            self._row_spotlight_shortcut,
        ]) + gap
        y = self._group(tr("Format Priority"), pad, y, card_w, [
            self._row_format_priority,
        ]) + gap
        interface_rows = [
            self._switch_row("allow_dragging", tr("Allow Dragging"), False,
                             self._on_drag_change, self.DRAG_TIP),
            self._row_window_mode,
            self._select_row(tr("Menu Bar Icon"), self._icon_values(),
                             self._current_icon_display(), self._on_icon_change,
                             icons=self._icon_icons()),
            self._select_row(tr("Theme"), list(THEMES),
                             self.settings.get("theme", THEMES[0]), self._on_theme_change),
            # Живое стекло — только у прозрачной темы Frosted (см. ui/glass.py).
            self._switch_row("live_glass", tr("Live Glass"), False,
                             self.app.set_live_glass, self.LIVE_GLASS_TIP),
            self._select_row(tr("Language"), list(LANGUAGES),
                             self.settings.get("language", DEFAULT_LANGUAGE),
                             self._on_language_change),
        ]
        y = self._group(tr("Interface"), pad, y, card_w, interface_rows) + gap
        y = self._group(tr("System"), pad, y, card_w, [
            self._switch_row("update_notify", tr("Notify about updates"), True,
                             self.app.set_update_notify),
            self._switch_row("autostart", tr("Launch at startup"), False,
                             self.app.set_autostart),
        ]) + gap
        y = self._group(tr("Repair"), pad, y, card_w, [
            self._row_ytdlp,
            self._row_tools,
        ]) + s(16)
        # Open Logs Folder + Reset Settings — в самом низу, под Repair.
        self._build_bottom_buttons(pad, y, card_w)
        y += s(32) + s(30)                    # + увеличенный нижний отступ

        content.resize(self.width_, y)
        area.setWidget(content)

    LIVE_GLASS_TIP = ("Glass follows what is behind the window in real time.\n"
                      "While on, the window is hidden from screenshots and\n"
                      "screen recordings (Windows cannot capture it).")

    # --- группы и строки ------------------------------------------------ #
    def _row_h(self):
        return self.app._s(40)

    def _group(self, title, x, y, w, rows):
        """Карточка-группа: заголовок над ней, строки внутри.

        rows — функции f(rx, ry, rw) -> высота строки: rx/rw — поле строки уже
        с внутренним отступом карточки. Возвращает y под карточкой."""
        s = self.app._s
        if title:
            self._section_title(title, x + s(4), y)
            y += s(18)
        ip = s(12)
        group = self.themed(SettingsGroup(
            self._host, self._pal["card_bg"], self._pal["separator"],
            self._pal["border"], s(10), ip),
            bg_color="card_bg", line_color="separator", border_color="border")
        cy = 0
        for i, build in enumerate(rows):
            if i:
                group.add_line(cy)
            cy += build(x + ip, y + cy, w - 2 * ip)
        group.setGeometry(x, y, w, cy)
        group.lower()                         # под строками
        return y + cy

    def _row_label(self, text, x, y, h, tip=None):
        s = self.app._s
        lbl = self._label(text, fonts.font(s(12), "Regular"), self.TEXT_COLOR,
                          x, y, key="text")
        lbl.move(x, y + (h - lbl.height()) // 2)
        if tip:
            lbl.setToolTip(tr(tip))
        return lbl

    def _switch_row(self, key, text, default, on_toggle, tip=None):
        def build(x, y, w):
            s = self.app._s
            h = self._row_h()
            sw = self.themed(Switch(self._host, text, fonts.font(s(12), "Regular"),
                                    self.TEXT_COLOR, self.CB_OFF, self.CB_ON, s(20)),
                             text_color="text", off_color="cb_off", on_color="cb_on")
            sw.setChecked(bool(self.settings.get(key, default)))
            sw.setGeometry(x, y, w, h)
            if tip:
                sw.setToolTip(tr(tip))
            sw.toggled.connect(on_toggle)
            self._checks[key] = sw
            return h
        return build

    def _seg(self, options, current, min_w):
        s = self.app._s
        seg = self.themed(SegmentedControl(
            self._host, options, current, fonts.font(s(11), "Medium"),
            self.SEG_BG, self.SEG_SEL, self.MUTED_COLOR, self.ON_ACCENT, s(9),
            edge_color=self._pal["field_edge"]),
            bg_color="seg_bg", sel_color="seg_sel",
            text_color="muted", sel_text_color="on_accent", edge_color="field_edge")
        return seg, seg.fit_width(min_w)

    def _seg_row(self, label, options, current, on_change, min_w, tip=None):
        """Строка: подпись слева, сегменты справа. Возвращает (build, holder)."""
        holder = {}

        def build(x, y, w):
            s = self.app._s
            h = self._row_h()
            self._row_label(label, x, y, h, tip)
            seg, sw = self._seg(options, current, min_w)
            sh = s(28)
            seg.setGeometry(x + w - sw, y + (h - sh) // 2, sw, sh)
            if tip:
                seg.setToolTip(tr(tip))
            seg.changed.connect(on_change)
            holder["seg"] = seg
            return h
        return build, holder

    def _row_toast_position(self, x, y, w):
        build, holder = self._seg_row(
            tr("Toast Position"), [(tr("Corner"), "corner"), (tr("At cursor"), "cursor")],
            self.settings.get("toast_position", "corner"), self.app.set_toast_position,
            self.app._s(168))
        h = build(x, y, w)
        self._toast_seg = holder["seg"]
        return h

    def _row_spotlight_dismiss(self, x, y, w):
        # Режим скрытия: Auto-hide (по потере фокуса) | Pinned (пока не нажмёшь снова).
        build, holder = self._seg_row(
            tr("Hide Mode"), [(tr("Auto-hide"), "focus"), (tr("Pinned"), "manual")],
            self.settings.get("spotlight_dismiss", "focus"), self.app.set_spotlight_dismiss,
            self.app._s(168), self.SPOTLIGHT_TIP)
        h = build(x, y, w)
        self._spotlight_seg = holder["seg"]
        return h

    def _row_window_mode(self, x, y, w):
        build, holder = self._seg_row(
            tr("Window Mode"), [(tr("Pinned"), "toggle"), (tr("Auto-hide"), "focus")],
            self.settings.get("usage_mode", "toggle"), self._on_usage_change,
            self.app._s(160), self.USAGE_TIP)
        h = build(x, y, w)
        self._usage_seg = holder["seg"]
        return h

    def _row_ytdlp(self, x, y, w):
        build, holder = self._seg_row(
            "yt-dlp", [("Stable", "stable"), ("Nightly", "nightly")],
            self.settings.get("ytdlp_channel", "stable"), self.app.set_ytdlp_channel,
            self.app._s(160))
        h = build(x, y, w)
        self._ytdlp_seg = holder["seg"]
        return h

    def _hotkey_row(self, title, key, default, on_change):
        def build(x, y, w):
            s = self.app._s
            h = self._row_h()
            self._row_label(title, x, y, h)
            hk_w, hk_h = s(180), s(28)
            hk = HotkeyEdit(self.app, self.settings.get(key, default), self._host, self._pal)
            hk.setGeometry(x + w - hk_w, y + (h - hk_h) // 2, hk_w, hk_h)
            hk.changed.connect(on_change)
            return h
        return build

    def _row_spotlight_shortcut(self, x, y, w):
        s = self.app._s
        h = self._row_h()
        self._row_label(tr("Shortcut"), x, y, h)
        hk_w, hk_h = s(180), s(28)
        hk = HotkeyEdit(self.app, self.settings.get("spotlight_combo", "ctrl+shift+d"),
                        self._host, self._pal)
        hk.setGeometry(x + w - hk_w, y + (h - hk_h) // 2, hk_w, hk_h)
        hk.changed.connect(self.app.set_spotlight_combo)
        self._hotkey_edit = hk
        return h

    def _select_row(self, label, values, current, command, icons=None, field_bg="card_bg"):
        def build(x, y, w):
            s = self.app._s
            h = self._row_h()
            self._row_label(label, x, y, h)
            menu_w, mh = s(140), s(26)
            combo = self.themed(Selector(self._host, fonts.font(s(11), "Regular"),
                                         self._pal[field_bg], self._pal["sel_chip"],
                                         self.TEXT_COLOR, self._pal["sel_chevron"], s(7), s(22),
                                         accent=self._pal["seg_sel"], border=self._pal["border"],
                                         on_accent=self._pal["on_accent"],
                                         edge=self._pal["field_edge"],
                                         popup_bg=self._pal["popup_bg"]),
                                field_bg=field_bg, chip_bg="sel_chip", text_color="text",
                                chevron_color="sel_chevron", accent="seg_sel", border="border",
                                on_accent="on_accent", edge="field_edge", popup_bg="popup_bg")
            for v in values:
                combo.add_item(v, icons.get(v) if icons else None)
            if current in values:
                combo.set_current(current)
            combo.setGeometry(x + w - menu_w, y + (h - mh) // 2, menu_w, mh)
            combo.changed.connect(command)
            return h
        return build

    def _row_cookies(self, x, y, w):
        cur_val = self.settings.get("cookies_browser", "auto")
        cur_label = next((lab for lab, v in _COOKIE_CHOICES if v == cur_val), "Auto")
        self._cookie_val = {lab: v for lab, v in _COOKIE_CHOICES}
        return self._select_row(tr("Browser for cookies"),
                                [lab for lab, _ in _COOKIE_CHOICES], cur_label,
                                self._on_cookie_browser_change,
                                field_bg="field_bg")(x, y, w)

    def _row_parallel(self, x, y, w):
        cur = str(self.settings.get("parallel_downloads", 2))
        return self._select_row(tr("Parallel Downloads"), ["1", "2", "3"], cur,
                                lambda v: self.app.set_parallel_downloads(int(v)))(x, y, w)

    def _speed_label(self, mbps):
        return tr("Unlimited") if not mbps else "%d MB/s" % mbps

    def _row_speed_limit(self, x, y, w):
        from core.downloader import SPEED_LIMITS_MBPS
        self._speed_by_label = {self._speed_label(m): m for m in SPEED_LIMITS_MBPS}
        values = [self._speed_label(m) for m in SPEED_LIMITS_MBPS]
        cur = self._speed_label(int(self.settings.get("speed_limit_mbps", 0) or 0))
        return self._select_row(tr("Download Speed Limit"), values, cur,
                                self._on_speed_limit_change)(x, y, w)

    def _on_speed_limit_change(self, label):
        self.settings["speed_limit_mbps"] = self._speed_by_label.get(label, 0)
        self.app.save_settings()

    def _row_format_priority(self, x, y, w):
        """Подпись слева, кнопка Edit справа (отдельная страница)."""
        s = self.app._s
        h = self._row_h()
        self._row_label(tr("Show/Hide and reorder formats"), x, y, h)
        btn_w, btn_h = s(76), s(28)
        self.btn_formats = self.themed(LinkButton(
            self._host, tr("Edit"), fonts.font(s(11), "Semibold"),
            self.CHOOSE, self.LINK_HOVER, self.app.open_formats,
            hover_bg=self.CHOOSE_BG_H, radius=s(6), base_bg=self.CHOOSE_BG),
            color="choose", hover_color="link_hover",
            hover_bg="choose_bg_h", base_bg="choose_bg")
        self.btn_formats.setGeometry(x + w - btn_w, y + (h - btn_h) // 2, btn_w, btn_h)
        return h

    def _row_tools(self, x, y, w):
        s = self.app._s
        h = self._row_h()
        bh, gap = s(26), s(8)
        self._row_label(tr("Tools"), x, y, h)
        font = fonts.font(s(11), "Semibold")
        fm = QFontMetrics(font)
        t_yt, t_ff, t_cc = tr("Update yt-dlp"), tr("Update ffmpeg"), tr("Clear Cache")
        bw_yt = max(s(96), fm.horizontalAdvance(t_yt) + s(18))
        bw_ff = max(s(96), fm.horizontalAdvance(t_ff) + s(18))
        bw_cc = max(s(100), fm.horizontalAdvance(t_cc) + s(24),
                    fm.horizontalAdvance(tr("Cache cleared")) + s(24))
        by = y + (h - bh) // 2
        # Три кнопки в один ряд, выровнены вправо: yt-dlp | ffmpeg | Clear Cache.
        cc_x = x + w - bw_cc
        ff_x = cc_x - gap - bw_ff
        yt_x = ff_x - gap - bw_yt
        self.btn_update = self.themed(LinkButton(
            self._host, t_yt, font, self.CHOOSE, self.LINK_HOVER,
            lambda: self.app.start_ytdlp_update(
                lambda ok, err: self._tool_update_done(self.btn_update, t_yt, ok)),
            hover_bg=self.CHOOSE_BG_H, radius=s(6), base_bg=self.CHOOSE_BG),
            color="choose", hover_color="link_hover",
            hover_bg="choose_bg_h", base_bg="choose_bg")
        self.btn_update.setGeometry(yt_x, by, bw_yt, bh)
        self.btn_update_ff = self.themed(LinkButton(
            self._host, t_ff, font, self.CHOOSE, self.LINK_HOVER,
            lambda: self.app.start_ffmpeg_update(
                lambda ok, err: self._tool_update_done(self.btn_update_ff, t_ff, ok)),
            hover_bg=self.CHOOSE_BG_H, radius=s(6), base_bg=self.CHOOSE_BG),
            color="choose", hover_color="link_hover",
            hover_bg="choose_bg_h", base_bg="choose_bg")
        self.btn_update_ff.setGeometry(ff_x, by, bw_ff, bh)
        self.btn_clear_cache = self.themed(LinkButton(
            self._host, t_cc, font, self.CHOOSE, self.LINK_HOVER, self._clear_cache,
            hover_bg=self.CHOOSE_BG_H, radius=s(6), base_bg=self.CHOOSE_BG),
            color="choose", hover_color="link_hover",
            hover_bg="choose_bg_h", base_bg="choose_bg")
        self.btn_clear_cache.setGeometry(cc_x, by, bw_cc, bh)
        return h

    # --- автовставка ссылки при открытии окна --------------------------- #
    _AUTOPASTE_SITES = [("youtube", "Youtube"), ("instagram", "Insta"),
                        ("tiktok", "Tiktok"), ("reddit", "Reddit"),
                        ("twitter", "Twitter"), ("vk", "VK"),
                        ("soundcloud", "SoundCloud")]

    def _row_autopaste_sites(self, x, y, w):
        """Сетка сайтов для автовставки (2 колонки) + Select/Deselect All в
        свободной ячейке последнего ряда. Здесь остаются галочки: это выбор
        нескольких пунктов из списка, а не включение режима."""
        s = self.app._s
        from core import downloader
        enabled = set(self.settings.get("autopaste_sites", downloader.AUTOPASTE_SITES))
        self._site_checks = {}
        cols, rh = 2, s(28)
        top = s(8)
        col_w = w // cols
        for i, (key, label) in enumerate(self._AUTOPASTE_SITES):
            r, c = divmod(i, cols)
            scb = self.themed(
                CheckBox(self._host, label, fonts.font(s(11), "Regular"),
                         self.TEXT_COLOR, self.CB_OFF, self.CB_ON, s(16), s(5)),
                text_color="text", off_color="cb_off", on_color="cb_on")
            scb.setChecked(key in enabled)
            scb.setGeometry(x + c * col_w, y + top + r * rh, col_w - s(6), rh)
            scb.toggled.connect(lambda v, k=key: self._on_site_toggle(k, v))
            self._site_checks[key] = scb
        n = len(self._AUTOPASTE_SITES)
        rows = (n + cols - 1) // cols
        sd_w, sd_h = s(96), s(24)
        last_r, last_c = divmod(n, cols)       # первая свободная ячейка
        if last_c == 0:                         # сетка заполнена — кнопка ниже
            last_r, rows = rows, rows + 1
        sd = self.themed(LinkButton(
            self._host, tr("Deselect All"), fonts.font(s(10), "Semibold"),
            self.CHOOSE, self.LINK_HOVER, self._toggle_all_sites,
            hover_bg=self.CHOOSE_BG_H, radius=s(6), base_bg=self.CHOOSE_BG),
            color="choose", hover_color="link_hover",
            hover_bg="choose_bg_h", base_bg="choose_bg")
        sd.setGeometry(x + w - sd_w, y + top + last_r * rh + (rh - sd_h) // 2, sd_w, sd_h)
        self._sites_toggle_btn = sd
        self._sync_sites_toggle_label()
        return top + rows * rh + s(8)


    HK_DOWNLOAD_TIP = ("Download the link from the clipboard by pressing a\n"
                       "shortcut — no window opens. What is downloaded\n"
                       "(video or audio) depends on the shortcut pressed;\n"
                       "progress is shown by the spinning tray icon.")


    HK_NOTIFY_TIP = ("Show a short «Download Started» plate in the corner\n"
                     "when a shortcut fires. Without it the only sign that\n"
                     "the download began is the spinning tray icon.")


    def _build_bottom_buttons(self, x, y, card_w):
        """Open Logs Folder + Reset Settings — две кнопки одинаковой ширины в один
        ряд, с равными отступами от краёв блока и друг от друга (три равных зазора)."""
        s = self.app._s
        font = fonts.font(s(11), "Semibold")
        bh = s(32)
        gap = s(12)
        bw = (card_w - 3 * gap) // 2
        logs_x = x + gap
        reset_x = x + 2 * gap + bw
        self.btn_logs = self.themed(LinkButton(
            self._host, tr("Open Logs Folder"), font, self.CHOOSE, self.LINK_HOVER,
            self.app.open_logs_folder, hover_bg=self.CHOOSE_BG_H, radius=s(6),
            base_bg=self.CHOOSE_BG), color="choose", hover_color="link_hover",
            hover_bg="choose_bg_h", base_bg="choose_bg")
        self.btn_logs.setGeometry(logs_x, y, bw, bh)
        self._reset_armed = False
        self.btn_reset = self.themed(LinkButton(
            self._host, tr("Reset Settings"), font, self._pal["error"], self.LINK_HOVER,
            self._on_reset_click, hover_bg=self.CHOOSE_BG_H, radius=s(6),
            base_bg=self.CHOOSE_BG),
            color="error", hover_color="link_hover",
            hover_bg="choose_bg_h", base_bg="choose_bg")
        self.btn_reset.setGeometry(reset_x, y, bw, bh)

    def _on_reset_click(self):
        # Двухступенчатое подтверждение, как в спотлайте: первый клик взводит
        # («Confirm»), второй в течение пары секунд — выполняет сброс.
        if not self._reset_armed:
            self._reset_armed = True
            self.btn_reset.setText(tr("Confirm"))
            QTimer.singleShot(2600, self._disarm_reset)
            return
        self._reset_armed = False
        self.app.reset_and_restart()

    def _disarm_reset(self):
        if not self._reset_armed:
            return
        self._reset_armed = False
        try:
            self.btn_reset.setText(tr("Reset Settings"))
        except RuntimeError:
            pass


    def _on_site_toggle(self, key, value):
        from core import downloader
        sites = set(self.settings.get("autopaste_sites", list(downloader.AUTOPASTE_SITES)))
        sites.add(key) if value else sites.discard(key)
        # Сохраняем в каноническом порядке.
        self.settings["autopaste_sites"] = [k for k in downloader.AUTOPASTE_SITES
                                            if k in sites]
        self.app.save_settings()
        self._sync_sites_toggle_label()

    def _toggle_all_sites(self):
        target = not all(cb.isChecked() for cb in self._site_checks.values())
        for cb in self._site_checks.values():
            cb.setCheckedAnimated(target)     # toggled -> _on_site_toggle сохранит
        self._sync_sites_toggle_label()

    def _sync_sites_toggle_label(self):
        all_on = all(cb.isChecked() for cb in self._site_checks.values())
        self._sites_toggle_btn.setText(tr("Deselect All") if all_on else tr("Select All"))


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
        # Тему применяем живьём — перекрашиваем существующие виджеты, без
        # пересборки страниц и без кадра-заглушки поверх окна.
        self.app.apply_theme_live()

    def _on_language_change(self, choice):
        if choice == self.settings.get("language"):
            return
        self.settings["language"] = choice
        i18n.set_language(choice)
        self.app.save_settings()
        # Как и у темы — живьём, без пересборки окна и кадра-заглушки.
        # Отложено на следующий тик: нельзя пересобирать страницу прямо внутри
        # сигнала её же селектора.
        QTimer.singleShot(0, self.app.apply_language_live)


    def _on_cookie_browser_change(self, label):
        self.settings["cookies_browser"] = self._cookie_val.get(label, "auto")
        self.app.save_settings()


    # ------------------------------------------------------------------ #
    def _section_title(self, text, x, y):
        s = self.app._s
        self._label(text, fonts.font(s(10), "Medium"),
                    self.SECTION_COLOR, x, y, key="icon")

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
        # Шрифт моноширинный, поэтому подпись создаётся вручную, а не через
        # _label(). Регистрируем её в списке перекраски отдельно: без этого она
        # держала цвет той темы, на которой была построена страница, и после
        # возврата на светлую тему оставалась белой (страница живёт всю сессию,
        # так что не помогало и закрытие окна — только перезапуск программы).
        self.path_lbl = QLabel(self._short_path(self.settings["download_path"]), card)
        self.path_lbl.setFont(fonts.mono(s(11)))
        self.path_lbl.setStyleSheet(f"color: {self.TEXT_COLOR}; background: transparent;")
        self.path_lbl.setGeometry(s(4), card_h // 2 - s(9), card_w - s(90), s(18))
        self._labels.append((self.path_lbl, "text"))

        browse = self.themed(LinkButton(
            card, tr("Choose"), fonts.font(s(10), "Semibold"),
            self.CHOOSE, self.CHOOSE, self._choose_folder,
            hover_bg=self.CHOOSE_BG_H, radius=s(6),
            base_bg=self.CHOOSE_BG, press_pop=True),
            color="choose", hover_color="choose",
            hover_bg="choose_bg_h", base_bg="choose_bg")
        browse.setGeometry(card_w - s(64) - s(8), card_h // 2 - s(12), s(64), s(24))

        return card_h


    def _tool_update_done(self, btn, restore, ok):
        """Итог обновления инструмента — прямо на кнопке, которую нажали.
        Успех не подтверждаем: там и так виден оверлей с полосой. Молчали только
        о сбое, и это было хуже всего — обновление «проходило», а бинарь
        оставался старым."""
        if ok:
            return
        try:
            self._flash_button_text(btn, tr("Update failed"), restore, hold_ms=2600)
        except RuntimeError:
            pass                          # страницу пересобрали — кнопки уже нет

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
                anim.fade(btn, 0.0, 1.0, anim.ENTER_MS, on_finished=on_done)
            anim.fade(btn, 1.0, 0.0, anim.EXIT_MS, on_finished=swapped)

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
