"""
Общий планировщик параллельных загрузок для окна и Spotlight.

Держит активные воркеры и очередь ожидающих. «Managed»-задания (пакетные:
плейлист/мультиссылки) уважают лимит parallel_downloads и встают в очередь;
«unmanaged» (одиночные: Paste/Toast/ручная ссылка) стартуют сразу.

Владелец (host) хранит соответствие id → строка истории и обрабатывает сигналы
progress/finished/failed. Воркеры живут здесь — host их не касается.
"""

from PySide6.QtCore import QObject, Signal


class DownloadScheduler(QObject):
    progress = Signal(str, dict)      # dl_id, прогресс-словарь
    finished = Signal(str, str)       # dl_id, путь к файлу
    failed = Signal(str, str)         # dl_id, сообщение об ошибке

    def __init__(self, app, parent=None):
        super().__init__(parent)
        self.app = app
        self._active = {}             # dl_id -> worker
        self._queue = []              # [{id,option,url,title}] ожидающие (managed)
        self._managed = set()         # id, считающиеся в лимит параллельности

    def _limit(self):
        return max(1, min(3, int(self.app.settings.get("parallel_downloads", 2))))

    def _active_managed(self):
        return sum(1 for i in self._active if i in self._managed)

    # --- публичное ----------------------------------------------------- #
    def submit(self, dl_id, option, url, title=None, managed=False):
        """Ставит задание. managed=True — уважает лимит (иначе стартует сразу)."""
        job = {"id": dl_id, "option": option, "url": url, "title": title}
        if managed:
            self._managed.add(dl_id)
            if self._active_managed() >= self._limit():
                self._queue.append(job)
                return
        self._start(job)

    def cancel(self, dl_id):
        """Снимает задание: из очереди — просто убрать, активное — остановить."""
        for i, job in enumerate(self._queue):
            if job["id"] == dl_id:
                self._queue.pop(i)
                self._managed.discard(dl_id)
                return
        w = self._active.pop(dl_id, None)
        self._managed.discard(dl_id)
        if w is not None:
            try:
                w.stop()
            except Exception:
                pass
        self._pump()                  # освободился слот — тянем следующий

    def has_work(self):
        return bool(self._active) or bool(self._queue)

    def stop_all(self):
        self._queue.clear()
        for w in list(self._active.values()):
            try:
                w.stop()
                w.wait(1500)
            except Exception:
                pass
        self._active.clear()
        self._managed.clear()

    # --- внутреннее ---------------------------------------------------- #
    def _start(self, job):
        from core.workers import DownloadWorker
        dl_id = job["id"]
        w = DownloadWorker(job["option"], job["url"], self.app.settings,
                           job["title"], self)
        w.progress.connect(lambda p, i=dl_id: self.progress.emit(i, p))
        w.finished_ok.connect(lambda dest, i=dl_id: self._done(i, dest))
        w.failed.connect(lambda msg, i=dl_id: self._fail(i, msg))
        self._active[dl_id] = w
        w.start()

    def _done(self, dl_id, dest):
        was_managed = dl_id in self._managed
        self._active.pop(dl_id, None)
        self._managed.discard(dl_id)
        self.finished.emit(dl_id, dest)
        if was_managed:
            self._pump()

    def _fail(self, dl_id, msg):
        was_managed = dl_id in self._managed
        self._active.pop(dl_id, None)
        self._managed.discard(dl_id)
        self.failed.emit(dl_id, msg)
        if was_managed:
            self._pump()

    def _pump(self):
        while self._queue and self._active_managed() < self._limit():
            self._start(self._queue.pop(0))
