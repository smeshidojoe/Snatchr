"""
Панель выбора элементов плейлиста в Spotlight. Открывается на месте панели
обрезки (та же анимация раскрытия). Показывает список роликов плейлиста с
галочками, шапку (Select/Deselect + счётчик выбрано/всего) и кнопку Download
внизу справа. По Download выбранные ролики уезжают в историю на загрузку.
"""

from PySide6.QtCore import QRectF, Signal
from PySide6.QtGui import QPainter, QColor, QPen, QPixmap, QImage

from PySide6.QtWidgets import QWidget

from core import fonts, themes
from core.i18n import tr
from core.workers import ThumbWorker
from ui.widgets import ScrollList, InfoCardRow, PlaylistHeader, DownloadButton, rounded_pixmap


class PlaylistPanel(QWidget):
    """entries -> список с галочками; сигнал download(list_of_entries)."""

    download = Signal(list)

    def __init__(self, app, parent=None):
        super().__init__(parent)
        self.app = app
        s = app._s
        pal = themes.palette(app.settings.get("theme", themes.DEFAULT_THEME))
        self._pal = pal
        self._bg = QColor(pal["card_bg"])
        self._border = QColor(pal["border"])
        self._rows = []
        self._entries = []
        self._thumb_workers = []
        self._header = None

        self._list = ScrollList(self, pal["field_bg"], pal["muted"])
        self._btn = DownloadButton(self, tr("Download"), fonts.font(s(13), "Semibold"),
                                   pal["download_bg"], pal["download_bg_hover"], s(8),
                                   fg=pal["on_accent"],
                                   disabled_bg=pal["disabled_bg"],
                                   disabled_text=pal["disabled_text"])
        self._btn.clicked.connect(self._on_download_click)
        self._header = None              # закреплённая шапка (вне скролла)

    def target_height(self):
        return self.app._s(300)

    def _header_h(self):
        return self.app._s(30)

    # --- наполнение ---------------------------------------------------- #
    def open_for(self, entries, title):
        s = self.app._s
        pal = self._pal
        self._entries = list(entries)
        self._rows = []
        self._thumb_workers = []
        self._list.clear()

        # Шапка закреплена НАД списком (не в скролле) — тайтл/счётчик/Select-All
        # всегда видны при прокрутке.
        if self._header is not None:
            self._header.setParent(None)
            self._header.deleteLater()
        self._header = PlaylistHeader(
            self, title or tr("Playlist"), len(entries),
            fonts.font(s(11), "Semibold"), fonts.font(s(10), "Medium"),
            fonts.font(s(10), "Regular"),
            pal["title"], pal["title"], pal["muted"], pal["muted"], s(30))
        self._header.toggled.connect(self._on_toggle_all)
        self._header.show()
        self._place_children()

        placeholder = QPixmap(s(80), s(45))
        placeholder.fill(QColor(pal["field_bg"]))
        placeholder = rounded_pixmap(placeholder, s(80), s(45), s(6))
        for e in entries:
            row = InfoCardRow(
                self._list, e.get("title") or tr("Unknown"), e.get("uploader") or "",
                self._fmt_dur(e.get("duration")),
                fonts.font(s(11), "Medium"), fonts.font(s(10), "Regular"),
                fonts.mono(s(10)), pal["title"], pal["text"], pal["muted"],
                s(80), s(45), s(6), s(52),
                with_check=True, cb_colors=(pal["cb_off"], pal["cb_on"]))
            row.set_thumb(placeholder)
            row.cb.toggled.connect(self._on_check_changed)
            self._list.add_row(row)
            self._rows.append(row)
            turl = e.get("thumbnail")
            if turl:
                tw = ThumbWorker(turl, self)
                tw.done.connect(lambda data, r=row: self._set_thumb(r, data))
                tw.start()
                self._thumb_workers.append(tw)
        self._update_count()

    def _set_thumb(self, row, data):
        if not data:
            return
        try:
            img = QImage.fromData(data)
            if img.isNull():
                return
            row.set_thumb(rounded_pixmap(QPixmap.fromImage(img), row._tw, row._th, row._r))
        except RuntimeError:
            pass

    def _on_toggle_all(self):
        target = not all(r.is_checked() for r in self._rows) if self._rows else True
        for r in self._rows:
            r.cb.blockSignals(True)
            r.set_checked(target, animate=True)
            r.cb.blockSignals(False)
        self._update_count()

    def _on_check_changed(self):
        self._update_count()

    def _update_count(self):
        sel = sum(1 for r in self._rows if r.is_checked())
        if self._header is not None:
            self._header.set_state(sel, len(self._rows))
        self._btn.setEnabled(sel > 0)

    def selected_entries(self):
        return [self._entries[i] for i, r in enumerate(self._rows) if r.is_checked()]

    def _on_download_click(self):
        sel = self.selected_entries()
        if sel:
            self.download.emit(sel)

    # --- геометрия / фон ----------------------------------------------- #
    def resizeEvent(self, event):
        self._place_children()

    def _place_children(self):
        s = self.app._s
        pad = s(10)
        w, h = self.width(), self.height()
        btn_w, btn_h = s(150), s(38)
        hh = self._header_h() if self._header is not None else 0
        if self._header is not None:
            self._header.setGeometry(pad, pad, w - 2 * pad, hh)
            self._header.raise_()
        list_top = pad + (hh + s(6) if hh else 0)
        list_h = max(s(40), h - list_top - btn_h - s(10) - pad)
        self._list.setGeometry(pad, list_top, w - 2 * pad, list_h)
        self._btn.setGeometry(w - pad - btn_w, h - pad - btn_h, btn_w, btn_h)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        s = self.app._s
        w, h = self.width(), self.height()
        p.setPen(QPen(self._border, 1))
        p.setBrush(self._bg)
        p.drawRoundedRect(QRectF(0.5, 0.5, w - 1, h - 1), s(18), s(18))
        p.end()

    @staticmethod
    def _fmt_dur(secs):
        if not secs:
            return "--:--"
        secs = int(secs)
        h, rem = divmod(secs, 3600)
        m, ss = divmod(rem, 60)
        return f"{h}:{m:02d}:{ss:02d}" if h else f"{m:02d}:{ss:02d}"
