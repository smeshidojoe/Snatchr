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
    QPainter, QColor, QPen, QPixmap, QGuiApplication, QCursor, QKeyEvent
)
from PySide6.QtWidgets import QWidget, QLineEdit, QLabel, QApplication

from core import fonts, themes, downloader, history
from core.i18n import tr
from ui import anim
from ui.spotlight_history import HistoryList
from ui.spotlight_trim import TrimPanel


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
        p.setBrush(self._bg)
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
        self.SEARCH_H = s(60)
        self.GAP = s(12)
        self.HIST_H = s(330)

        self._dls = {}                 # id -> {worker,row,frac,convert,url} (свои загрузки)
        self._ext = {}                 # id -> {row,worker,url} (загрузки из окна)
        self._trim_open = False
        self._trim_h = 0
        self._suppress_hide = False
        self._menu = None
        self._closing = False          # идёт анимация исчезновения окна
        self._tray_ring_on = False     # показываем ли кольцо в трее (скрытый спотлайт)

        self.search = SearchField(app, self._on_submit, self._on_debounce, self)
        self.trim = TrimPanel(app, self)
        self.trim.hide()
        self.trim.saved.connect(self._on_trim_saved)
        self.trim.copied.connect(self._on_trim_copied)
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
        for d in list(self._dls.values()):
            w = d.get("worker")
            if w is not None:
                try:
                    w.stop()
                    w.wait(1500)
                except Exception:
                    pass
        self._dls.clear()

    # --- геометрия ----------------------------------------------------- #
    def _base_height(self):
        h = self.MY * 2 + self.SEARCH_H + self.GAP + self.HIST_H
        if self._trim_open:
            h += self._trim_h + self.GAP
        return h

    def _relayout(self):
        w = self.CW + 2 * self.MX
        self.resize(w, self._base_height())
        self.search.setGeometry(self.MX, self.MY, self.CW, self.SEARCH_H)
        y = self.MY + self.SEARCH_H + self.GAP
        if self._trim_open:
            self.trim.setGeometry(self.MX, y, self.CW, max(1, self._trim_h))
            y += self._trim_h + self.GAP
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
        self._relayout()                        # сначала размеры (ширина списка), потом строки
        self.history.rebuild(history.prune_missing())   # выкинуть удалённые с диска
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
        self._prep_slide(self.search, self.app._s(10))
        self._prep_slide(self.history, self.app._s(18))
        self.show()
        self.raise_()
        self.activateWindow()
        self.search.edit().setFocus()
        self.search.edit().selectAll()
        self._stop_tray_ring()                  # окно снова видно — кольцо не нужно
        self._prune_timer.start()
        a = QPropertyAnimation(self, b"windowOpacity", self)
        a.setDuration(170)
        a.setStartValue(0.0)
        a.setEndValue(1.0)
        a.setEasingCurve(QEasingCurve.OutCubic)
        a.start()
        self._show_anim = a
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
        # В режиме обрезки не прячем — иначе панель обрезки закроется случайно.
        if self._trim_open:
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
            else:
                self.hide_spotlight()
            return
        super().keyPressEvent(e)

    # --- загрузка ------------------------------------------------------ #
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
        # Плейлисты (только чистые /playlist) — через окно; одиночные видео с
        # ?list=… пускаем.
        if "/playlist" in url.lower():
            self.search.flash(QColor("#e05a5a"))
            self._notify(tr("Playlists: open the link in the main window"))
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
        option = {"label": "Best Quality", "fmt": downloader.BEST_VIDEO_FMT, "mp3": False}
        convert = downloader.should_convert(option, url, self.app.settings)

        # Сразу помещаем «файл» в историю как строку-прогресс (без кнопок).
        entry = {"id": uuid.uuid4().hex[:12], "url": url,
                 "host": history.host_label(url), "title": "", "path": None,
                 "thumb": "", "ts": 0}
        row = self.history.insert_downloading(entry)

        from core.workers import DownloadWorker
        dl_id = entry["id"]
        worker = DownloadWorker(option, url, self.app.settings, None, self)
        worker.progress.connect(lambda p, i=dl_id: self._on_progress(i, p))
        worker.finished_ok.connect(lambda dest, i=dl_id: self._on_done(i, dest))
        worker.failed.connect(lambda msg, i=dl_id: self._on_failed(i, msg))
        self._dls[dl_id] = {"worker": worker, "row": row, "frac": 0.0,
                            "convert": convert, "url": url,
                            "thumb": self._fetch_preview(url, row)}
        worker.start()
        self.search.edit().clear()       # поле снова свободно для новых ссылок

    def _fetch_preview(self, url, row):
        """Тянет обложку через yt-dlp и показывает её в строке-прогрессе сразу."""
        from core.workers import SpotlightThumbWorker
        tw = SpotlightThumbWorker(url, self)

        def on_done(data, r=row):
            if not data:
                return
            pm = QPixmap()
            if not pm.loadFromData(data):
                return
            try:
                r.set_preview(pm)
            except RuntimeError:
                pass   # строку уже удалили (упавшая/отменённая загрузка)
        tw.done.connect(on_done)
        tw.start()
        return tw

    def _on_progress(self, dl_id, p):
        d = self._dls.get(dl_id)
        if d is None:
            return
        if p.get("stage") == "convert":
            frac = 0.5 + 0.5 * (p.get("frac") or 0.0)
        else:
            base = p.get("frac") or 0.0
            frac = base * 0.5 if d["convert"] else base
        d["frac"] = frac
        if d["row"] is not None:
            d["row"].set_progress(frac)
        self._update_tray_ring()

    def _on_done(self, dl_id, dest):
        d = self._dls.pop(dl_id, None)
        entry = self.app.record_download(dest, d["url"] if d else "", None,
                                         notify_spotlight=False)
        if d is not None and d["row"] is not None and entry is not None:
            d["row"].finish(entry, pulse=self.isVisible())
        elif entry is not None and self.isVisible():
            self.history.insert_new(entry)
        self._update_tray_ring()

    def _on_failed(self, dl_id, msg):
        d = self._dls.pop(dl_id, None)
        if d is None:
            return                       # уже отменено пользователем (Stop) — молча
        if d["row"] is not None:
            self.history.remove_row(d["row"])
        if self.isVisible():
            self.search.flash(QColor("#e05a5a"))
        self._update_tray_ring()

    def _cancel_download(self, entry):
        """Отмена идущей загрузки по кнопке Stop в её строке (своей или из окна)."""
        dl_id = entry.get("id")
        d = self._dls.pop(dl_id, None)
        if d is not None:
            w = d.get("worker")
            if w is not None:
                try:
                    w.stop()
                except Exception:
                    pass
            if d.get("row") is not None:
                self.history.remove_row(d["row"])
            self._update_tray_ring()
            return
        # загрузка из окна: останавливаем её воркер (окно само сбросит свой UI)
        e = self._ext.get(dl_id)
        if e is not None:
            w = e.get("worker")
            if w is not None:
                try:
                    w.stop()
                except Exception:
                    pass

    # --- мост с загрузкой из окна программы ---------------------------- #
    def attach_window_download(self, info):
        """info: {id,url,worker,frac}. Показать загрузку окна как строку-прогресс."""
        dl_id = info.get("id")
        if not dl_id or dl_id in self._ext:
            return
        entry = {"id": dl_id, "url": info.get("url", ""),
                 "host": history.host_label(info.get("url", "")), "title": "",
                 "path": None, "thumb": "", "ts": 0}
        row = self.history.insert_downloading(entry)
        row.set_progress(info.get("frac", 0.0))
        self._ext[dl_id] = {"row": row, "worker": info.get("worker"),
                            "url": info.get("url", ""),
                            "thumb": self._fetch_preview(info.get("url", ""), row)}

    def update_window_download(self, dl_id, frac):
        e = self._ext.get(dl_id)
        if e is not None and e["row"] is not None:
            e["row"].set_progress(frac)

    def finish_window_download(self, dl_id, entry):
        e = self._ext.pop(dl_id, None)
        if e is not None and e["row"] is not None and entry is not None:
            e["row"].finish(entry, pulse=self.isVisible())
        elif entry is not None and self.isVisible():
            self.history.insert_new(entry)

    def remove_window_download(self, dl_id):
        e = self._ext.pop(dl_id, None)
        if e is not None and e["row"] is not None:
            self.history.remove_row(e["row"])

    def on_external_download(self, entry):
        """Ролик, скачанный из окна/трея, — тоже наезжает в список (если открыт)."""
        if entry is not None and self.isVisible():
            self.history.insert_new(entry)

    # --- кольцо в трее, пока спотлайт скрыт, а загрузка идёт ------------ #
    def _update_tray_ring(self):
        tray = self.app.tray
        if tray is None:
            return
        active = self._dls
        if self.isVisible() or not active:
            if self._tray_ring_on and not active:
                tray.animator.finish(True)     # все загрузки завершились — галочка
                self._tray_ring_on = False
            elif self._tray_ring_on:
                self._stop_tray_ring()          # окно открыто — просто убрать кольцо
            return
        if self.app.is_tray_downloading():
            return                              # не конфликтуем с Paste
        if not self._tray_ring_on:
            tray.animator.start()
            self._tray_ring_on = True
        frac = sum(d["frac"] for d in active.values()) / max(1, len(active))
        tray.animator.set_fraction(frac)

    def _stop_tray_ring(self):
        if self._tray_ring_on and self.app.tray is not None:
            self.app.tray.animator.abort()
        self._tray_ring_on = False

    # --- обрезка ------------------------------------------------------- #
    def _open_trim(self, entry):
        path = entry.get("path", "")
        if not path or not os.path.isfile(path):
            self.search.flash(QColor("#e05a5a"))
            return
        self._trim_url = entry.get("url", "")     # для истории обрезанного фрагмента
        if self._trim_open:
            same = (os.path.normpath(path)
                    == os.path.normpath(self.trim.current_path() or ""))
            if same:
                return                    # уже режем этот файл — повторно нельзя
            if self.trim.is_dirty():
                # ползунки двигали — просим подтвердить смену файла
                self.trim.confirm_switch(tr("Discard current trim?"),
                                         lambda p=path: self._load_trim(p))
            else:
                self._load_trim(path)     # изменений не было — переключаемся сразу
            return
        # первое открытие — с анимацией раскрытия
        self._trim_open = True
        self._trim_h = 0
        target = self.trim.target_height()
        self._load_trim(path)
        self.trim.show()
        # держим окно на экране: при росте вниз — сдвигаем вверх при нужде
        self._fit_for_extra(target + self.GAP)
        anim.animate(self, 0.0, 1.0, 300,
                     lambda v: self._set_trim_h(int(target * v)),
                     easing=QEasingCurve.OutCubic, attr="_trim_anim")

    def _load_trim(self, path):
        """Загрузить файл в панель обрезки и отметить его строку активной."""
        self.trim.open_for(path)
        self.history.set_active_path(path)

    def _set_trim_h(self, h):
        self._trim_h = h
        self._relayout()

    def _close_trim(self, animate=True):
        if not self._trim_open:
            return
        self.history.set_active_path(None)   # крестик -> снова ножницы
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
        anim.animate(self, 1.0, 0.0, 240,
                     lambda v: self._set_trim_h(int(start * v)),
                     easing=QEasingCurve.InCubic, on_finished=done, attr="_trim_anim")

    def _fit_for_extra(self, extra):
        screen = QGuiApplication.screenAt(self.pos()) or QGuiApplication.primaryScreen()
        avail = screen.availableGeometry()
        need = self.y() + self._base_height() + extra + self.MY
        if need > avail.bottom():
            new_y = max(avail.top() + self.MY, avail.bottom() - self._base_height()
                        - extra - self.MY)
            self.move(self.x(), new_y)

    def _on_trim_saved(self, out):
        entry = self.app.record_download(out, getattr(self, "_trim_url", ""), None,
                                         notify_spotlight=False)
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
        from tray import TrayMenu
        items = [
            (tr("Open"), lambda e=entry: self._open_folder(e)),
            (tr("Copy link"), lambda e=entry: self._copy_link(e)),
            (tr("Remove"), lambda e=entry: self._remove_entry(e)),
        ]
        self._suppress_hide = True
        self._menu = TrayMenu(self.app, items)
        self._menu.popup_at(gpos)
        QTimer.singleShot(400, lambda: setattr(self, "_suppress_hide", False))

    def _open_folder(self, entry):
        self.app._open_in_folder(entry.get("path", ""))

    def _copy_link(self, entry):
        try:
            QApplication.clipboard().setText(entry.get("url", ""))
        except Exception:
            pass

    def _remove_entry(self, entry):
        history.remove(entry.get("id", ""))
        self.history.rebuild(history.load())

    # --- фон ----------------------------------------------------------- #
    def paintEvent(self, event):
        # окно прозрачное; панели рисуют себя сами. Ничего не рисуем.
        pass
