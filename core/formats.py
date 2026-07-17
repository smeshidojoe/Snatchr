"""
Приоритет форматов: какие строки показывать в селекторе качества и в каком
порядке. Настраивается на странице Format Priority (Settings -> Format Priority).

Канонический набор строк фиксирован (он же — список на странице настройки), а
реальный селектор строится динамически из info ролика: каждой опции проставляется
`key`, по нему применяются видимость и порядок.

Ключи:
  best        — Best Quality
  compat      — Best Compatibility (AVC)
  <h>_<codec> — строка разрешения, напр. «2160_VP9», «1080_H.264»
  thumbnail   — обложка
"""

from core.i18n import tr

# Порядок по умолчанию = порядок показа в селекторе (сверху вниз).
DEFAULT_ORDER = [
    "best",
    "compat",
    "4320_VP9",
    "2880_VP9",
    "2160_VP9",
    "1440_VP9",
    "1080_H.264",
    "720_H.264",
    "480_H.264",
    "360_H.264",
    "thumbnail",
]

# Подписи для страницы настройки (в селекторе подписи свои — с битрейтом и т.п.).
_LABELS = {
    "best": "Best Quality",
    "compat": "Best Compatibility (H.264)",
    "4320_VP9": "8K · VP9",
    "2880_VP9": "5K · VP9",
    "2160_VP9": "4K · VP9",
    "1440_VP9": "1440p · VP9",
    "1080_H.264": "1080p · H.264",
    "720_H.264": "720p · H.264",
    "480_H.264": "480p · H.264",
    "360_H.264": "360p · H.264",
    "thumbnail": "Thumbnail",
}


def label_for(key):
    return tr(_LABELS.get(key, key))


def res_key(height, codec_label):
    """Ключ строки разрешения: («2160», «VP9») -> «2160_VP9»."""
    return "%d_%s" % (int(height or 0), codec_label or "?")


def order(settings):
    """Порядок ключей из настроек; лишние — прочь, недостающие — на своё место.

    Новый ключ (появился с обновлением, напр. 8K/5K) вставляем сразу за его
    ближайшим предшественником по DEFAULT_ORDER, а не в хвост: иначе у тех, у
    кого порядок уже сохранён, свежие строки падали бы в самый низ списка."""
    saved = [k for k in ((settings or {}).get("format_order") or [])
             if k in DEFAULT_ORDER]
    out = list(dict.fromkeys(saved))          # без дублей, порядок сохранён
    for i, key in enumerate(DEFAULT_ORDER):
        if key in out:
            continue
        pos = 0
        for prev in reversed(DEFAULT_ORDER[:i]):
            if prev in out:
                pos = out.index(prev) + 1
                break
        out.insert(pos, key)
    return out


def hidden(settings):
    """Множество скрытых ключей."""
    return set((settings or {}).get("format_hidden") or [])


def apply(options, settings):
    """Фильтрует по видимости и сортирует опции селектора по настроенному порядку.

    Ключи вне канонического набора (HEVC, 8K, экзотика прочих сайтов) не трогаем:
    их не скрываем и оставляем в хвосте, сохраняя исходный порядок. Если настройки
    скрыли вообще всё — возвращаем исходный список (пустой селектор бесполезен)."""
    idx = {k: i for i, k in enumerate(order(settings))}
    hid = hidden(settings)
    kept = [o for o in options if o.get("key") not in hid]
    if not kept:
        return list(options)
    # sort стабилен -> неизвестные ключи (idx по умолчанию в конце) держат порядок.
    kept.sort(key=lambda o: idx.get(o.get("key"), len(idx)))
    return kept
