import math
import os
import threading
import webbrowser

from PySide6.QtCore import Qt, Signal, QRectF, QEasingCurve
from PySide6.QtGui import (QFontMetrics, QImage, QPixmap, QPainter, QColor, QPen,
                           QPainterPath)
from PySide6.QtWidgets import QWidget, QLabel

from core.constants import (APP_NAME, APP_VERSION, PROFILE_IMG, DEVELOPER_URL,
                            DONATE_BUTTONS, DONATE_ICONS_DIR)
from core import updater
from core import fonts
from core import themes
from core.i18n import tr
from ui.widgets import (LinkButton, ClickableLabel, WindowDragMixin,
                        Rethemable, ThemedOwner)
from ui import anim


class DonateButton(Rethemable, QWidget):
    """Кнопка поддержки: скруглённая, фирменный цвет фона, текст (+ опц. иконка
    из assets/donate/<name>.png). Клик открывает url (пустой url — no-op).

    Виджет чуть больше самой пилюли на PAD с каждой стороны — это запас под
    перелёт масштаба в анимации нажатия (иначе края обрезались)."""


    MAX_SCALE = 1.03            # пик перелёта в _scale_of
    _COLOR_ATTRS = {"accent": ("_accent", True)}

    @classmethod
    def pad_for(cls, pill_w, pill_h):
        """Запас с каждой стороны, чтобы пилюля на пике масштаба не обрезалась."""
        return int(math.ceil(max(pill_w, pill_h) * (cls.MAX_SCALE - 1) / 2.0)) + 1

    def __init__(self, app, label, bg, fg, url, icon_name, pad=0, parent=None):
        super().__init__(parent)
        self.app = app
        self.PAD = pad
        self._label = label
        self._bg = QColor(bg)
        self._fg = QColor(fg)
        self._url = url or ""
        self._hover = False
        s = app._s
        self._font = fonts.font(s(12), "Semibold")
        # Иконка (если PNG есть) — иначе только текст.
        self._icon = None
        path = os.path.join(DONATE_ICONS_DIR, (icon_name or "") + ".png")
        if icon_name and os.path.isfile(path):
            pm = QPixmap(path)
            if not pm.isNull():
                self._icon = pm.scaledToHeight(s(22), Qt.SmoothTransformation)
        # Белую кнопку (CloudTips) обводим — иначе сливается с фоном окна.
        self._border = QColor("#d8dee8") if bg.lower() == "#ffffff" else None
        # Левый сегмент под иконкой — акцентом текущей темы: кнопка читается как
        # бейдж и не теряется на светлом фоне окна.
        pal = themes.palette(app.settings.get("theme", themes.DEFAULT_THEME))
        self._accent = QColor(pal["accent"])
        self._press_p = 1.0            # прогресс анимации нажатия (1 = покой)
        self.setCursor(Qt.PointingHandCursor)

    def enterEvent(self, e):
        self._hover = True
        self.update()

    def leaveEvent(self, e):
        self._hover = False
        self.update()

    def mouseReleaseEvent(self, e):
        if (e.button() == Qt.LeftButton
                and self.rect().contains(e.position().toPoint())):
            # Отклик даём даже с пустой ссылкой-заглушкой: клик должен читаться.
            self._press_bounce()
            if self._url:
                webbrowser.open(self._url)

    def _press_bounce(self):
        """Отклик на нажатие: сжатие -> лёгкий перелёт -> возврат (как у
        переключателей в настройках и кнопок истории)."""
        def tick(v):
            self._press_p = v
            self.update()

        def fin():
            self._press_p = 1.0
            self.update()

        anim.animate(self, 0.0, 1.0, 240, tick,
                     easing=QEasingCurve.Linear, on_finished=fin,
                     attr="_press_anim")

    @staticmethod
    def _scale_of(p):
        # Кнопка широкая, поэтому амплитуда мягче, чем у маленьких иконок.
        if p <= 0.30:
            return 1.0 + (0.96 - 1.0) * (p / 0.30)
        if p <= 0.70:
            return 0.96 + (1.03 - 0.96) * ((p - 0.30) / 0.40)
        return 1.03 + (1.0 - 1.03) * ((p - 0.70) / 0.30)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        s = self.app._s
        w, h = self.width(), self.height()
        if self._press_p < 1.0:
            k = self._scale_of(self._press_p)
            p.translate(w / 2.0, h / 2.0)
            p.scale(k, k)
            p.translate(-w / 2.0, -h / 2.0)
        bg = self._bg.lighter(108) if self._hover else self._bg
        # Поля под перелёт масштаба при нажатии (1.03): без них края пилюли
        # обрезались границами виджета.
        m = self.PAD
        body = QRectF(m + 0.5, m + 0.5, w - 2 * m - 1, h - 2 * m - 1)
        r = body.height() / 2.0

        # Содержимое (иконка + текст) центрируем в ОСТАВШЕЙСЯ части пилюли —
        # правее акцентного сегмента, иначе текст уезжает под него.
        fm = QFontMetrics(self._font)
        tw = fm.horizontalAdvance(self._label)
        iw = self._icon.width() if self._icon else 0
        seg_w = (iw + s(20)) if self._icon else 0.0     # ширина акцентной «шапки»

        p.setPen(Qt.NoPen)
        p.setBrush(bg)
        p.drawRoundedRect(body, r, r)

        if seg_w:
            # Сегмент: скруглён слева по форме пилюли, справа обрезан ровно.
            p.save()
            clip = QPainterPath()
            clip.addRoundedRect(body, r, r)
            p.setClipPath(clip)
            acc = self._accent.lighter(112) if self._hover else self._accent
            p.setBrush(acc)
            p.drawRect(QRectF(body.left(), body.top(), seg_w, body.height()))
            p.restore()
            p.drawPixmap(int(body.left() + (seg_w - iw) / 2),
                         int((h - self._icon.height()) / 2), self._icon)

        # Обводка — поверх заливок, иначе сегмент перекрывает её слева.
        if self._border:
            p.setPen(QPen(self._border, 1))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(body, r, r)

        p.setFont(self._font)
        p.setPen(self._fg)
        seg_right = body.left() + seg_w
        text_x = (seg_right + (body.right() - seg_right - tw) / 2.0 if seg_w
                  else body.left() + (body.width() - tw) / 2.0)
        p.drawText(QRectF(text_x, 0, tw + s(4), h),
                   Qt.AlignLeft | Qt.AlignVCenter, self._label)
        p.end()


