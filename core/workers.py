"""
Фоновые потоки (QThread) для движка: установка бинарников, анализ ссылки,
скачивание. Всё с обработкой ошибок — UI никогда не зависает.
"""

import urllib.request

from PySide6.QtCore import QThread, Signal

from core import tools, downloader


class SetupWorker(QThread):
    """Первый запуск: докачивает недостающие бинарники (yt-dlp, ffmpeg/ffprobe)
    по очереди, отдавая общий прогресс на все скачивания."""
    status = Signal(str)          # человекочитаемый статус («Downloading yt-dlp…»)
    progress = Signal(float)      # общий прогресс по всем загрузкам (0..1)
    done = Signal(bool, str)      # (успех, сообщение об ошибке)

    def run(self):
        try:
            tasks = []
            if not tools.have_ytdlp():
                tasks.append(("Downloading yt-dlp…", tools.download_ytdlp, True))
            if not tools.have_ffmpeg():
                tasks.append(("Downloading ffmpeg…", tools.download_ffmpeg, True))

            n = len(tasks)
            for i, (label, fn, required) in enumerate(tasks):
                self.status.emit(label)
                try:
                    fn(progress=lambda frac, i=i: self.progress.emit((i + frac) / n))
                except Exception:
                    if required:
                        raise
                self.progress.emit((i + 1) / n)

            self.progress.emit(1.0)
            self.done.emit(True, "")
        except Exception as exc:
            self.done.emit(False, str(exc))


class UpdateYtdlpWorker(QThread):
    """Переустановка yt-dlp по требованию (кнопка в настройках)."""
    progress = Signal(float)      # ход скачивания (0..1)
    done = Signal(bool, str)      # (успех, сообщение об ошибке)

    def run(self):
        try:
            tools.update_ytdlp(progress=lambda f: self.progress.emit(float(f)))
            self.done.emit(True, "")
        except Exception as exc:
            self.done.emit(False, str(exc))


class UpdateFfmpegWorker(QThread):
    """Переустановка ffmpeg+ffprobe по требованию (кнопка в настройках)."""
    progress = Signal(float)      # ход скачивания (0..1)
    done = Signal(bool, str)      # (успех, сообщение об ошибке)

    def run(self):
        try:
            tools.update_ffmpeg(progress=lambda f: self.progress.emit(float(f)))
            self.done.emit(True, "")
        except Exception as exc:
            self.done.emit(False, str(exc))


class AppUpdateWorker(QThread):
    """Скачивание обновления приложения (zip релиза) с прогрессом."""
    progress = Signal(float)
    done = Signal(bool, str)

    def __init__(self, url, parent=None):
        super().__init__(parent)
        self._url = url

    def run(self):
        from core import updater
        try:
            updater.download_update(self._url,
                                    on_progress=lambda f: self.progress.emit(float(f)))
            self.done.emit(True, "")
        except Exception as exc:
            self.done.emit(False, str(exc))


class ProbeWorker(QThread):
    """Анализ ссылки через yt-dlp -J."""
    done = Signal(object)         # info (dict)
    error = Signal(str)

    def __init__(self, url, parent=None):
        super().__init__(parent)
        self._url = url

    def run(self):
        try:
            info = downloader.probe(self._url)
            self.done.emit(info)
        except Exception as exc:
            self.error.emit(str(exc))


class PlaylistProbeWorker(QThread):
    """Быстрый разбор плейлиста (flat) -> info с entries."""
    done = Signal(object)
    error = Signal(str)

    def __init__(self, url, parent=None):
        super().__init__(parent)
        self._url = url

    def run(self):
        try:
            self.done.emit(downloader.probe_flat(self._url))
        except Exception as exc:
            self.error.emit(str(exc))


class ThumbWorker(QThread):
    """Загрузка обложки по URL (bytes)."""
    done = Signal(bytes)

    def __init__(self, url, parent=None):
        super().__init__(parent)
        self._url = url

    def run(self):
        try:
            req = urllib.request.Request(self._url, headers={"User-Agent": "Snatchr"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                self.done.emit(resp.read())
        except Exception:
            self.done.emit(b"")


class MultiProbeWorker(QThread):
    """Анализ списка ссылок по очереди (для Multiple Links)."""
    item = Signal(int, object, str)   # индекс, info|None, текст ошибки
    done = Signal()

    def __init__(self, urls, parent=None):
        super().__init__(parent)
        self._urls = urls
        self._stopped = False

    def run(self):
        for i, u in enumerate(self._urls):
            if self._stopped:
                break
            try:
                self.item.emit(i, downloader.probe(u), "")
            except Exception as exc:
                self.item.emit(i, None, str(exc))
        self.done.emit()

    def stop(self):
        self._stopped = True


class _Hooks:
    """Мост между downloader.run_job и сигналами воркера."""
    def __init__(self, on_progress, on_status, set_proc, is_stopped):
        self.on_progress = on_progress
        self.on_status = on_status
        self.set_proc = set_proc
        self.is_stopped = is_stopped


class MultiDownloadWorker(QThread):
    """Последовательное скачивание набора заданий (url, option)."""
    item_progress = Signal(int, dict)
    item_status = Signal(int, str)        # «Converting…», «Trying streamlink…»
    item_done = Signal(int, bool, str)
    all_done = Signal()

    def __init__(self, jobs, settings, parent=None):
        super().__init__(parent)
        self._jobs = jobs
        self._settings = settings
        self._proc = None
        self._stopped = False

    def _set_proc(self, p):
        self._proc = p

    def run(self):
        for i, job in enumerate(self._jobs):
            if self._stopped:
                break
            url, opt = job[0], job[1]
            title = job[2] if len(job) > 2 else None
            hooks = _Hooks(
                lambda p, i=i: self.item_progress.emit(i, p),
                lambda s, i=i: self.item_status.emit(i, s),
                self._set_proc, lambda: self._stopped)
            try:
                ok, dest, log = downloader.run_job(opt, url, self._settings, hooks, title)
                if self._stopped:
                    self.item_done.emit(i, False, "Stopped")
                elif ok:
                    self.item_done.emit(i, True, dest)
                else:
                    log.save_error()
                    self.item_done.emit(i, False, downloader.friendly_error(log.text()))
            except Exception as exc:
                self.item_done.emit(i, False, str(exc))
        self.all_done.emit()

    def stop(self):
        self._stopped = True
        tools.kill_tree(self._proc)


class DownloadWorker(QThread):
    """Скачивание выбранного варианта с потоковым прогрессом."""
    progress = Signal(dict)       # {percent_str, speed, eta, frac}
    status = Signal(str)          # «Converting…» / «Trying streamlink…»
    finished_ok = Signal(str)     # путь к файлу (или "")
    failed = Signal(str)          # путь к лог-файлу / "Stopped" / "failed"

    def __init__(self, option, url, settings, title=None, parent=None):
        super().__init__(parent)
        self._option = option
        self._url = url
        self._settings = settings
        self._title = title
        self._proc = None
        self._stopped = False

    def _set_proc(self, p):
        self._proc = p

    def run(self):
        hooks = _Hooks(self.progress.emit, self.status.emit,
                       self._set_proc, lambda: self._stopped)
        try:
            ok, dest, log = downloader.run_job(self._option, self._url,
                                               self._settings, hooks, self._title)
            if self._stopped:
                self.failed.emit("Stopped")
            elif ok:
                self.finished_ok.emit(dest)
            else:
                log.save_error()
                self.failed.emit(downloader.friendly_error(log.text()))
        except Exception as exc:
            self.failed.emit(str(exc))

    def stop(self):
        self._stopped = True
        tools.kill_tree(self._proc)
