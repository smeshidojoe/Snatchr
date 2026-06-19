# -*- mode: python ; coding: utf-8 -*-
# Рецепт сборки Snatchr в один exe:  pyinstaller Snatchr.spec
# (иконку assets/app.ico создать заранее — см. README/инструкцию).

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[('assets', 'assets')],   # шрифты/иконки/темы внутрь exe
    hiddenimports=[],
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
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
    strip=False,
    upx=True,
    console=False,             # без окна консоли (= --windowed/--noconsole)
    icon='assets/app.ico',     # иконка exe
    version='version_info.txt',# свойства файла (Product Name / Version)
)
