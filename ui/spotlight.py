"""
Окно Spotlight (Ctrl+Shift+D): строка вставки ссылки + история скачиваний +
панель обрезки. Вставил ссылку → Enter / 600мс дебаунс → фоновая загрузка
(Best Quality, как Paste). Готовый ролик наезжает в список сверху.

Неподдерживаемая ссылка — красная подсветка краёв; успех — зелёная.
"""

import os
import uuid

from PySide6.QtCore import (
    Qt, QRectF, QTimer, QEvent, QEasingCurve, QPoint, QPropertyAnimation
)
from PySide6.QtGui import (
    QPainter, QColor, QPen, QPixmap, QGuiApplication, QCursor, QKeyEvent,
    QLinearGradient
)
from PySide6.QtWidgets import QWidget, QLineEdit, QLabel, QApplication

from core import fonts, themes, downloader, history
from core.i18n import tr
from ui import anim
from ui.widgets import SegmentedControl
from ui.spotlight_history import HistoryList
from ui.spotlight_trim import TrimPanel
from ui.spotlight_playlist import PlaylistPanel
from ui.download_scheduler import DownloadScheduler


# ------------------------------------------------------------------ #
class SearchField(QWidget):
    """Скруглённая «пилюля» ввода с подсветкой краёв. Прогресс теперь у каждого
    файла в истории, поэтому поле всегда доступно для новых ссылок."""

    def __init__(self, app, on_submit, on_debounce, parent=None):
        super().__init__(parent)
        self.app = app
        s = app._s
        pal = themes.palette(app.settings.get("theme", themes.DEFAULT_THEME))
        self._bg = QColor(pal["field_bg"])
        self._border = QColor(pal["border"])
        self._radius = s(18)
        self._glow = QColor(0, 0, 0, 0)     # анимируемая подсветка краёв
        self._glow_t = 0.0

        self._edit = QLineEdit(self)
        self._edit.setPlaceholderText(tr("Paste URL Here"))
        self._edit.setFrame(False)
        self._edit.setStyleSheet(
            f"QLineEdit {{ background: transparent; border: none; color: {pal['title']};"
            f" selection-background-color: {pal['accent']}; }}")
        self._edit.setFont(fonts.font(s(16), "Regular"))
        self._edit.returnPressed.connect(lambda: on_submit(self._edit.text()))
        self._deb = QTimer(self)
        self._deb.setSingleShot(True)
        self._deb.setInterval(600)
        self._deb.timeout.connect(lambda: on_debounce(self._edit.text()))
        self._edit.textEdited.connect(lambda _t: self._deb.start())

    def edit(self):
        return self._edit

    def resizeEvent(self, event):
        s = self.app._s
        pad = s(24)
        self._edit.setGeometry(pad, 0, self.width() - 2 * pad, self.height())

    # --- подсветка краёв ---------------------------------------------- #
    def flash(self, color):
        self._glow = QColor(color)
        anim.animate(self, 1.0, 0.0, 900, self._set_glow_t,
                     easing=QEasingCurve.OutCubic, attr="_glow_anim")

    def _set_glow_t(self, v):
        self._glow_t = float(v)
        self.update()

    # --- отрисовка ----------------------------------------------------- #
    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        s = self.app._s
        w, h = self.width(), self.height()
        p.setPen(QPen(self._border, 1))
        grad = QLinearGradient(0, 0, 0, h)      # свой вертикальный градиент поля
        grad.setColorAt(0.0, self._bg.lighter(105))
        grad.setColorAt(1.0, self._bg.darker(107))
        p.setBrush(grad)
        p.drawRoundedRect(QRectF(0.5, 0.5, w - 1, h - 1), self._radius, self._radius)

        # подсветка краёв (красная — неподдерживаемая ссылка), затухающая
        if self._glow_t > 0.01:
            gc = QColor(self._glow)
            gc.setAlphaF(min(1.0, self._glow_t))
            pen = QPen(gc, max(2.0, s(2.4)))
            p.setPen(pen)
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(QRectF(1.2, 1.2, w - 2.4, h - 2.4),
                              self._radius, self._radius)
        p.end()


