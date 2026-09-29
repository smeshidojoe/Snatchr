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

# Собираем из глобального Python, где стоит много лишнего: без списка PyInstaller
# тащил в exe numpy (28 МБ), tkinter и прочее, чего Snatchr не импортирует. Две
# привязки Qt сразу он вообще не соберёт.
# НЕ исключать: QtMultimedia (проигрыватель обрезки), QtNetwork (от него зависит
# Qt6Multimedia.dll), cryptography и sqlite3 (Ember читает cookies браузера),
# curl_cffi и yt_dlp (Ember), PIL (картинка на странице About), pywin32 —
# win32gui/win32api/win32process.
_EXCLUDES = [
    'PyQt5', 'PyQt6', 'PySide2', 'shiboken2', 'qtpy', 'sip',
    'numpy', 'scipy', 'pandas', 'matplotlib', 'sympy', 'cv2',
    'tkinter', '_tkinter', 'IPython', 'notebook', 'jupyter_client', 'pytest',
    # MFC-обвязка pywin32: весит 7 МБ и подтягивается хуками, хотя нам нужны
    # только win32gui/win32api/win32process.
    'win32ui', 'win32uiole', 'dde', 'pywin', 'pythonwin', 'win32com',
    # Куски Qt, которых нет на экране.
    'PySide6.QtWebEngineCore', 'PySide6.QtWebEngineWidgets', 'PySide6.QtQml',
    'PySide6.QtQuick', 'PySide6.QtQuickWidgets', 'PySide6.Qt3DCore',
    'PySide6.QtCharts', 'PySide6.QtDataVisualization', 'PySide6.QtBluetooth',
    'PySide6.QtSql', 'PySide6.QtTest', 'PySide6.QtNetworkAuth', 'PySide6.QtPdf',
    'PySide6.QtSvg', 'PySide6.QtOpenGLWidgets', 'PySide6.QtDesigner',
]

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
    excludes=_EXCLUDES,
    noarchive=False,
    optimize=0,
)
# Библиотеки, которые приходят вслед за плагинами Qt и Pillow, а нам не нужны:
#   opengl32sw.dll     — программный OpenGL на 20 МБ для машин без драйвера;
#                        стекло рисуется без OpenGL, видео — через Direct3D;
#   Quick/Qml          — приходят с плагином экранной клавиатуры;
#   Pdf, Svg           — плагины форматов картинок, а у нас только PNG и ICO;
#   *-x64 OpenSSL, tls — шифрование для QtNetwork. Проигрыватель открывает
#                        только локальные файлы, а сеть у нас — питоновская со
#                        своим libcrypto-3.dll (без -x64), его не трогаем;
#   mfc140u            — MFC для win32ui (см. _EXCLUDES);
#   _avif              — AVIF в Pillow (8 МБ). Без модуля Pillow просто считает
#                        формат неподдерживаемым.
_DROP = ('opengl32sw.dll', 'qt6quick', 'qt6qml', 'qt6pdf', 'qt6svg',
         'qt6virtualkeyboard', 'platforminputcontexts', 'qdirect2d',
         '\\qpdf', '\\qsvg', 'libcrypto-3-x64', 'libssl-3-x64', '\\tls\\',
         'mfc140u', '\\_avif.')
a.binaries = [b for b in a.binaries if not any(d in b[0].lower() for d in _DROP)]
# Переводы стандартных диалогов Qt (7 МБ): QTranslator мы не ставим, диалог
# выбора папки и так приходит от Windows.
a.datas = [d for d in a.datas if '\\translations\\' not in d[0].lower()]

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
