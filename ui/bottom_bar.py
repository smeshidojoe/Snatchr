import os
import subprocess

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget

from core import fonts, themes
from core.i18n import tr
from core.icons import themed_icon
from ui.widgets import IconButton, LinkButton
from ui import anim


class BottomBar(QWidget):
    def __init__(self, parent, app, settings, width=460, height=48):
        super().__init__(parent)
        self.app = app
        self.settings = settings
        self._mode = "main"
        self.setAttribute(Qt.WA_TranslucentBackground, True)

        self._load_icons()
        self._build()

    def _load_icons(self):
        theme = self.settings.get("theme", themes.DEFAULT_THEME)
        p = themes.palette(theme)
        self._icon = p["icon"]
        self._icon_hover = p["icon_hover"]
        gz = self.app._s(24)   # шестерёнка/стрелка — крупнее
        fz = self.app._s(24)   # иконка папки — крупнее
        self.ic_settings   = themed_icon(theme, "settings.png",   self._icon, gz)
        self.ic_settings_h = themed_icon(theme, "settings.png",   self._icon_hover, gz)
        self.ic_back       = themed_icon(theme, "back-black.png", self._icon, gz)
        self.ic_back_h     = themed_icon(theme, "back-black.png", self._icon_hover, gz)
        self.ic_folder     = themed_icon(theme, "folder.png",     self._icon, fz)
        self.ic_folder_h   = themed_icon(theme, "folder.png",     self._icon_hover, fz)

    def _build(self):
        s = self.app._s

        self.btn_settings = IconButton(
            self.app, self.ic_settings, self.ic_settings_h,
            s(24), self._on_left
        )
        self.btn_settings.resize(s(32), s(32))

        self.btn_folder = IconButton(
            self.app, self.ic_folder, self.ic_folder_h,
            s(24), self._open_folder
        )
        self.btn_folder.resize(s(32), s(32))

        self.btn_exit = LinkButton(self.app, tr("Exit"), fonts.font(s(11), "Regular"),
                                   self._icon, self._icon_hover, self._exit_app)
        self.btn_exit.resize(s(48), s(32))

        # Кнопки привязаны к окну: если оно уже видимо (пересоздание панели при
        # смене темы/языка), новые виджеты надо показать явно — иначе панель
        # окажется пустой. set_page_mode дальше сам скроет лишние кнопки.
        for b in (self.btn_settings, self.btn_folder, self.btn_exit):
            b.show()

    def teardown(self):
        """Удаляет кнопки панели (они привязаны к окну, а не к самой панели,
        поэтому при пересоздании панели их нужно убрать вручную)."""
        for w in (self.btn_settings, self.btn_folder, self.btn_exit):
            w.setParent(None)
            w.deleteLater()

    def _on_left(self):
        self.app.on_left_button()

    def reposition(self):
        """Пересчитать позиции кнопок (после смены высоты окна)."""
        s = self.app._s
        bar_y = self.app.WIN_H - s(48)
        self.setGeometry(0, bar_y, self.app.WIN_W, s(48))

        btn_y = bar_y + s(8)
        self.btn_settings.move(s(12), btn_y)
        self.btn_folder.move(self.app.WIN_W // 2 - s(16), btn_y)
        self.btn_exit.move(self.app.WIN_W - s(60), btn_y)

        self.btn_settings.raise_()
        if self._mode != "about":
            self.btn_folder.raise_()
            self.btn_exit.raise_()

    def set_page_mode(self, page):
        """
        main     — шестерёнка + папка + Exit
        settings — стрелка назад + папка + Exit
        about    — только стрелка назад
        """
        self._mode = page
        # Шестерёнка/стрелка переключаются мгновенно (без фейда).
        if page == "main":
            self.btn_settings.set_icons(self.ic_settings, self.ic_settings_h)
        else:
            self.btn_settings.set_icons(self.ic_back, self.ic_back_h)

        # Папка и Exit — с фейдом (когда окно видимо).
        self._set_aux_visible(page != "about")
        self.btn_settings.raise_()

    def _set_aux_visible(self, visible):
        animate = self.app.isVisible()
        for btn in (self.btn_folder, self.btn_exit):
            if visible:
                if not btn.isVisible():
                    btn.show()
                    btn.raise_()
                    if animate:
                        anim.fade(btn, 0.0, 1.0, 180)
            else:
                if btn.isVisible():
                    if animate:
                        anim.fade(btn, 1.0, 0.0, 160, on_finished=btn.hide)
                    else:
                        btn.hide()

    def _open_folder(self):
        path = self.settings.get("download_path", "")
        if path and os.path.exists(path):
            subprocess.Popen(f'explorer "{path}"')
        else:
            subprocess.Popen(f'explorer "{os.path.expanduser("~")}"')

    def _exit_app(self):
        from PySide6.QtWidgets import QApplication
        QApplication.instance().quit()
