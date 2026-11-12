# -*- mode: python ; coding: utf-8 -*-
"""
Dotmark (ChestZn) — PyInstaller Spec файл для сборки единого исполняемого файла Dotmark.exe
Сборка:
    pyinstaller ChestZn.spec
"""

import os
import sys
from pathlib import Path
from PyInstaller.utils.hooks import collect_all

block_cipher = None

# Сбор всех необходимых модулей и плагинов PySide6 (стили, платформы, qwindows.dll)
pyside6_datas, pyside6_binaries, pyside6_hidden = collect_all('PySide6')

# Скрытые импорты для аппаратных библиотек, шифрования и баз данных
hidden_imports = [
    'win32print',
    'win32api',
    'win32con',
    'win32crypt',
    'serial',
    'serial.tools.list_ports',
    'pyodbc',
    'openpyxl',
    'sqlite3',
    'json',
    'socket',
    're',
    'ctypes',
    'logging',
    'pathlib',
    'datetime',
    'shutil',
    'tempfile',
    'concurrent.futures',
] + pyside6_hidden

for opt_mod in ('sqlcipher3', 'cryptography'):
    try:
        __import__(opt_mod)
        hidden_imports.append(opt_mod)
    except ImportError:
        pass

asset_files = []
for asset in ('app.ico', 'logo.png', 'brand_header.png', 'installer_banner.png'):
    if os.path.exists(asset):
        asset_files.append((asset, '.'))

datas = asset_files + pyside6_datas
binaries = [] + pyside6_binaries

a = Analysis(
    ['main.py'],
    pathex=['.'],
    binaries=binaries,
    datas=datas,
    hiddenimports=hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['tkinter', 'matplotlib', 'scipy', 'numpy', 'test', 'unittest'],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='Dotmark',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    uac_admin=False,
    icon='app.ico',
)
