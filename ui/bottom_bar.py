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
        self._mode_anim = False        # идёт анимация перехода main<->settings
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
        iz = self.app._s(22)
        self.ic_about      = themed_icon(theme, "info.png",       self._icon, iz)
        self.ic_about_h    = themed_icon(theme, "info.png",       self._icon_hover, iz)

    def _build(self):
        s = self.app._s

        self.btn_settings = IconButton(
            self.app, self.ic_settings, self.ic_settings_h,
            s(24), self._on_left
        )
        self.btn_settings.resize(s(32), s(32))

        # Оригинальная центральная папка — оставлена, но СКРЫТА: держит старое
        # положение на случай, если раскладку захочется вернуть. Рабочая — копия
        # btn_folder2 в новой раскладке (между settings и about).
        self.btn_folder = IconButton(
            self.app, self.ic_folder, self.ic_folder_h,
            s(24), self._open_folder
        )
        self.btn_folder.resize(s(32), s(32))

        self.btn_folder2 = IconButton(
            self.app, self.ic_folder, self.ic_folder_h,
            s(24), self._open_folder
        )
        self.btn_folder2.resize(s(32), s(32))

        self.btn_about = IconButton(
            self.app, self.ic_about, self.ic_about_h,
            s(18), self._open_about
        )
        self.btn_about.resize(s(28), s(28))

        self.btn_exit = LinkButton(self.app, tr("Exit"), fonts.font(s(11), "Regular"),
                                   self._icon, self._icon_hover, self._exit_app)
        self.btn_exit.resize(s(48), s(32))

        # Кнопки привязаны к окну, а не к панели, поэтому показываем их явно —
        # set_page_mode дальше скроет лишние.
        for b in (self.btn_settings, self.btn_folder2, self.btn_about, self.btn_exit):
            b.show()
        self.btn_folder.hide()          # оригинал спрятан (см. выше)

    def apply_theme(self, pal):
        """Живая смена темы: перетонировать иконки и обновить цвета кнопок."""
        self._load_icons()               # перечитывает палитру и тонирует заново
        # Шестерёнка/стрелка зависит от текущей страницы — ставим ту же пару.
        if self._mode == "main":
            self.btn_settings.set_icons(self.ic_settings, self.ic_settings_h)
        else:
            self.btn_settings.set_icons(self.ic_back, self.ic_back_h)
        self.btn_folder.set_icons(self.ic_folder, self.ic_folder_h)
        self.btn_folder2.set_icons(self.ic_folder, self.ic_folder_h)
        self.btn_about.set_icons(self.ic_about, self.ic_about_h)
        self.btn_exit.set_colors(color=self._icon, hover_color=self._icon_hover)
        self.update()

    def retranslate(self):
        """Смена языка: единственная надпись панели — «Выход»."""
        self.btn_exit.setText(tr("Exit"))
        self.reposition()

    def _on_left(self):
        self.app.on_left_button()

    def _open_about(self):
        self.app.open_about()

    # --- геометрия ------------------------------------------------------ #
    def _bar_y_at(self, h):
        return h - self.app._s(48)

    def _btn_y_at(self, h):
        return self._bar_y_at(h) + self.app._s(8)

    def _about_y_at(self, h):
        return self._bar_y_at(h) + (self.app._s(48) - self.btn_about.height()) // 2

    def _bar_y(self):
        return self._bar_y_at(self.app.WIN_H)

    def _btn_y(self):
        return self._btn_y_at(self.app.WIN_H)

    # Страницы, на которых папка стоит по центру (место скрытого оригинала):
    # Settings и вложенный в него Format Priority — переход между ними не должен
    # дёргать кнопку из центра в ряд.
    _CENTERED = ("settings", "formats")

    def _folder2_x(self, page):
        """X папки: на Settings/Format Priority — центр (место скрытого
        оригинала), иначе — своя позиция в ряду main."""
        s = self.app._s
        if page in self._CENTERED:
            return self.app.WIN_W // 2 - s(16)
        left_c = s(12) + s(16)
        right_c = (self.app.WIN_W - s(60)) + s(24)
        step = (right_c - left_c) / 3.0
        return int(left_c + step - self.btn_folder2.width() / 2)

    def _about_x(self):
        s = self.app._s
        left_c = s(12) + s(16)
        right_c = (self.app.WIN_W - s(60)) + s(24)
        step = (right_c - left_c) / 3.0
        return int(left_c + 2 * step - self.btn_about.width() / 2)

    def _about_y(self):
        return self._about_y_at(self.app.WIN_H)

    def reposition(self):
        """Начальная раскладка (и после resize) — без анимации, по текущему mode.
        settings и exit на прежних местах, folder2 в ряду (или в центре в
        настройках), about виден только на главной."""
        s = self.app._s
        self.setGeometry(0, self._bar_y(), self.app.WIN_W, s(48))
        btn_y = self._btn_y()
        self.btn_settings.move(s(12), btn_y)
        self.btn_exit.move(self.app.WIN_W - s(60), btn_y)
        self.btn_folder.move(self.app.WIN_W // 2 - s(16), btn_y)   # старый центр (скрыт)
        # Во время перехода main<->settings folder2/about ведут собственные
        # анимации (X + динамический Y) — их здесь не трогаем, иначе перебьём.
        # settings/exit/folder(скрыт) уже сдвинуты по Y выше — этого достаточно.
        if self._mode_anim:
            self.btn_settings.raise_()
            return
        self.btn_folder2.move(self._folder2_x(self._mode), btn_y)
        self.btn_about.move(self._about_x(), self._about_y())
        self.btn_about.setVisible(self._mode == "main")
        self.btn_folder2.setVisible(self._mode != "about")
        self.btn_exit.setVisible(self._mode != "about")
        self.btn_settings.raise_()
        if self._mode != "about":
            self.btn_folder2.raise_()
            self.btn_about.raise_()
            self.btn_exit.raise_()

    def set_page_mode(self, page, target_h=None, animate=None):
        """
        main     — шестерёнка + папка(в ряду) + about + Exit
        settings — стрелка + папка(едет в центр) + Exit (about уезжает вниз)
        formats  — как settings (папка остаётся в центре, не дёргается)
        about    — только стрелка назад

        target_h — целевая высота окна (окно анимируется параллельно). Нужна,
        чтобы папка/about ехали к ФИНАЛЬНОМУ Y по прямой (одна OutCubic с окном),
        а не ломаной траекторией из-за динамического Y во время роста окна.

        animate=False — расставить мгновенно. Нужно после пересборки панели
        (смена темы/языка): новая панель рождается в режиме main, и без этого
        папка каждый раз заново ехала из ряда в центр.
        """
        prev, self._mode = self._mode, page
        self.btn_settings.set_icons(*(
            (self.ic_settings, self.ic_settings_h) if page == "main"
            else (self.ic_back, self.ic_back_h)))
        if animate is None:
            animate = self.app.isVisible() and {prev, page} <= {"main", "settings"}
        h = target_h if target_h is not None else self.app.WIN_H

        if page == "about":
            self._mode_anim = False
            self._set_visible(self.btn_folder2, False)
            self._set_visible(self.btn_about, False)
            self._set_visible(self.btn_exit, False)
        else:
            if animate:
                self._mode_anim = True
            self._set_visible(self.btn_exit, True)
            # Папка едет между рядной позицией (main) и центром (settings),
            # по прямой к финальному Y (той же OutCubic/длительностью, что окно).
            self._move_to(self.btn_folder2, self._folder2_x(page),
                          self._btn_y_at(h), animate, on_done=self._end_mode_anim)
            self._set_visible(self.btn_folder2, True)
            # About есть только на главной: уезжает вниз+гаснет при уходе в настройки.
            self._about_transition(page == "main", animate, h)
        self.btn_settings.raise_()

    def _end_mode_anim(self):
        self._mode_anim = False
        self.reposition()

    def _about_transition(self, show, animate, target_h):
        """About появляется/уходит: slide по вертикали (16px) + fade к финальному
        Y (при целевой высоте окна). Резкий старт — плавный конец (OutCubic)."""
        b = self.btn_about
        off = self.app._s(16)
        base_y = self._about_y_at(target_h)
        if show:
            b.move(self._about_x(), base_y + (off if animate else 0))
            b.setVisible(True)
            b.raise_()
            if animate:
                # t: 1->0 — приезжает снизу вверх, проявляясь.
                anim.animate(b, 1.0, 0.0, anim.WIN_RESIZE_MS,
                             lambda t: b.move(self._about_x(),
                                              int(base_y + off * t)),
                             easing=anim.WIN_RESIZE_EASING, attr="_about_move_anim")
                anim.fade(b, 0.0, 1.0, anim.WIN_RESIZE_MS)
        else:
            if not b.isVisible():
                return
            if animate:
                # t: 0->1 — уезжает вниз, гаснет.
                sy = b.y()
                anim.animate(b, 0.0, 1.0, anim.WIN_RESIZE_MS,
                             lambda t: b.move(self._about_x(),
                                              int(sy + off * t)),
                             easing=anim.WIN_RESIZE_EASING, attr="_about_move_anim")
                anim.fade(b, 1.0, 0.0, anim.PAGE_FADE_OUT_MS, on_finished=b.hide)
            else:
                b.hide()

    def _set_visible(self, btn, visible, animate=None):
        if animate is None:
            animate = self.app.isVisible()
        if visible:
            if not btn.isVisible():
                btn.show()
                btn.raise_()
                if animate:
                    anim.fade(btn, 0.0, 1.0, 180)
        elif btn.isVisible():
            if animate:
                anim.fade(btn, 1.0, 0.0, 160, on_finished=btn.hide)
            else:
                btn.hide()

    def _move_to(self, btn, tx, ty, animate, on_done=None):
        """Плавно двигает кнопку по прямой к (tx, ty). Длительность/кривая
        совпадают с анимацией окна (anim.WIN_RESIZE_*) — синхронная диагональ."""
        if not animate:
            btn.move(tx, ty)
            if on_done:
                on_done()
            return
        sx, sy = btn.x(), btn.y()
        anim.animate(btn, 0.0, 1.0, anim.WIN_RESIZE_MS,
                     lambda t: btn.move(int(sx + (tx - sx) * t),
                                        int(sy + (ty - sy) * t)),
                     easing=anim.WIN_RESIZE_EASING,
                     on_finished=on_done, attr="_bar_move_anim")

    def _open_folder(self):
        path = self.settings.get("download_path", "")
        if path and os.path.exists(path):
            subprocess.Popen(f'explorer "{path}"')
        else:
            subprocess.Popen(f'explorer "{os.path.expanduser("~")}"')

    def _exit_app(self):
        from PySide6.QtWidgets import QApplication
        QApplication.instance().quit()
