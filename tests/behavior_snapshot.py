# -*- coding: utf-8 -*-
"""Глобальный слепок поведения программы.

Прогоняет решающие функции всего конвейера на широкой матрице входов и печатает
детерминированный отчёт. Гоняется в закоммиченной версии и в текущем дереве,
отчёты сравниваются построчно: любое расхождение = изменение поведения.

Сети и диска не касается — проверяются РЕШЕНИЯ (какая команда собирается, какие
варианты показываются в селекторе, куда пойдёт файл), а не сам факт скачивания.

    python global_diff.py <файл_отчёта>
"""
import io, sys, os, re, hashlib
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.abspath("."))

OUT = []


def head(t):
    OUT.append("")
    OUT.append("################ %s" % t)


def line(t):
    OUT.append(str(t))


def norm(x):
    """Убирает из строк всё, что меняется от запуска к запуску."""
    s = str(x)
    s = re.sub(r"[A-Za-z]:\\\\?[^\"'\s]*?[Tt]emp[^\"'\s]*", "<TEMP>", s)
    s = re.sub(r"snatchr_[A-Za-z0-9_]+", "<TMPNAME>", s)
    s = re.sub(r"JOB[0-9a-f-]{6,}", "<JOB>", s)
    s = re.sub(r"stream-\d{8}-\d{6}", "stream-<TS>", s)
    s = s.replace(os.path.expanduser("~"), "<HOME>")
    return s


# ------------------------------------------------------------------ #
URLS = [
    "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
    "https://youtu.be/dQw4w9WgXcQ",
    "https://www.youtube.com/playlist?list=PL1234567890",
    "https://www.youtube.com/@channelname",
    "https://www.youtube.com/watch?v=abc&list=PL999",
    "https://vimeo.com/123456789",
    "https://player.vimeo.com/video/123456789",
    "https://vimeo.com/channels/staffpicks/987654321",
    "https://www.instagram.com/p/CxYzAbC123/",
    "https://www.instagram.com/reel/CxYzAbC123/",
    "https://www.instagram.com/stories/user/123456/",
    "https://twitter.com/user/status/1234567890",
    "https://x.com/user/status/1234567890",
    "https://vk.com/video-123456_789012",
    "https://vk.com/wall-123_456",
    "https://www.twitch.tv/videos/1234567890",
    "https://www.twitch.tv/somestreamer",
    "https://www.tiktok.com/@user/video/7123456789",
    "https://soundcloud.com/artist/track-name",
    "https://soundcloud.com/artist/sets/album-name",
    "https://music.youtube.com/watch?v=abc123",
    "https://rutube.ru/video/abcdef123456/",
    "https://ok.ru/video/123456789",
    "https://www.reddit.com/r/videos/comments/abc/title/",
    "https://www.facebook.com/watch/?v=123456789",
    "https://example.com/some/file.mp4",
    "not a url at all",
    "",
]

OPTIONS = [
    {"label": "Best Quality", "fmt": "bv*+ba/b", "mp3": False},
    {"label": "Best Compatibility (MP4)", "fmt": "bv[vcodec^=avc1]+ba/b", "mp3": False},
    {"label": "Audio (MP3)", "fmt": "ba/b", "mp3": True},
    {"label": "1080p", "fmt": "137+140", "height": 1080},
    {"label": "720p", "fmt": "22", "height": 720},
    {"label": "Thumbnail", "thumbnail": True, "mp3": False},
    {"label": "Media (5)", "key": "gallery", "gallery": True},
    {"label": "Best Quality", "fmt": "bv*+ba/b", "section": "*10.5-75.25"},
    {"label": "Audio (MP3)", "fmt": "ba/b", "mp3": True, "section": "*0-30"},
    {"label": "1080p ember", "ember": True, "key": "video"},
]

SETTINGS_SET = [
    ("по умолчанию", {"download_dir": "D:/Downloads"}),
    ("куки firefox", {"download_dir": "D:/Downloads", "cookies_browser": "firefox"}),
    ("куки авто", {"download_dir": "D:/Downloads", "cookies_browser": "auto"}),
    ("лимит скорости", {"download_dir": "D:/Downloads", "speed_limit": "5"}),
    ("конвертация выкл", {"download_dir": "D:/Downloads", "convert_mp4": False}),
    ("конвертация вкл", {"download_dir": "D:/Downloads", "convert_mp4": True}),
]

# --- 1. Разбор ссылок ---------------------------------------------- #
head("1. Классификация ссылок")
from core import downloader as dl
from core import ember_dl, history

