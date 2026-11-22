# -*- mode: python ; coding: utf-8 -*-
# Консольная сборка UCI-движка PyCaesar (без PyQt6).

a = Analysis(
    ['recovered_src\\uci.py'],
    pathex=['recovered_src'],
    binaries=[],
    datas=[('book.bin', '.'), ('syzygy', 'syzygy')],
    hiddenimports=[
        'chess', 'chess.polyglot', 'chess.syzygy',
        'engine', 'engine.search', 'engine.evaluation',
        'engine.engine', 'engine.strength', 'engine.tablebase', 'engine.fast_board', 'engine.fast_eval', 'engine._fast_core',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'PyQt6', 'PyQt6.QtWidgets', 'PyQt6.QtGui', 'PyQt6.QtCore',
        'PyQt6.QtSvg', 'tkinter', 'PIL', 'matplotlib', 'numpy',
    ],
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
    name='PyCaesarUCI',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['icon_app.ico'],
)
