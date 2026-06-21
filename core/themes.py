"""
Реестр тем оформления.

Каждая тема — это:
  * "assets": имя папки в assets/Themes/<...> с иконками-глифами темы. Глифы
    перекрашиваются под цвет темы на лету (см. core/icons.py), поэтому новой теме
    своя папка не обязательна — берутся глифы темы по умолчанию.
  * "palette": словарь цветовых параметров интерфейса.

Чтобы добавить новую тему: добавить сюда запись THEMES["<Name>"] со своей
палитрой (все ключи как в _DEEP_OCEAN).
"""

# --- Deep Ocean ------------------------------------------------------------ #
_DEEP_OCEAN = {
    "grad_center":  "#415e87",
    "grad_edge":    "#2b3b57",
    "border":       "#3a5068",
    "bg":           "#1e2a3a",
    "page_bg":      "#35496b",

    "icon":         "#8a98af",
    "icon_hover":   "#c4d0e0",
    "separator":    "#8a98af",

    "title":        "#cdddf0",
    "text":         "#aebfd4",
    "muted":        "#7d93ad",
    "on_accent":    "#ffffff",   # текст на акцентных поверхностях (кнопка/пилюля)

    "accent":       "#3d6ea5",
    "accent_hover": "#4a7cb8",
    "link":         "#7fa8d8",
    "link_hover":   "#a9cdf5",
    "choose":       "#6c93da",
    "choose_bg":    "rgba(127, 168, 216, 0.10)",
    "choose_bg_h":  "rgba(127, 168, 216, 0.18)",

    "card_bg":      "#26344a",
    "field_bg":     "#26344a",

    "cb_off":       "#40597d",
    "cb_on":        "#3a77f0",

    "seg_bg":       "#26344a",
    "seg_sel":      "#3a77f0",

    "sel_chip":     "#39506f",
    "sel_chevron":  "#c4d0e0",

    "download_bg":       "#3a77f0",
    "download_bg_hover": "#4a83f5",
    "analyze_bg":        "#caa64a",
    "analyze_bg_hover":  "#d8b65e",
    "stop_bg":           "#c25b5b",
    "stop_bg_hover":     "#d06a6a",
    "disabled_bg":       "#34425c",
    "disabled_text":     "#7d93ad",

    "prog_track":   "#2c3a52",
    "ok":           "#7fd18a",
    "error":        "#e08a8a",
}

# --- Rose Negative (белая основа, чёрный текст, красные акценты) ------------ #
_ROSE_NEGATIVE = {
    "grad_center":  "#ffffff",
    "grad_edge":    "#f2f2f2",
    "border":       "#cfcfcf",
    "bg":           "#ffffff",
    "page_bg":      "#f5f5f5",

    "icon":         "#000000",
    "icon_hover":   "#555555",
    "separator":    "#d0d0d0",

    "title":        "#000000",
    "text":         "#1a1a1a",
    "muted":        "#6b6b6b",
    "on_accent":    "#ffffff",

    "accent":       "#e23b3b",
    "accent_hover": "#ef5151",
    "link":         "#d12f2f",
    "link_hover":   "#e84b4b",
    "choose":       "#d12f2f",
    "choose_bg":    "rgba(226, 59, 59, 0.10)",
    "choose_bg_h":  "rgba(226, 59, 59, 0.18)",

    "card_bg":      "#f2f2f2",
    "field_bg":     "#efefef",

    "cb_off":       "#cccccc",
    "cb_on":        "#e23b3b",

    "seg_bg":       "#e6e6e6",
    "seg_sel":      "#e23b3b",

    "sel_chip":     "#ececec",
    "sel_chevron":  "#000000",

    "download_bg":       "#e23b3b",
    "download_bg_hover": "#ef4d4d",
    "analyze_bg":        "#e08a3a",
    "analyze_bg_hover":  "#ee9a4a",
    "stop_bg":           "#9e2b2b",
    "stop_bg_hover":     "#b33636",
    "disabled_bg":       "#e0e0e0",
    "disabled_text":     "#9a9a9a",

    "prog_track":   "#dcdcdc",
    "ok":           "#2e9e4f",
    "error":        "#c0392b",
}

