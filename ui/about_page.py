import os
import threading
import webbrowser

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFontMetrics, QImage, QPixmap
from PySide6.QtWidgets import QWidget, QLabel, QFrame

from PIL import Image, ImageDraw

from core.constants import (
    APP_NAME, APP_VERSION, ICONS_DIR, THEMES, PROFILE_IMG, DEVELOPER_URL,
    LANGUAGES, DEFAULT_LANGUAGE
)
from core import updater
from core import fonts
from core import i18n
from core import themes
from core.i18n import tr
from ui.widgets import LinkButton, ClickableLabel, Selector, WindowDragMixin


class AboutPage(WindowDragMixin, QWidget):
    """Экран «About»: лого, версия, проверка обновлений, иконка трея, тема."""

    _update_ready = Signal(object)

    def __init__(self, parent, app, settings, width, height):
        super().__init__(parent)
        self.app = app
        self.settings = settings
        self.width_ = width
        self.height_ = height
        self._icon_map = {}
        self._load_theme()
        self.init_window_drag(app)
        self.resize(width, height)

        self._update_ready.connect(self._show_update_result)
        self._build()

    def _load_theme(self):
        p = themes.palette(self.settings.get("theme", themes.DEFAULT_THEME))
        self._pal = p
        self.TITLE_COLOR  = p["title"]
        self.TEXT_COLOR   = p["text"]
        self.MUTED_COLOR  = p["muted"]
        self.ACCENT       = p["accent"]
        self.ACCENT_HOVER = p["accent_hover"]
        self.CARD_BG      = p["card_bg"]
        self.BORDER       = p["border"]
        self.LINK         = p["link"]
        self.LINK_HOVER   = p["link_hover"]
        self.SEL_CHIP     = p["sel_chip"]
        self.SEL_CHEVRON  = p["sel_chevron"]
        self.SEP_LINE     = p["separator"]
        self.ICON_COLOR   = p["icon"]

    # ------------------------------------------------------------------ #
    def _center_label(self, text, font, color, y):
        lbl = QLabel(text, self)
        lbl.setFont(font)
        lbl.setStyleSheet(f"color: {color}; background: transparent;")
        lbl.setAlignment(Qt.AlignHCenter | Qt.AlignTop)
        lbl.setGeometry(0, y, self.width_, QFontMetrics(font).height() + self.app._s(4))
        return lbl

    def _build(self):
        s = self.app._s
        pad = s(16)
        cx = self.width_ // 2

        # 0. About
        self._center_label(tr("About"), fonts.font(s(14), "Semibold"),
                            self.TITLE_COLOR, s(12))

        # 1. Логотип
        d = s(60)
        logo_y = s(38)
        self._logo_pm = self._make_logo_pixmap(d)
        logo = QLabel(self)
        logo.setPixmap(self._logo_pm)
        logo.setAlignment(Qt.AlignCenter)
        logo.setStyleSheet("background: transparent;")
        logo.setGeometry(cx - d // 2, logo_y, d, d)

        # 2. Snatchr
        name_y = logo_y + d + s(6)
        self._center_label(APP_NAME, fonts.font(s(13), "Bold"),
                            self.TITLE_COLOR, name_y)

        # 3. Version (на 5px ниже)
        ver_y = logo_y + d + s(31)
        self._center_label(f"{tr('Version')} {APP_VERSION}", fonts.font(s(10), "Regular"),
                            self.ICON_COLOR, ver_y)

        # 4. Check for Updates (+ статус)
        upd_y = logo_y + d + s(52)
        upd_font = fonts.font(s(11), "Regular")
        # Ширина под самую длинную подпись (на русском «Проверить обновления» /
        # «Обновить и перезапустить» длиннее) — иначе текст обрезается.
        fm = QFontMetrics(upd_font)
        bw = max(fm.horizontalAdvance(tr("Check for Updates")),
                 fm.horizontalAdvance(tr("Update & Restart"))) + s(28)
        self.btn_update = LinkButton(self, tr("Check for Updates"), upd_font,
                                     self.LINK, self.LINK_HOVER, self._check_updates)
        self.btn_update.setGeometry(cx - bw // 2, upd_y, bw, s(26))

        st_y = logo_y + d + s(80)
        self.update_status = self._center_label("", fonts.font(s(11), "Regular"),
                                                self.MUTED_COLOR, st_y)

        # 5. Menu Bar Icon / 6. Theme / 7. Language — сразу под статусом «Check…».
        sep_y       = st_y + s(28)
        icon_row_y  = sep_y + s(14)
        theme_row_y = icon_row_y + s(36)
        lang_row_y  = theme_row_y + s(36)

        # Разделительная линия над «Menu Bar Icon».
        sep = QFrame(self)
        sep.setStyleSheet(f"background-color: {self.SEP_LINE}; border: none;")
        sep.setGeometry(pad, sep_y, self.width_ - 2 * pad, max(1, s(1)))

        self._build_select_row(tr("Menu Bar Icon"), pad, icon_row_y, self._icon_values(),
                               self._current_icon_display(), self._on_icon_change,
                               icons=self._icon_icons())
        self._build_select_row(tr("Theme"), pad, theme_row_y, list(THEMES),
                               self.settings.get("theme", THEMES[0]), self._on_theme_change)
        self._build_select_row(tr("Language"), pad, lang_row_y, list(LANGUAGES),
                               self.settings.get("language", DEFAULT_LANGUAGE),
                               self._on_language_change)

        # 8. «2026 Developed by …» — внизу (почта убрана).
        developed_y = self.height_ - s(30)
        self._build_developed_line(cx, developed_y)

    # ------------------------------------------------------------------ #
    def _build_select_row(self, label, x, y, values, current, command, icons=None):
        s = self.app._s
        menu_w = s(140)

        lbl = QLabel(label, self)
        lbl.setFont(fonts.font(s(12), "Medium"))
        lbl.setStyleSheet(f"color: {self.TEXT_COLOR}; background: transparent;")
        lbl.move(x, y + s(5))
        lbl.adjustSize()

        combo = Selector(self, fonts.font(s(11), "Regular"),
                         self.CARD_BG, self.SEL_CHIP, self.TEXT_COLOR,
                         self.SEL_CHEVRON, s(7), s(22),
                         accent=self._pal["seg_sel"], border=self._pal["border"],
                         on_accent=self._pal["on_accent"])
        for v in values:
            combo.add_item(v, icons.get(v) if icons else None)
        if current in values:
            combo.set_current(current)
        menu_x = self.width_ - x - menu_w
        combo.setGeometry(menu_x, y, menu_w, s(26))
        combo.changed.connect(command)
        return combo

    def _build_developed_line(self, cx, y):
        """«2026 Developed by SmeshidoJoe» — имя кликабельно (ссылка на GitHub)."""
        s = self.app._s
        font = fonts.font(s(10), "Regular")
        fm = QFontMetrics(font)

        prefix_text = tr("2026 Developed by ")
        link_text = "SmeshidoJoe"
        wp = fm.horizontalAdvance(prefix_text)
        wn = fm.horizontalAdvance(link_text)
        h = fm.height() + s(4)
        x0 = cx - (wp + wn) // 2

        prefix = QLabel(prefix_text, self)
        prefix.setFont(font)
        prefix.setStyleSheet(f"color: {self.MUTED_COLOR}; background: transparent;")
        prefix.setGeometry(x0, y, wp + s(2), h)

        link = ClickableLabel(self, link_text, self.LINK, self.LINK_HOVER)
        link.setFont(font)
        link.setGeometry(x0 + wp, y, wn + s(4), h)
        link.clicked.connect(lambda: webbrowser.open(DEVELOPER_URL))

    # ------------------------------------------------------------------ #
    def _make_logo_pixmap(self, d):
        img = self._make_logo(d * 2)
        data = img.convert("RGBA").tobytes("raw", "RGBA")
        qim = QImage(data, img.width, img.height, QImage.Format_RGBA8888).copy()
        pm = QPixmap.fromImage(qim)
        return pm.scaled(d, d, Qt.KeepAspectRatio, Qt.SmoothTransformation)

    def _make_logo(self, d):
        radius = int(d * 0.24)
        bg = Image.new("RGBA", (d, d), (0, 0, 0, 0))
        ImageDraw.Draw(bg).rounded_rectangle([0, 0, d - 1, d - 1], radius=radius,
                                             fill=(51, 68, 93, 255))
        try:
            pic = Image.open(PROFILE_IMG).convert("RGBA").resize((d, d), Image.LANCZOS)
            bg.alpha_composite(pic)
        except (OSError, FileNotFoundError):
            draw = ImageDraw.Draw(bg)
            m = d * 0.34
            draw.polygon([(m, d * 0.30), (m, d * 0.70), (d * 0.74, d * 0.50)],
                         fill=(255, 255, 255, 255))
        mask = Image.new("L", (d, d), 0)
        ImageDraw.Draw(mask).rounded_rectangle([0, 0, d - 1, d - 1], radius=radius, fill=255)
        out = Image.new("RGBA", (d, d), (0, 0, 0, 0))
        out.paste(bg, (0, 0), mask)
        return out

    # --- Menu Bar Icon -------------------------------------------------- #
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
        """{display: путь к png} для отображения иконки в пунктах селектора."""
        result = {}
        for disp, stem in self._icon_map.items():
            if stem:
                path = os.path.join(ICONS_DIR, stem + ".png")
                if os.path.isfile(path):
                    result[disp] = path
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

    # --- Theme ---------------------------------------------------------- #
    def _on_theme_change(self, choice):
        if choice == self.settings.get("theme"):
            return
        self.settings["theme"] = choice
        self.app.save_settings()
        self.app.apply_appearance()      # применяем тему сразу

    # --- Language ------------------------------------------------------- #
    def _on_language_change(self, choice):
        if choice == self.settings.get("language"):
            return
        self.settings["language"] = choice
        i18n.set_language(choice)
        self.app.save_settings()
        self.app.apply_appearance()      # применяем язык сразу

    # --- Updates -------------------------------------------------------- #
    def _check_updates(self):
        self.update_status.setText(tr("Checking…"))
        self.update_status.setStyleSheet(
            f"color: {self.MUTED_COLOR}; background: transparent;")
        threading.Thread(target=self._check_updates_worker, daemon=True).start()

    def _check_updates_worker(self):
        result = updater.check_update()
        self._update_ready.emit(result)

    def _show_update_result(self, result):
        status = result.get("status")
        if status == "available":
            text, color = f"{tr('Update available')}: {result.get('version')}", self._pal["ok"]
            url = result.get("download_url")
            if url:
                self._update_url = url
                self._set_update_action(tr("Update & Restart"), self._start_app_update)
        elif status == "current":
            text, color = tr("You're up to date"), self.MUTED_COLOR
        else:
            text, color = tr("Check failed — try later"), self._pal["error"]
        self.update_status.setText(text)
        self.update_status.setStyleSheet(f"color: {color}; background: transparent;")

    def _set_update_action(self, text, slot):
        """Меняет надпись и действие кнопки обновления (проверка -> скачать)."""
        try:
            self.btn_update.clicked.disconnect()
        except (TypeError, RuntimeError):
            pass
        self.btn_update.setText(text)
        self.btn_update.clicked.connect(slot)

    def _start_app_update(self):
        self.app.start_app_update(getattr(self, "_update_url", None))
