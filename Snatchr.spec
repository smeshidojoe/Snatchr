# -*- mode: python ; coding: utf-8 -*-

import os


# --- версия --------------------------------------------------------------- #
# Единственный источник — core/constants.py. Отдельный version_info.txt держать
# незачем: он дублировал ту же строку и однажды уже разошёлся с реальностью —
# лежал в репозитории, исправно правился, а в сборку не подключался вовсе, и у
# exe были пустые поля версии.
import re as _re
_APP_VERSION = _re.search(r'APP_VERSION\s*=\s*"([^"]+)"',
                          open('core/constants.py', encoding='utf-8').read()).group(1)
_VER_TUPLE = tuple(int(x) for x in (_APP_VERSION.split('.') + ['0', '0', '0', '0'])[:4])

from PyInstaller.utils.win32.versioninfo import (
    VSVersionInfo, FixedFileInfo, StringFileInfo, StringTable, StringStruct,
    VarFileInfo, VarStruct)

_VERSION_RES = VSVersionInfo(
    ffi=FixedFileInfo(filevers=_VER_TUPLE, prodvers=_VER_TUPLE,
                      mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0),
    kids=[
        StringFileInfo([StringTable('040904B0', [
            StringStruct('CompanyName', 'SmeshidoJoe'),
            StringStruct('FileDescription', 'Snatchr — video downloader'),
            StringStruct('FileVersion', _APP_VERSION),
            StringStruct('InternalName', 'Snatchr'),
            StringStruct('LegalCopyright', '© 2026 SmeshidoJoe'),
            StringStruct('OriginalFilename', 'Snatchr.exe'),
            StringStruct('ProductName', 'Snatchr'),
            StringStruct('ProductVersion', _APP_VERSION),
        ])]),
        VarFileInfo([VarStruct('Translation', [1033, 1200])]),
    ])

# --- Ember ---------------------------------------------------------------- #
# Ставится в editable-режиме: обычного пути к нему в sys.path нет, он
# подключается через хук установщика. Анализатор PyInstaller такие пакеты не
# находит — и exe собирался БЕЗ Ember. Внешне это выглядело как «ссылка не
# поддерживается»: посты из одних фотографий уходили к yt-dlp, а тот отвечал
# «No video could be found in this tweet». Вычисляем путь к пакету сами.
import importlib.util as _ilu
_spec = _ilu.find_spec('ember')
_EMBER_PATH = [os.path.dirname(os.path.dirname(_spec.origin))] if _spec and _spec.origin else []

a = Analysis(
    ['main.py'],
    pathex=_EMBER_PATH,
    binaries=[],
    datas=[('assets', 'assets')],
    # certifi импортируется внутри функции (core.tools.ssl_context) — указываем
    # явно, чтобы связка корневых сертификатов гарантированно попала в сборку.
    # Без неё наши HTTPS-запросы падают там, где корни Windows устарели.
    hiddenimports=['certifi', 'ember'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='Snatchr',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['assets\\app.ico'],
    version=_VERSION_RES,      # см. _APP_VERSION выше — читается из constants.py
)