for u in URLS:
    try:
        row = (
            "yt=%s supported=%s playlist=%s channel=%s twitch=%s "
            "vimeo_alt=%s site=%s ember=%s ember_primary=%s "
            "collection=%s host=%s"
            % (dl.is_youtube(u), dl.is_supported_url(u), dl.is_playlist_url(u),
               dl.is_channel_url(u), dl.is_twitch(u), dl.vimeo_player_url(u),
               dl.link_site(u), ember_dl.can_handle(u), ember_dl.is_primary(u),
               ember_dl.is_collection(u), history.host_label(u)))
    except Exception as exc:
        row = "ИСКЛЮЧЕНИЕ %s: %s" % (type(exc).__name__, exc)
    line("%-58s %s" % (u[:58], norm(row)))

# --- 2. Команды yt-dlp --------------------------------------------- #
head("2. Аргументы yt-dlp (матрица ссылка x вариант x настройки)")
for sname, st in SETTINGS_SET:
    for u in URLS[:20]:
        for opt in OPTIONS:
            for cookies in (True, False):
                for imp in (False, True):
                    try:
                        args = dl.build_download_args(opt, u, st, "Ролик 🎬 Title",
                                                      "OUTDIR", cookies=cookies,
                                                      impersonate=imp)
                        val = " ".join(norm(a) for a in args)
                    except Exception as exc:
                        val = "ИСКЛЮЧЕНИЕ %s: %s" % (type(exc).__name__, exc)
                    key = "%s|%s|%s|c=%s|i=%s" % (sname, u[:34], opt["label"],
                                                  cookies, imp)
                    line("%s => %s" % (key, val))

# --- 3. Streamlink -------------------------------------------------- #
head("3. Аргументы streamlink")
for sname, st in SETTINGS_SET:
    for u in ("https://www.twitch.tv/videos/1", "https://www.twitch.tv/streamer"):
        try:
            a, out = dl.build_streamlink_args(u, st, "OUTDIR")
            line("%s|%s => %s || %s" % (sname, u, " ".join(norm(x) for x in a), norm(out)))
        except Exception as exc:
            line("%s|%s => ИСКЛЮЧЕНИЕ %s: %s" % (sname, u, type(exc).__name__, exc))

# --- 4. Куки -------------------------------------------------------- #
head("4. Куки")
for sname, st in SETTINGS_SET:
    for u in URLS[:14]:
        try:
            line("%s|%-40s have=%s args=%s fast=%s"
                 % (sname, u[:40], dl.have_cookies(st, u),
                    norm(dl.cookie_args(st, u)), norm(dl.fast_cookie_args(st, u))))
        except Exception as exc:
            line("%s|%s ИСКЛЮЧЕНИЕ %s: %s" % (sname, u, type(exc).__name__, exc))

# --- 5. Выбор форматов из info ------------------------------------- #
head("5. Селектор форматов (yt-dlp info)")


def fmt(fid, h, vcodec, acodec="none", ext="mp4", tbr=1000, fps=30):
    return {"format_id": fid, "height": h, "vcodec": vcodec, "acodec": acodec,
            "ext": ext, "tbr": tbr, "fps": fps, "url": "https://x/%s" % fid}


INFOS = [
    ("4k vp9+h264", {"title": "Ролик", "extractor_key": "Youtube", "duration": 300,
                     "formats": [fmt("313", 2160, "vp9"), fmt("299", 1080, "avc1"),
                                 fmt("137", 1080, "avc1"), fmt("136", 720, "avc1"),
                                 fmt("140", None, "none", "mp4a")]}),
    ("только 720", {"title": "Ролик", "extractor_key": "Youtube", "duration": 120,
                    "formats": [fmt("136", 720, "avc1"), fmt("140", None, "none", "mp4a")]}),
    ("av1 + vp9", {"title": "Ролик", "extractor_key": "Youtube",
                   "formats": [fmt("571", 2160, "av01"), fmt("313", 2160, "vp9"),
                               fmt("137", 1080, "avc1"), fmt("140", None, "none", "mp4a")]}),
    ("только аудио", {"title": "Трек", "extractor_key": "Soundcloud",
                      "formats": [fmt("http_mp3", None, "none", "mp3", "mp3")]}),
    ("без форматов", {"title": "Пусто", "formats": []}),
    ("низкие", {"title": "Мелкий", "formats": [fmt("160", 144, "avc1"),
                                               fmt("133", 240, "avc1"),
                                               fmt("140", None, "none", "mp4a")]}),
]
for name, info in INFOS:
    for sname, st in SETTINGS_SET[:3]:
        try:
            v = dl.video_formats(info, settings=st)
            a = dl.audio_formats(info)
            bp = dl.best_picks_vp9(info)
            line("%s|%s video=%s" % (name, sname, norm(v)))
            line("%s|%s audio=%s" % (name, sname, norm(a)))
            line("%s|%s vp9picks=%s" % (name, sname, norm(bp)))
        except Exception as exc:
            line("%s|%s ИСКЛЮЧЕНИЕ %s: %s" % (name, sname, type(exc).__name__, exc))