# --- Dark Pulse (чёрная основа, жёлтые рамка/текст/акценты) ----------------- #
_DARK_PULSE = {
    "grad_center":  "#161616",
    "grad_edge":    "#000000",
    "border":       "#f5d020",
    "bg":           "#000000",
    "page_bg":      "#111111",

    "icon":         "#f5d020",
    "icon_hover":   "#ffe24d",
    "separator":    "#8a7414",

    "title":        "#f5d020",
    "text":         "#e6c41d",
    "muted":        "#9a8420",
    "on_accent":    "#000000",   # чёрный текст на жёлтых поверхностях

    "accent":       "#f5d020",
    "accent_hover": "#ffe24d",
    "link":         "#f5d020",
    "link_hover":   "#ffe24d",
    "choose":       "#f5d020",
    "choose_bg":    "rgba(245, 208, 32, 0.10)",
    "choose_bg_h":  "rgba(245, 208, 32, 0.20)",

    "card_bg":      "#141414",
    "field_bg":     "#181818",

    "cb_off":       "#4a4216",
    "cb_on":        "#f5d020",

    "seg_bg":       "#181818",
    "seg_sel":      "#f5d020",

    "sel_chip":     "#1f1f1f",
    "sel_chevron":  "#f5d020",

    "download_bg":       "#f5d020",
    "download_bg_hover": "#ffe24d",
    "analyze_bg":        "#b8941a",
    "analyze_bg_hover":  "#d0a91e",
    "stop_bg":           "#c25b5b",
    "stop_bg_hover":     "#d06a6a",
    "disabled_bg":       "#1f1f1f",
    "disabled_text":     "#6b6b20",

    "prog_track":   "#2a2a2a",
    "ok":           "#8ad15a",
    "error":        "#e08a8a",
}

# --- Decadence (Звёздная ночь Ван Гога: глубокий синий + золотые звёзды) ---- #
_DECADENCE = {
    "grad_center":  "#1b3a6b",
    "grad_edge":    "#0a1430",
    "border":       "#e8d18a",
    "bg":           "#0e1a3a",
    "page_bg":      "#16264d",

    "icon":         "#cdd9f0",
    "icon_hover":   "#f0e6b0",
    "separator":    "#3a4f80",

    "title":        "#eef2ff",
    "text":         "#c3d0ec",
    "muted":        "#8497c0",
    "on_accent":    "#0e1a3a",

    "accent":       "#f0d878",
    "accent_hover": "#f7e79a",
    "link":         "#e8d18a",
    "link_hover":   "#f7e79a",
    "choose":       "#e8d18a",
    "choose_bg":    "rgba(240, 216, 120, 0.10)",
    "choose_bg_h":  "rgba(240, 216, 120, 0.20)",

    "card_bg":      "#16264d",
    "field_bg":     "#1a2c57",

    "cb_off":       "#2e406e",
    "cb_on":        "#f0d878",

    "seg_bg":       "#16264d",
    "seg_sel":      "#f0d878",

    "sel_chip":     "#22386b",
    "sel_chevron":  "#eef2ff",

    "download_bg":       "#f0d878",
    "download_bg_hover": "#f7e79a",
    "analyze_bg":        "#c79a3a",
    "analyze_bg_hover":  "#d8ad4e",
    "stop_bg":           "#c25b5b",
    "stop_bg_hover":     "#d06a6a",
    "disabled_bg":       "#1a2c57",
    "disabled_text":     "#5a6c96",

    "prog_track":   "#1a2c57",
    "ok":           "#8ad1a0",
    "error":        "#e08a8a",
}