class AboutPage(ThemedOwner, WindowDragMixin, QWidget):
    """Экран «About»: лого, версия, проверка обновлений, иконка трея, тема."""

    THEME_ATTRS = {
        "TITLE_COLOR": "title",
        "TEXT_COLOR": "text",
        "MUTED_COLOR": "muted",
        "ACCENT": "accent",
        "ACCENT_HOVER": "accent_hover",
        "CARD_BG": "card_bg",
        "BORDER": "border",
        "LINK": "link",
        "LINK_HOVER": "link_hover",
        "SEL_CHIP": "sel_chip",
        "SEL_CHEVRON": "sel_chevron",
        "SEP_LINE": "separator",
        "ICON_COLOR": "icon",
    }


    _update_ready = Signal(object)

    def __init__(self, parent, app, settings, width, height):
        super().__init__(parent)
        self.app = app
        self.settings = settings
        self.width_ = width
        self.height_ = height
        self._icon_map = {}
        self._themed_labels = []   # (QLabel, ключ палитры)
        self._donate_btns = []
        self._load_theme()
        self.init_window_drag(app)
        self.resize(width, height)

        self._update_ready.connect(self._show_update_result)
        self._build()

    def apply_theme(self, pal):
        """Живая смена темы: перечитать палитру и разослать цвета детям."""
        self._load_theme()
        for lbl, key in self._themed_labels:
            try:
                lbl.setStyleSheet("color: %s; background: transparent;" % self._pal[key])
            except RuntimeError:
                pass                   # подпись уже удалена
        self.btn_update.set_colors(color=self.LINK, hover_color=self.LINK_HOVER)
        self._dev_link.set_colors(color=self.LINK, hover_color=self.LINK_HOVER)
        for b in self._donate_btns:
            b.set_colors(accent=self._pal["accent"])
        self.update()

    def _load_theme(self):
        """Цвета текущей темы в атрибуты (таблица THEME_ATTRS)."""
        self.load_theme_attrs()
    # ------------------------------------------------------------------ #
    def _center_label(self, text, font, color, y, key=None):
        lbl = QLabel(text, self)
        if key:                       # запомнить для живой смены темы
            self._themed_labels.append((lbl, key))
        lbl.setFont(font)
        lbl.setStyleSheet(f"color: {color}; background: transparent;")
        lbl.setAlignment(Qt.AlignHCenter | Qt.AlignTop)
        lbl.setGeometry(0, y, self.width_, QFontMetrics(font).height() + self.app._s(4))
        return lbl

    def _build(self):
        # About стал компактнее (иконка/тема/язык переехали в Настройки), поэтому
        # оставшиеся элементы (от заголовка до «Check for Updates») крупнее.
        s = self.app._s
        cx = self.width_ // 2

        # 0. About
        self._center_label(tr("About"), fonts.font(s(16), "Semibold"),
                            self.TITLE_COLOR, s(16), key="title")

        # 1. Логотип
        d = s(88)
        logo_y = s(54)
        self._logo_pm = self._make_logo_pixmap(d)
        logo = QLabel(self)
        logo.setPixmap(self._logo_pm)
        logo.setAlignment(Qt.AlignCenter)
        logo.setStyleSheet("background: transparent;")
        logo.setGeometry(cx - d // 2, logo_y, d, d)

        # 2. Snatchr
        name_y = logo_y + d + s(12)
        self._center_label(APP_NAME, fonts.font(s(17), "Bold"),
                            self.TITLE_COLOR, name_y, key="title")

        # 3. Version
        ver_y = logo_y + d + s(42)
        self._center_label(f"{tr('Version')} {APP_VERSION}", fonts.font(s(12), "Regular"),
                            self.ICON_COLOR, ver_y, key="icon")

        # 4. Check for Updates (+ статус)
        upd_y = logo_y + d + s(72)
        upd_font = fonts.font(s(13), "Regular")
        # Ширина под самую длинную подпись (на русском «Проверить обновления» /
        # «Обновить и перезапустить» длиннее) — иначе текст обрезается.
        fm = QFontMetrics(upd_font)
        bw = max(fm.horizontalAdvance(tr("Check for Updates")),
                 fm.horizontalAdvance(tr("Update & Restart"))) + s(32)
        self.btn_update = LinkButton(self, tr("Check for Updates"), upd_font,
                                     self.LINK, self.LINK_HOVER, self._check_updates)
        self.btn_update.setGeometry(cx - bw // 2, upd_y, bw, s(30))

        st_y = logo_y + d + s(106)
        self.update_status = self._center_label("", fonts.font(s(12), "Regular"),
                                                self.MUTED_COLOR, st_y, key="muted")

        # 5. Кнопки поддержки — по одной на строку, по центру.
        self._build_donate_buttons(cx, st_y + s(30))

        # 6. «2026 Developed by …» — внизу (почта убрана).
        developed_y = self.height_ - s(30)
        self._build_developed_line(cx, developed_y)

    # ------------------------------------------------------------------ #
    def _build_donate_buttons(self, cx, y):
        s = self.app._s
        bw, bh, gap = s(178), s(38), s(10)      # пилюля покороче — меньше «воздуха» по краям
        pad = DonateButton.pad_for(bw, bh)      # запас под перелёт масштаба
        for key, label, bg, fg, url, icon in DONATE_BUTTONS:
            btn = DonateButton(self.app, tr(label), bg, fg, url, icon, pad, self)
            # Виджет больше пилюли на pad с каждой стороны; видимый размер — bw×bh.
            btn.setGeometry(cx - bw // 2 - pad, y - pad, bw + 2 * pad, bh + 2 * pad)
            self._donate_btns.append(btn)
            y += bh + gap
        self._donate_bottom = y - gap       # низ последней кнопки

    # ------------------------------------------------------------------ #
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
        self._themed_labels.append((prefix, "muted"))
        prefix.setGeometry(x0, y, wp + s(2), h)

        link = ClickableLabel(self, link_text, self.LINK, self.LINK_HOVER)
        link.setFont(font)
        link.setGeometry(x0 + wp, y, wn + s(4), h)
        link.clicked.connect(lambda: webbrowser.open(DEVELOPER_URL))
        self._dev_link = link

    # ------------------------------------------------------------------ #
    def _make_logo_pixmap(self, d):
        img = self._make_logo(d * 2)
        data = img.convert("RGBA").tobytes("raw", "RGBA")
        qim = QImage(data, img.width, img.height, QImage.Format_RGBA8888).copy()
        pm = QPixmap.fromImage(qim)
        return pm.scaled(d, d, Qt.KeepAspectRatio, Qt.SmoothTransformation)

    def _make_logo(self, d):
        radius = int(d * 0.24)
        from PIL import Image, ImageDraw   # Pillow тяжёлый (~60 мс) и нужен
                                          # только здесь — не тянем его на старте
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
                self._set_update_action(tr("Download Update"), self._start_app_update)
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