# --- 6. Ember: галереи, посты, селектор ----------------------------- #
head("6. Ember")
EMBER_INFOS = [
    ("одно видео", {"_ember_kind": "single", "_ember_count": 1,
                    "_ember_media_kinds": ["video"], "title": "Пост"}),
    ("карусель 5", {"_ember_kind": "gallery", "_ember_count": 5,
                    "_ember_media_kinds": ["photo"] * 5, "title": "Пост"}),
    ("одно фото", {"_ember_kind": "single", "_ember_count": 1,
                   "_ember_media_kinds": ["photo"], "title": "Пост"}),
    ("2 фото", {"_ember_count": 2, "_ember_media_kinds": ["photo", "photo"]}),
    ("видео+фото", {"_ember_count": 3,
                    "_ember_media_kinds": ["video", "photo", "photo"]}),
    ("merge", {"_ember_kind": "merge", "_ember_count": 1,
               "_ember_media_kinds": ["video"]}),
    ("gif", {"_ember_count": 1, "_ember_media_kinds": ["gif"]}),
    ("пусто", {}),
]
for name, info in EMBER_INFOS:
    for sname, st in SETTINGS_SET[:2]:
        try:
            line("%s|%s gallery=%s count=%s opts=%s"
                 % (name, sname, ember_dl.is_gallery(info),
                    ember_dl.media_count(info),
                    norm(ember_dl.format_options(info, st))))
        except Exception as exc:
            line("%s|%s ИСКЛЮЧЕНИЕ %s: %s" % (name, sname, type(exc).__name__, exc))

# --- 6b. Порядок строк селектора ------------------------------------ #
head("6b. Format Priority: порядок строк селектора")
from core import formats

HEIGHT_SETS = [
    [1080, 720, 640, 480, 320, 240, 144],
    [2160, 1440, 1080, 720, 480, 360],
    [720, 640, 360],
    [5000, 2160, 1080],
    [240, 144],
    [1080],
    [],
]
ORDERS = [
    ("по умолчанию", {}),
    ("480 наверх, обложка второй",
     {"format_order": ["best", "480_H.264", "thumbnail", "compat", "2160_VP9",
                       "1080_H.264", "720_H.264", "360_H.264"]}),
    ("обложка первой", {"format_order": ["thumbnail", "best", "compat",
                                         "1080_H.264", "720_H.264"]}),
    ("скрыты 720 и обложка", {"format_hidden": ["720_H.264", "thumbnail"]}),
    ("скрыто всё", {"format_hidden": list(formats.DEFAULT_ORDER)}),
]
EXTRA = [None, {"label": "HEVC", "key": "hevc_weird"},
         {"label": "Экзотика", "key": "странный_ключ"}]
for hs in HEIGHT_SETS:
    einfo = {"_ember_kind": "single", "_ember_count": 1,
             "_ember_media_kinds": ["video"], "_ember_heights": hs}
    for oname, ost in ORDERS:
        for extra in EXTRA:
            opts = ember_dl.format_options(einfo, ost)
            if extra:
                opts = opts + [extra]
            got = " / ".join(str(o.get("key")) for o in formats.apply(opts, ost))
            line("%-28s|%-26s|%s => %s"
                 % (hs, oname, (extra or {}).get("key", "-"), got))
# и на канонических ключах пути yt-dlp
for name, info in INFOS:
    for oname, ost in ORDERS:
        opts = dl.video_formats(info, settings=ost) + dl.audio_formats(info)
        got = " / ".join(str(o.get("key")) for o in formats.apply(opts, ost))
        line("ytdlp|%s|%s => %s" % (name, oname, got))

# --- 7. Пути, расширения, конвертация ------------------------------- #
head("7. Пути и решения о конвертации")
for sname, st in SETTINGS_SET:
    for u in URLS[:12]:
        for opt in OPTIONS:
            try:
                line("%s|%s|%s exp=%s ext=%s conv=%s sect=%s secs=%s hint=%s"
                     % (sname, u[:30], opt["label"],
                        norm(dl._expected_path(opt, u, st, "Ролик 🎬", "OUTDIR")),
                        dl._merge_ext(opt, u, st), dl.should_convert(opt, u, st),
                        dl._section_bounds(opt), dl._section_seconds(opt),
                        norm(dl.option_media_hint(opt, INFOS[0][1]))))
            except Exception as exc:
                line("%s|%s|%s ИСКЛЮЧЕНИЕ %s: %s"
                     % (sname, u[:30], opt["label"], type(exc).__name__, exc))