# --- Crimson Forest (тёмно-зелёная основа + кремовый, акцент — багровый) ---- #
_CRIMSON_FOREST = {
    "grad_center":  "#1d3a26",
    "grad_edge":    "#0a160e",
    "border":       "#c0392b",
    "bg":           "#0f1f14",
    "page_bg":      "#15281b",

    "icon":         "#e8e0c8",
    "icon_hover":   "#f5efd8",
    "separator":    "#3a5a44",

    "title":        "#ede7cf",
    "text":         "#c9d6bf",
    "muted":        "#8aa089",
    "on_accent":    "#ffffff",

    "accent":       "#c0392b",
    "accent_hover": "#d4493a",
    "link":         "#d98a3a",
    "link_hover":   "#ec9a4a",
    "choose":       "#d98a3a",
    "choose_bg":    "rgba(217, 138, 58, 0.10)",
    "choose_bg_h":  "rgba(217, 138, 58, 0.20)",

    "card_bg":      "#15281b",
    "field_bg":     "#18301f",

    "cb_off":       "#2f4a36",
    "cb_on":        "#c0392b",

    "seg_bg":       "#18301f",
    "seg_sel":      "#c0392b",

    "sel_chip":     "#1f3a28",
    "sel_chevron":  "#ede7cf",

    "download_bg":       "#c0392b",
    "download_bg_hover": "#d4493a",
    "analyze_bg":        "#b8893a",
    "analyze_bg_hover":  "#ca9a4a",
    "stop_bg":           "#8e2b2b",
    "stop_bg_hover":     "#a23636",
    "disabled_bg":       "#18301f",
    "disabled_text":     "#5a6c52",

    "prog_track":   "#18301f",
    "ok":           "#7fd18a",
    "error":        "#e08a8a",
}

# --- Vibrancecore (тёмно-синяя основа, электрик-синий + сочный красный) ----- #
_VIBRANCECORE = {
    "grad_center":  "#12203a",
    "grad_edge":    "#060a16",
    "border":       "#2ea3e6",
    "bg":           "#0a1020",
    "page_bg":      "#101a30",

    "icon":         "#e0eaff",
    "icon_hover":   "#ffffff",
    "separator":    "#2a4a70",

    "title":        "#eaf2ff",
    "text":         "#b8c8e8",
    "muted":        "#7a8cb0",
    "on_accent":    "#ffffff",

    "accent":       "#2ea3e6",
    "accent_hover": "#4ab4f0",
    "link":         "#2ea3e6",
    "link_hover":   "#4ab4f0",
    "choose":       "#2ea3e6",
    "choose_bg":    "rgba(46, 163, 230, 0.10)",
    "choose_bg_h":  "rgba(46, 163, 230, 0.20)",

    "card_bg":      "#101a30",
    "field_bg":     "#14203a",

    "cb_off":       "#2a3a5e",
    "cb_on":        "#2ea3e6",

    "seg_bg":       "#14203a",
    "seg_sel":      "#2ea3e6",

    "sel_chip":     "#1a2a48",
    "sel_chevron":  "#eaf2ff",

    "download_bg":       "#ff4d4d",
    "download_bg_hover": "#ff6360",
    "analyze_bg":        "#e0a23a",
    "analyze_bg_hover":  "#eeb24e",
    "stop_bg":           "#c0392b",
    "stop_bg_hover":     "#d4493a",
    "disabled_bg":       "#14203a",
    "disabled_text":     "#5a6c90",

    "prog_track":   "#14203a",
    "ok":           "#5ad18a",
    "error":        "#ff6b6b",
}


THEMES = {
    "Deep Ocean":     {"assets": "Deep Ocean", "palette": _DEEP_OCEAN},
    "White Rose":     {"assets": "Deep Ocean", "palette": _ROSE_NEGATIVE},
    "Dark Pulse":     {"assets": "Deep Ocean", "palette": _DARK_PULSE},
    "Decadence":      {"assets": "Deep Ocean", "palette": _DECADENCE},
    "Crimson Forest": {"assets": "Deep Ocean", "palette": _CRIMSON_FOREST},
    "Vibrancecore":   {"assets": "Deep Ocean", "palette": _VIBRANCECORE},
}

DEFAULT_THEME = "Deep Ocean"


def palette(theme):
    """Палитра темы (с откатом на тему по умолчанию)."""
    entry = THEMES.get(theme) or THEMES[DEFAULT_THEME]
    return entry["palette"]


def assets_name(theme):
    """Имя папки ассетов темы внутри assets/Themes."""
    entry = THEMES.get(theme) or THEMES[DEFAULT_THEME]
    return entry["assets"]


def color(theme, key):
    """Один цвет из палитры темы."""
    pal = palette(theme)
    return pal.get(key) or _DEEP_OCEAN.get(key)
