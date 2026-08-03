# -*- coding: utf-8 -*-
"""Эталонные рендеры строки истории во всех состояниях.

Запуск:  python render_rows.py <папка>
Кладёт PNG на каждое состояние и rows.sha256 с хешами. После рефакторинга
запускается повторно в другую папку и сравнивается — попиксельно.
"""
import io, sys, os, hashlib
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.abspath("."))

from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QImage
qa = QApplication([])
import app as appmod
from core import themes

out_dir = sys.argv[1]
os.makedirs(out_dir, exist_ok=True)

ENTRY = {"id": "ref", "url": "https://example.com/watch?v=abcdef",
         "title": "Пример заголовка ролика — довольно длинный, чтобы обрезался",
         "uploader": "Автор Ролика", "host": "example.com",
         "height": 1080, "fps": 60, "duration": 754}
GALLERY = dict(ENTRY, id="gal", is_gallery=True, media_count=5, thumbs=[])

W = 452
hashes = []


def shot(name, setup, entry=None, theme="Glass"):
    w = appmod.App()
    w.settings["theme"] = theme
    hl = w.main_page.history
    hl.drop_missing = lambda: []
    row = hl._make_row(dict(entry or ENTRY))
    row.resize(W, row._h)
    setup(row)
    img = QImage(W, row._h, QImage.Format_ARGB32)
    img.fill(0)
    row.render(img)
    path = os.path.join(out_dir, "%s.png" % name)
    img.save(path)
    with open(path, "rb") as f:
        h = hashlib.sha256(f.read()).hexdigest()[:16]
    hashes.append("%s  %s" % (h, name))
    print("%s  %s" % (h, name))
    w.deleteLater()


def st(state=None, **attrs):
    def apply(row):
        if state:
            row._state = state
            if hasattr(row, "_apply_state"):
                row._apply_state()
        for k, v in attrs.items():
            setattr(row, k, v)
    return apply


CASES = [
    ("normal",           st()),
    ("normal_alpha50",   st(_alpha=0.5)),
    ("hover",            st(_hover_t=1.0)),
    ("hover_half",       st(_hover_t=0.45)),
    ("pending",          st("pending")),
    ("fetching",         st("fetching")),
    ("error",            st("error")),
    ("downloading_30",   st("downloading", _dl_t=1.0, _draw_frac=0.3, _frac=0.3,
                            _dl={"percent_str": "30%", "speed": "3.1MiB/s",
                                 "eta": "00:42", "size": "120MiB"})),
    ("downloading_mid",  st("downloading", _dl_t=0.5, _draw_frac=0.7, _frac=0.7,
                            _dl={"percent_str": "70%"})),
    ("transition",       st(_transition_t=0.5)),
    ("pulse",            st(_pulse_t=0.25)),
    ("err_overlay",      st(_err_t=1.0, _err_text="Couldn't delete")),
    ("err_overlay_half", st(_err_t=0.5, _err_text="Couldn't delete")),
]

for name, setup in CASES:
    shot(name, setup)

# Ветки обложки: реальная картинка, янтарная рамка изображения, размытие ошибки.
from PySide6.QtGui import QPixmap, QColor, QPainter as _QP


def _fake_pm():
    pm = QPixmap(640, 360)
    pm.fill(QColor("#3a5f8a"))
    q = _QP(pm)
    q.fillRect(0, 0, 320, 360, QColor("#8a5f3a"))
    q.end()
    return pm


def with_pm(extra=None):
    def apply(row):
        row._pm = _fake_pm()
        row._fit_cache = {}
        for k, v in (extra or {}).items():
            setattr(row, k, v)
    return apply


shot("thumb_real", with_pm())
shot("thumb_real_blur", with_pm({"_err_t": 1.0, "_err_text": "Ошибка"}))
shot("thumb_image_border", with_pm(), dict(ENTRY, id="img", is_image=True))


def gal_pms(row):
    row._gallery_pms = [_fake_pm(), _fake_pm(), _fake_pm()]
    row._fit_cache = {}


shot("gallery_three", gal_pms, GALLERY)


def gal_one(row):
    row._gallery_pms = [_fake_pm()]
    row._fit_cache = {}


shot("gallery_one", gal_one, GALLERY)
shot("gallery", st(), GALLERY)
shot("gallery_err", st(_err_t=1.0, _err_text="Ошибка"), GALLERY)
for theme in [t for t in themes.THEMES if t != "Glass"][:3]:
    shot("theme_%s" % theme.replace(" ", "_"), st(), theme=theme)
    shot("theme_%s_hover" % theme.replace(" ", "_"), st(_hover_t=1.0), theme=theme)

with open(os.path.join(out_dir, "rows.sha256"), "w", encoding="utf-8") as f:
    f.write("\n".join(hashes) + "\n")
print("\nвсего кадров:", len(hashes), "->", out_dir)