# ------------------------------------------------------------------ #
class Spotlight(QWidget):
    """Верхнеуровневое окно Spotlight (одно на приложение)."""

    def __init__(self, app):
        super().__init__(None, Qt.FramelessWindowHint | Qt.Tool
                         | Qt.WindowStaysOnTopHint | Qt.NoDropShadowWindowHint)
        self.app = app
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        s = app._s

        self.MX = s(8)
        self.MY = s(8)
        self.CW = s(624)                    # ширина контента
        self.TOGGLE_H = s(34)               # переключатель Video/Audio сверху (в воздухе)
        self.TOGGLE_TOP = s(6)              # опущен чуть ниже верхнего края
        self.GAP = s(12)
        self.TOGGLE_GAP = self.GAP          # зазор переключатель↔поле = поле↔история
        self.SEARCH_H = s(60)
        self.HIST_H = s(330)
        self._mode = "video"                # режим загрузки для ссылок из Spotlight

        self._dl_source = "spotlight"  # тег источника для зеркала загрузок
        self._dls = {}                 # id -> {row,frac,convert,url,copy,announce,managed,thumb}
        self._mirrors = {}             # id -> row (зеркала загрузок из окна)
        self._sched = DownloadScheduler(app, self)   # общий планировщик (лимит parallel)
        self._sched.progress.connect(self._on_progress)
        self._sched.finished.connect(self._on_done)
        self._sched.failed.connect(self._on_failed)
        self._trim_open = False
        self._trim_h = 0
        self._pl_open = False          # открыта ли панель выбора плейлиста
        self._pl_h = 0
        self._suppress_hide = False
        self._menu = None
        self._closing = False          # идёт анимация исчезновения окна
        self._tray_ring_on = False     # показываем ли кольцо в трее (скрытый спотлайт)

        pal0 = themes.palette(app.settings.get("theme", themes.DEFAULT_THEME))
        self.seg_mode = SegmentedControl(
            self, [(tr("Video"), "video"), (tr("Audio"), "audio")], "video",
            fonts.font(s(12), "Medium"), pal0["seg_bg"], pal0["seg_sel"],
            pal0["muted"], pal0["on_accent"], s(13))    # более круглые края
        self.seg_mode.changed.connect(self._on_mode_change)

        self.search = SearchField(app, self._on_submit, self._on_debounce, self)
        self.trim = TrimPanel(app, self)
        self.trim.hide()
        self.trim.saved.connect(self._on_trim_saved)
        self.trim.copied.connect(self._on_trim_copied)
        self.playlist = PlaylistPanel(app, self)
        self.playlist.hide()
        self.playlist.download.connect(self._on_playlist_download)
        self.history = HistoryList(app, self)
        self.history.trimClicked.connect(self._open_trim)
        self.history.closeTrimClicked.connect(lambda e=None: self._close_trim())
        self.history.stopClicked.connect(self._cancel_download)
        self.history.copyClicked.connect(self._copy_entry)
        self.history.moreClicked.connect(self._show_more_menu)

        # Всплывающая подсказка (напр., «плейлисты — через окно»).
        pal = themes.palette(app.settings.get("theme", themes.DEFAULT_THEME))
        self._msg = QLabel("", self)
        self._msg.setFont(fonts.font(s(11), "Medium"))
        self._msg.setAlignment(Qt.AlignCenter)
        self._msg.setStyleSheet(
            f"QLabel {{ color: {pal['title']}; background: {pal['card_bg']};"
            f" border: 1px solid {pal['border']}; border-radius: {s(10)}px;"
            f" padding: {s(7)}px {s(14)}px; }}")
        self._msg.hide()
        self._msg_timer = QTimer(self)
        self._msg_timer.setSingleShot(True)
        self._msg_timer.timeout.connect(self._hide_msg)

        # Пока окно открыто — периодически убираем из истории удалённые с диска
        # файлы (не дожидаясь перезапуска Spotlight).
        self._prune_timer = QTimer(self)
        self._prune_timer.setInterval(3000)
        self._prune_timer.timeout.connect(self._prune_visible)

        # Корректно гасим фоновые потоки при выходе (иначе Qt ругается
        # «QThread: Destroyed while thread is still running»).
        QApplication.instance().aboutToQuit.connect(self.shutdown)
        self._relayout()

    def _notify(self, text):
        """Короткая всплывающая подсказка по центру, у верха истории."""
        self._msg.setText(text)
        self._msg.adjustSize()
        mw = self._msg.width()
        x = self.MX + (self.CW - mw) // 2
        y = self.history.y() + self.app._s(12)
        self._msg.move(x, y)
        self._msg.show()
        self._msg.raise_()
        anim.fade(self._msg, 0.0, 1.0, 160)
        self._msg_timer.start(2600)

    def _hide_msg(self):
        anim.fade(self._msg, 1.0, 0.0, 200, on_finished=self._msg.hide)

    def shutdown(self):
        """Останавливает воспроизведение/воркеры перед выходом приложения."""
        try:
            self.trim.stop()
        except Exception:
            pass
        self._sched.stop_all()
        self._dls.clear()

    # --- геометрия ----------------------------------------------------- #
    def _base_height(self):
        h = (self.MY * 2 + self.TOGGLE_TOP + self.TOGGLE_H + self.TOGGLE_GAP
             + self.SEARCH_H + self.GAP + self.HIST_H)
        if self._trim_open:
            h += self._trim_h + self.GAP
        elif self._pl_open:
            h += self._pl_h + self.GAP
        return h

    def _relayout(self):
        w = self.CW + 2 * self.MX
        self.resize(w, self._base_height())
        tw = self.app._s(150)
        seg_x = self.MX + (self.CW - tw) // 2
        self.seg_mode.setGeometry(seg_x, self.MY + self.TOGGLE_TOP, tw, self.TOGGLE_H)
        sy = self.MY + self.TOGGLE_TOP + self.TOGGLE_H + self.TOGGLE_GAP
        self.search.setGeometry(self.MX, sy, self.CW, self.SEARCH_H)
        y = sy + self.SEARCH_H + self.GAP
        if self._trim_open:
            self.trim.setGeometry(self.MX, y, self.CW, max(1, self._trim_h))
            y += self._trim_h + self.GAP
        elif self._pl_open:
            self.playlist.setGeometry(self.MX, y, self.CW, max(1, self._pl_h))
            y += self._pl_h + self.GAP
        self.history.setGeometry(self.MX, y, self.CW, self.HIST_H)

    # --- показ/скрытие ------------------------------------------------- #
    def toggle(self):
        if self.isVisible():
            self.hide_spotlight()
        else:
            self.show_spotlight()

    def show_spotlight(self):
        self._closing = False
        self.setWindowOpacity(1.0)
        self._close_trim(animate=False)
        self._close_playlist(animate=False)
        self._relayout()                        # сначала размеры (ширина списка), потом строки
        self.history.rebuild(history.prune_missing())   # выкинуть удалённые с диска
        self.app.sync_view_mirrors(self)        # подтянуть идущие загрузки из окна
        cur = QCursor.pos()
        screen = QGuiApplication.screenAt(cur) or QGuiApplication.primaryScreen()
        avail = screen.availableGeometry()
        w, h = self.width(), self.height()
        x = avail.left() + (avail.width() - w) // 2
        y = avail.top() + int((avail.height() - h) * 0.44)   # ниже, ближе к центру
        y = max(avail.top() + self.MY, min(y, avail.bottom() - h - self.MY))
        self.move(x, y)
        # Окно появляется как единое целое (windowOpacity 0->1), а панели слегка
        # подъезжают снизу. НЕ используем QGraphicsOpacityEffect на детях: на
        # полупрозрачном окне это на кадр показывало насквозь рабочий стол
        # (моргание). Дети остаются непрозрачными — окно проявляется целиком.
        self.setWindowOpacity(0.0)
        self._prep_slide(self.seg_mode, self.app._s(6))
        self._prep_slide(self.search, self.app._s(10))
        self._prep_slide(self.history, self.app._s(18))
        self.show()
        self.raise_()
        self.activateWindow()
        self.search.edit().setFocus()
        self.search.edit().selectAll()
        self._prune_timer.start()
        a = QPropertyAnimation(self, b"windowOpacity", self)
        a.setDuration(170)
        a.setStartValue(0.0)
        a.setEndValue(1.0)
        a.setEasingCurve(QEasingCurve.OutCubic)
        a.start()
        self._show_anim = a
        self._run_slide(self.seg_mode, delay=0)
        self._run_slide(self.search, delay=0)
        self._run_slide(self.history, delay=55)

    def _prep_slide(self, widget, dy):
        """Смещает элемент вниз ДО show() (без эффектов прозрачности)."""
        end = widget.pos()
        widget._enter_end = end
        widget._enter_start = QPoint(end.x(), end.y() + dy)
        widget.move(widget._enter_start)

    def _run_slide(self, widget, delay=0):
        def go():
            pa = QPropertyAnimation(widget, b"pos", widget)
            pa.setDuration(300)
            pa.setStartValue(widget._enter_start)
            pa.setEndValue(widget._enter_end)
            pa.setEasingCurve(QEasingCurve.OutCubic)
            pa.start()
            widget._enter_pos_anim = pa
        if delay:
            QTimer.singleShot(delay, go)
        else:
            go()

    def hide_spotlight(self):
        if not self.isVisible() or self._closing:
            return
        self._close_trim(animate=False)
        self._close_playlist(animate=False)
        self._closing = True
        a = QPropertyAnimation(self, b"windowOpacity", self)
        a.setDuration(150)
        a.setStartValue(1.0)
        a.setEndValue(0.0)
        a.setEasingCurve(QEasingCurve.InCubic)
        a.finished.connect(self._after_fade_out)
        a.start()
        self._exit_anim = a

    def _after_fade_out(self):
        self.hide()
        self.setWindowOpacity(1.0)
        self._closing = False
        self._prune_timer.stop()
        self._update_tray_ring()                # если идут загрузки — кольцо в трее

    def _prune_visible(self):
        """Убирает из истории строки, чьи файлы удалили с диска (окно открыто)."""
        if not self.isVisible():
            return
        ids = self.history.drop_missing()
        if ids:
            for entry_id in ids:
                history.remove(entry_id)

    # авто-скрытие при потере фокуса (кроме моментов, когда открыто своё меню)
    def event(self, e):
        if e.type() == QEvent.WindowDeactivate and self.isVisible() \
                and not self._suppress_hide:
            QTimer.singleShot(120, self._maybe_hide_on_deactivate)
        return super().event(e)

    def _maybe_hide_on_deactivate(self):
        if self._suppress_hide or not self.isVisible():
            return
        # В режиме обрезки/выбора плейлиста не прячем — панель закрылась бы случайно.
        if self._trim_open or self._pl_open:
            return
        # Режим «Pinned» — не прячем по потере фокуса (только Esc / хоткей).
        if self.app.settings.get("spotlight_dismiss", "focus") != "focus":
            return
        if self.isActiveWindow():
            return
        # Если фокус ушёл на наше меню — не прячем.
        if self._menu is not None and self._menu.isVisible():
            return
        self.hide_spotlight()

    def keyPressEvent(self, e: QKeyEvent):
        if e.key() == Qt.Key_Escape:
            if self._trim_open:
                self._close_trim()
            elif self._pl_open:
                self._close_playlist()
            else:
                self.hide_spotlight()
            return
        # Пробел в режиме обрезки — воспроизведение/пауза превью.
        if e.key() == Qt.Key_Space and self._trim_open:
            self.trim._toggle_play()
            return
        super().keyPressEvent(e)

    # --- загрузка ------------------------------------------------------ #
    def _on_mode_change(self, mode):
        self._mode = mode

    def _option_for(self):
        """Вариант формата по текущему переключателю Video/Audio."""
        if self._mode == "audio":
            return {"label": "Best Quality", "fmt": "ba/b", "mp3": True}
        return {"label": "Best Quality", "fmt": downloader.BEST_VIDEO_FMT, "mp3": False}

    def _on_submit(self, text):
        self._try_download(text, auto=False)

    def _on_debounce(self, text):
        t = (text or "").strip()
        if t.lower().startswith(("http://", "https://")):
            self._try_download(t, auto=True)

    def _try_download(self, text, auto):
        url = (text or "").strip()
        if not url:
            return
        if not downloader.is_supported_url(url):
            # красная подсветка только на осознанную попытку (Enter или http-ссылка)
            if not auto or url.lower().startswith(("http://", "https://")):
                self.search.flash(QColor("#e05a5a"))
            return
        # Любой плейлист (чистый /playlist ИЛИ ?list=…) — открываем панель выбора
        # роликов (окно с выбором, что скачать).
        if downloader.is_playlist_url(url):
            self._open_playlist(url)
            self.search.edit().clear()
            return
        # эту же ссылку уже качаем — не дублируем
        if any(d["url"] == url for d in self._dls.values()):
            self.search.edit().clear()
            return
        from core import tools
        if not (tools.have_ytdlp() and tools.have_ffmpeg()):
            # без бинарников — открываем основное окно (там докачка)
            self.app.show_near_tray()
            return
        option = self._option_for()         # Video / Audio по переключателю
        convert = downloader.should_convert(option, url, self.app.settings)

        # Сразу помещаем «файл» в историю как строку-прогресс (без кнопок).
        entry = {"id": uuid.uuid4().hex[:12], "url": url,
                 "host": history.host_label(url), "title": "", "path": None,
                 "thumb": "", "ts": 0,
                 "is_audio": bool(option.get("mp3") or option.get("audio"))}
        row = self.history.insert_downloading(entry)
        dl_id = entry["id"]
        self._dls[dl_id] = {"row": row, "frac": 0.0, "convert": convert, "url": url,
                            "copy": False, "announce": False, "managed": False,
                            "title": None, "uploader": None,
                            "thumb": self._fetch_preview(url, row, dl_id)}
        self._sched.submit(dl_id, option, url, None, managed=False)
        self.app.mirror_start("spotlight", dl_id, entry)
        self._update_tray_ring()         # запустить спиннер в трее
        self.search.edit().clear()       # поле снова свободно для новых ссылок

    def start_bg_download(self, url, copy_on_done=False, announce=False, managed=True):
        """Фоновая загрузка (Paste/Toast): добавляет строку-прогресс в историю,
        даже если окно Spotlight скрыто. copy_on_done — скопировать готовый файл
        в буфер по завершении (для Toast); announce — показать тост «Downloaded»;
        managed=True — подчиняться лимиту очереди (Paste), False — старт сразу
        (Toast). Возвращает True, если запущено."""
        url = (url or "").strip()
        if not downloader.is_downloadable_single(url):
            return False
        if any(d["url"] == url for d in self._dls.values()):
            return False
        from core import tools
        if not (tools.have_ytdlp() and tools.have_ffmpeg()):
            return False
        option = {"label": "Best Quality", "fmt": downloader.BEST_VIDEO_FMT, "mp3": False}
        convert = downloader.should_convert(option, url, self.app.settings)
        entry = {"id": uuid.uuid4().hex[:12], "url": url,
                 "host": history.host_label(url), "title": "", "path": None,
                 "thumb": "", "ts": 0, "is_audio": False}
        row = self.history.insert_downloading(entry)
        dl_id = entry["id"]
        self._dls[dl_id] = {"row": row, "frac": 0.0, "convert": convert, "url": url,
                            "copy": bool(copy_on_done), "announce": bool(announce),
                            "managed": bool(managed), "title": None, "uploader": None,
                            "thumb": self._fetch_preview(url, row, dl_id)}
        self._sched.submit(dl_id, option, url, None, managed=bool(managed))
        self.app.mirror_start("spotlight", dl_id, entry)
        # Toast-загрузка: если сейчас нет других активных — детерминированное кольцо
        # (заполнение по прогрессу -> галочка). Проверку делаем ДО _update_tray_ring,
        # пока эта загрузка ещё не учтена в счётчике.
        if announce:
            self._dls[dl_id]["toast_ring"] = self.app.toast_ring_begin(dl_id)
            self.app._toast_active = True      # пока качается — новые тосты не показываем
        self._update_tray_ring()
        return True

    def _fetch_preview(self, url, row, dl_id=None):
        """Тянет обложку+название+автора через yt-dlp; показывает в строке сразу,
        обновляет метаданные загрузки и зеркалит их в другое окно."""
        from core.workers import SpotlightThumbWorker
        tw = SpotlightThumbWorker(url, self)

        def on_done(data, title, uploader, height, fps, r=row, i=dl_id):
            try:
                if data:
                    pm = QPixmap()
                    if pm.loadFromData(data):
                        r._thumb_bytes = data      # постер для истории (не кадр)
                        r.set_preview(pm)
                if title and not r.entry.get("title"):
                    r.entry["title"] = title
                if uploader and not r.entry.get("uploader"):
                    r.entry["uploader"] = uploader
                if height:
                    r.entry["height"] = height     # для пилюли разрешение·fps
                if fps:
                    r.entry["fps"] = fps
                r._sub = r._make_sub()
                r.update()
            except RuntimeError:
                return   # строку уже удалили (упавшая/отменённая загрузка)
            if i is not None:
                d = self._dls.get(i)
                if d is not None:
                    if title:
                        d["title"] = title
                    if uploader:
                        d["uploader"] = uploader
                self.app.mirror_meta("spotlight", i, data, title, uploader,
                                     height, fps)
        tw.done.connect(on_done)
        tw.start()
        return tw

    def _on_progress(self, dl_id, p):
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
        self.app.mirror_progress("spotlight", dl_id, frac, p)
        self.app.toast_ring_progress(dl_id, frac)     # no-op, если не Toast-кольцо
        self._update_tray_ring()

    def _on_done(self, dl_id, dest):
        d = self._dls.pop(dl_id, None)
        tb = None
        if d is not None and d.get("row") is not None:
            try:
                tb = getattr(d["row"], "_thumb_bytes", None)   # постер от yt-dlp
            except RuntimeError:
                tb = None
        # Пишем в историю без авто-уведомлений — зеркало (mirror_finish) сам
        # финиширует строку в другом окне.
        entry = self.app.record_download(dest, d["url"] if d else "",
                                         d.get("title") if d else None,
                                         notify_window=False, thumb_bytes=tb,
                                         uploader=d.get("uploader") if d else None)
        if entry is None:
            # «Завершилось», но файла на месте нет (пропал/не записался) — это сбой:
            # строка на пару секунд становится красным крестиком, потом уезжает.
            self._row_error(d.get("row") if d else None)
            self.app.mirror_remove("spotlight", dl_id)
            self.app.toast_ring_end(dl_id, False)     # крестик, если это Toast-кольцо
            self._end_toast(d)
            if self.isVisible():
                self.search.flash(QColor("#e05a5a"))
            self._update_tray_ring()
            return
        if d is not None and d["row"] is not None:
            d["row"].finish(entry, pulse=self.isVisible())
        elif self.isVisible():
            self.history.insert_new(entry)
        self.app.mirror_finish("spotlight", dl_id, entry)
        self.app.toast_ring_end(dl_id, True)          # галочка, если это Toast-кольцо
        # Toast: копируем готовый файл в буфер (привязка по dl_id — работает, даже
        # если после старта в историю добавились другие ролики) + тост «Downloaded».
        if d is not None and dest and os.path.isfile(dest):
            if d.get("copy"):
                self.app._copy_file_to_clipboard(dest)
            if d.get("announce"):
                self.app.show_download_toast(dest)
        self._end_toast(d)
        self._update_tray_ring()

    def _end_toast(self, d):
        """Toast-загрузка завершилась (успех/сбой/отмена) — снимаем блокировку,
        чтобы снова могли всплывать тосты из буфера."""
        if d is not None and d.get("announce"):
            self.app._toast_active = False

    def _on_failed(self, dl_id, msg):
        d = self._dls.pop(dl_id, None)
        if d is None:
            return                       # уже отменено пользователем (Stop) — молча
        self._row_error(d.get("row"))    # красный крестик на пару секунд, затем уезжает
        self.app.mirror_remove("spotlight", dl_id)
        self.app.toast_ring_end(dl_id, False)         # крестик, если это Toast-кольцо
        self._end_toast(d)
        if self.isVisible():
            self.search.flash(QColor("#e05a5a"))
        self._update_tray_ring()

    def _row_error(self, row):
        """Строка сбоя: красный крестик на ~2.6с, затем плавно убирается."""
        if row is None:
            return
        try:
            row.to_error()
        except RuntimeError:
            return
        QTimer.singleShot(2600, lambda r=row: self.history.remove_row(r))

    def _cancel_download(self, entry):
        """Stop на строке: своя загрузка — отменяем; строка-зеркало — просим
        отменить владельца (окно)."""
        dl_id = entry.get("id")
        if self.cancel_own(dl_id):
            return
        if dl_id in self._mirrors:
            self.app.request_cancel(dl_id)
            return

    def cancel_own(self, dl_id):
        d = self._dls.pop(dl_id, None)
        if d is None:
            return False
        self._sched.cancel(dl_id)         # снимает из очереди ИЛИ останавливает воркер
        if d.get("row") is not None:
            self.history.remove_row(d["row"])
        self.app.mirror_remove("spotlight", dl_id)
        self.app.toast_ring_end(dl_id, False)         # если это было Toast-кольцо
        self._end_toast(d)
        self._update_tray_ring()
        return True

    # --- зеркала загрузок из окна -------------------------------------- #
    def add_mirror(self, dl_id, entry):
        if dl_id in self._mirrors:
            return
        row = self.history.insert_downloading(entry)
        self._mirrors[dl_id] = row
        self._fetch_preview_url(entry.get("_thumb_url"), entry.get("url", ""), row)

    def update_mirror(self, dl_id, frac, info=None):
        r = self._mirrors.get(dl_id)
        if r is not None:
            r.set_progress(frac, info)

    def finish_mirror(self, dl_id, entry):
        r = self._mirrors.pop(dl_id, None)
        if r is None:
            return
        if entry is not None:
            r.finish(entry, pulse=self.isVisible())
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
        out = []
        for dl_id, d in self._dls.items():
            out.append((dl_id, {
                "id": dl_id, "url": d.get("url", ""),
                "host": history.host_label(d.get("url", "")),
                "title": d.get("title") or "", "uploader": d.get("uploader") or "",
                "path": None, "thumb": "", "ts": 0,
                "_thumb_url": d.get("thumb_url")}, d.get("frac", 0.0)))
        return out

    # --- кольцо в трее, пока спотлайт скрыт, а загрузка идёт ------------ #
    def _update_tray_ring(self):
        """Сообщаем координатору в app число своих активных загрузок. Спиннером в
        трее управляет app (суммарно по всем источникам)."""
        self.app.report_active_downloads("spotlight", len(self._dls))

    # --- обрезка ------------------------------------------------------- #
    def _open_trim(self, entry):
        path = entry.get("path", "")
        if not path or not os.path.isfile(path):
            self.search.flash(QColor("#e05a5a"))
            return
        self._trim_url = entry.get("url", "")     # для истории обрезанного фрагмента
        wf = entry.get("waveform") or ""          # готовая заготовка волны (аудио)
        if self._trim_open:
            same = (os.path.normpath(path)
                    == os.path.normpath(self.trim.current_path() or ""))
            if same:
                return                    # уже режем этот файл — повторно нельзя
            if self.trim.is_dirty():
                # ползунки двигали — просим подтвердить смену файла
                self.trim.confirm_switch(tr("Discard current trim?"),
                                         lambda p=path, w=wf: self._load_trim(p, w))
            else:
                self._load_trim(path, wf)  # изменений не было — переключаемся сразу
            return
        # первое открытие — с анимацией раскрытия. Файл грузим сразу (filmstrip и
        # первый кадр начинают готовиться без задержки).
        self._trim_open = True
        self._trim_h = 0
        target = self.trim.target_height()
        self.trim.begin_anim()       # нативное видео прячем на время раскрытия
        self._load_trim(path, wf)
        self._relayout()             # геометрия ДО show — иначе панель успевала
        self.trim.show()             # мелькнуть в старом месте/размере
        self._fit_for_extra(target + self.GAP)
        anim.animate(self, 0.0, 1.0, 560,
                     lambda v: self._set_trim_h(int(target * v)),
                     easing=QEasingCurve.InOutCubic,
                     on_finished=self.trim.end_anim, attr="_trim_anim")

    def _load_trim(self, path, waveform=None):
        """Загрузить файл в панель обрезки и отметить его строку активной."""
        self.trim.open_for(path, waveform)
        self.history.set_active_path(path)

    def _set_trim_h(self, h):
        self._trim_h = h
        self._relayout()

    def _close_trim(self, animate=True):
        if not self._trim_open:
            return
        self.history.set_active_path(None)   # крестик -> снова ножницы
        self.trim.begin_anim()   # видео убираем СРАЗУ: нативное окно не схлопнуть
        if not animate:
            self._trim_open = False
            self._trim_h = 0
            self.trim.stop()
            self.trim.hide()
            self._relayout()
            return
        start = self._trim_h

        def done():
            self._trim_open = False
            self._trim_h = 0
            self.trim.stop()
            self.trim.hide()
            self._relayout()
        anim.animate(self, 1.0, 0.0, 500,
                     lambda v: self._set_trim_h(int(start * v)),
                     easing=QEasingCurve.InOutCubic, on_finished=done, attr="_trim_anim")

    def _fit_for_extra(self, extra):
        screen = QGuiApplication.screenAt(self.pos()) or QGuiApplication.primaryScreen()
        avail = screen.availableGeometry()
        need = self.y() + self._base_height() + extra + self.MY
        if need > avail.bottom():
            new_y = max(avail.top() + self.MY, avail.bottom() - self._base_height()
                        - extra - self.MY)
            self.move(self.x(), new_y)

    # --- плейлист: панель выбора + параллельная загрузка --------------- #
    def _open_playlist(self, url):
        from core.workers import PlaylistProbeWorker
        self._notify(tr("Fetching playlist…"))
        self._pl_probe = PlaylistProbeWorker(url, self.app.settings, self)
        self._pl_probe.done.connect(self._on_playlist_probed)
        self._pl_probe.error.connect(lambda m: self.search.flash(QColor("#e05a5a")))
        self._pl_probe.start()

    def _on_playlist_probed(self, info):
        entries = downloader.playlist_entries(info)
        if not entries:
            self.search.flash(QColor("#e05a5a"))
            return
        self._show_playlist_panel(entries, info.get("title") or tr("Playlist"))

    def _show_playlist_panel(self, entries, title):
        if self._trim_open:
            self._close_trim(animate=False)
        self.playlist.open_for(entries, title)
        if self._pl_open:
            self._relayout()
            return
        self._pl_open = True
        self._pl_h = 0
        target = self.playlist.target_height()
        self.playlist.show()
        self._fit_for_extra(target + self.GAP)
        anim.animate(self, 0.0, 1.0, 300,
                     lambda v: self._set_pl_h(int(target * v)),
                     easing=QEasingCurve.OutCubic, attr="_pl_anim")

    def _set_pl_h(self, h):
        self._pl_h = h
        self._relayout()

    def _close_playlist(self, animate=True):
        if not self._pl_open:
            return
        if not animate:
            self._pl_open = False
            self._pl_h = 0
            self.playlist.hide()
            self._relayout()
            return
        start = self._pl_h

        def done():
            self._pl_open = False
            self._pl_h = 0
            self.playlist.hide()
            self._relayout()
        anim.animate(self, 1.0, 0.0, 240,
                     lambda v: self._set_pl_h(int(start * v)),
                     easing=QEasingCurve.InCubic, on_finished=done, attr="_pl_anim")

    def _on_playlist_download(self, entries):
        """Кнопка Download в панели: выбранные ролики уезжают в историю и качаются
        с ограничением параллельности (parallel_downloads); панель закрывается."""
        option = self._option_for()
        items = []
        for e in entries:
            entry = {"id": uuid.uuid4().hex[:12], "url": e["url"],
                     "host": history.host_label(e["url"]),
                     "title": e.get("title") or "", "uploader": e.get("uploader") or "",
                     "duration": e.get("duration") or 0, "path": None, "thumb": "",
                     "ts": 0, "_thumb_url": e.get("thumbnail"),
                     "is_audio": bool(option.get("mp3") or option.get("audio"))}
            items.append((entry, option))
        self._close_playlist()
        self._enqueue_downloads(items)

    def _enqueue_downloads(self, items):
        """Создаёт строки-загрузки для всех выбранных роликов и отдаёт их
        планировщику (managed): активны N (лимит parallel), остальные ждут."""
        for entry, option in items:
            row = self.history.insert_downloading(entry)
            dl_id = entry["id"]
            url = entry["url"]
            convert = downloader.should_convert(option, url, self.app.settings)
            self._dls[dl_id] = {
                "row": row, "frac": 0.0, "convert": convert, "url": url,
                "copy": False, "announce": False, "managed": True,
                "title": entry.get("title") or None,
                "uploader": entry.get("uploader") or None,
                "thumb": self._fetch_preview_url(entry.get("_thumb_url"), url, row)}
            self._sched.submit(dl_id, option, url, entry.get("title") or None,
                               managed=True)
            self.app.mirror_start("spotlight", dl_id, entry)
        self._update_tray_ring()

    def _fetch_preview_url(self, thumb_url, url, row):
        """Как _fetch_preview, но если известен прямой URL обложки (из плейлиста) —
        тянем его напрямую (быстрее, без запроса к yt-dlp на каждый ролик)."""
        if not thumb_url:
            return self._fetch_preview(url, row)
        from core.workers import ThumbWorker
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
        tw.start()
        return tw

    def _on_trim_saved(self, out):
        entry = self.app.record_download(out, getattr(self, "_trim_url", ""), None,
                                         notify_window=True)
        if entry is not None:
            self.history.insert_new(entry)
        self.search.flash(QColor("#34c759"))

    def _on_trim_copied(self, out):
        self.app._copy_file_to_clipboard(out)
        self.search.flash(QColor("#34c759"))

    # --- строки истории ------------------------------------------------ #
    def _copy_entry(self, entry):
        path = entry.get("path", "")
        if path and os.path.isfile(path):
            self.app._copy_file_to_clipboard(path)
            self.search.flash(QColor("#34c759"))
        else:
            self.search.flash(QColor("#e05a5a"))

    def _show_more_menu(self, entry, gpos):
        import time as _t
        # Повторный клик по «три точки» сначала закрыл Popup — не переоткрываем.
        if _t.monotonic() - getattr(self.app, "_menu_closed_ts", 0.0) < 0.25:
            return
        from tray import TrayMenu
        items = [
            (tr("Open"), lambda e=entry: self.app.play_file(e.get("path", ""))),
            (tr("Remove From List"), lambda e=entry: self._remove_entry(e)),
            (tr("Delete"), lambda e=entry: self._delete_entry(e), "danger"),
        ]
        self._suppress_hide = True
        self._menu = TrayMenu(self.app, items)
        self._menu.popup_at(gpos)
        QTimer.singleShot(400, lambda: setattr(self, "_suppress_hide", False))

    def _remove_entry(self, entry):
        history.remove(entry.get("id", ""))
        self.app.refresh_histories()         # отразить в окне и Spotlight

    def _delete_entry(self, entry):
        if self.app.delete_file(entry):      # файл с диска + запись из истории
            self.app.refresh_histories()
        else:
            # Не удалось (файл занят/заблокирован) — краснеем строку с пояснением.
            self.history.flash_error(entry.get("id", ""),
                                     tr("Couldn't delete — file in use"))

    def release_trim_file(self, path):
        """Перед удалением файла: если он открыт в панели обрезки, жёстко
        освобождаем (иначе Qt держит файл и его не удалить)."""
        try:
            cur = self.trim.current_path()
            if cur and os.path.normpath(cur) == os.path.normpath(path):
                self.trim.hard_release()
        except Exception:
            pass

    # --- фон ----------------------------------------------------------- #
    def paintEvent(self, event):
        # окно прозрачное; панели (и переключатель) рисуют себя сами
        pass