# --- 8. Разбор вывода yt-dlp ---------------------------------------- #
head("8. Разбор строк вывода")
LINES = [
    "[download]   3.4% of ~  12.34MiB at    1.23MiB/s ETA 00:12",
    "[download] 100% of 12.34MiB in 00:10",
    "[download] Destination: C:\\Temp\\job\\Ролик.f137.mp4",
    "[Merger] Merging formats into \"C:\\Temp\\job\\Ролик.mp4\"",
    "[ExtractAudio] Destination: C:\\Temp\\job\\Трек.mp3",
    "[VideoConvertor] Converting video from mkv to mp4",
    "frame= 1234 fps= 60 q=28.0 size=   12345kB time=00:00:41.00 bitrate=2000.0kbits/s",
    "ERROR: [youtube] abc: Video unavailable",
    "ERROR: unable to download video data: HTTP Error 403: Forbidden",
    "ERROR: Unable to extract cookies from browser: could not copy chrome cookie database",
    "ERROR: [vk] Failed to parse JSON",
    "ERROR: [instagram] Requested content is not available, rate-limit reached",
    "ERROR: [youtube] Sign in to confirm you're not a bot",
    "ERROR: [youtube] This video is private",
    "error opening input: Connection reset by peer",
    "ffmpeg exited with code 1",
]
for t in LINES:
    line("%-70s prog=%s dest=%s cookieerr=%s autherr=%s secterr=%s friendly=%s"
         % (t[:70], norm(dl.parse_progress(t)), norm(dl.parse_destination(t)),
            dl.is_cookie_error(t), dl.is_auth_error(t),
            dl._is_section_stream_error(t), norm(dl.friendly_error(t))))

# --- 9. История ------------------------------------------------------ #
head("9. История: подписи, площадки, пути")
for u in URLS:
    line("%-58s host=%s" % (u[:58], history.host_label(u)))
line("waveform_path=%s" % norm(history.waveform_path("abc123")))

# --- 10. Переводы ---------------------------------------------------- #
head("10. Переводы (все ключи, оба языка)")
from core import i18n
keys = sorted(getattr(i18n, "_RU", getattr(i18n, "RU", {})).keys())
for lang in ("English", "Русский"):
    i18n.set_language(lang)
    for k in keys:
        line("%s|%s => %s" % (lang, k, i18n.tr(k)))
line("всего ключей: %d" % len(keys))

# --- 11. Пиктограммы в подписях -------------------------------------- #
head("11. Вырезание пиктограмм")
from core import fonts
for t in ["Egon 🤍", "Video 🎬 title", "обычный текст", "只有中文", "a\u200db",
          "смешанное 🤍🎬 x", "", "❤ сердце", "→ стрелка"]:
    line("%r -> %r" % (t, fonts.plain(t)))

# --- 12. Скачивание: полный след run_job ----------------------------- #
head("12. Проверка целостности модулей")
import importlib
for m in ("core.downloader", "core.ember_dl", "core.history", "core.tools",
          "core.updater", "core.workers", "core.hls_cut", "core.convert",
          "core.trimmer", "core.logbook", "core.crashlog", "core.hotkey",
          "core.autostart", "core.themes", "core.fonts", "core.icons",
          "core.config", "core.i18n", "core.perflog", "core.cache",
          "core.formats", "ui.download_scheduler", "ui.spotlight_history",
          "ui.widgets", "ui.main_page", "ui.settings_page", "ui.anim"):
    try:
        mod = importlib.import_module(m)
        names = sorted(n for n in dir(mod) if not n.startswith("__"))
        line("%s: %d имён, sha=%s" % (m, len(names),
                                      hashlib.sha256("|".join(names).encode()).hexdigest()[:12]))
    except Exception as exc:
        line("%s: ИСКЛЮЧЕНИЕ %s: %s" % (m, type(exc).__name__, exc))

text = "\n".join(OUT) + "\n"
with open(sys.argv[1], "w", encoding="utf-8") as f:
    f.write(text)
print("строк отчёта:", len(OUT))
print("sha256:", hashlib.sha256(text.encode("utf-8")).hexdigest()[:16])
