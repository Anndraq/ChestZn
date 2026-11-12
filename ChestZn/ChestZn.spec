# -*- mode: python ; coding: utf-8 -*-
"""
ChestZn — PyInstaller Spec файл для сборки единого исполняемого файла ChestZn.exe
Сборка:
    pyinstaller ChestZn.spec
"""

import os
import sys
from pathlib import Path
from PyInstaller.utils.hooks import collect_all, collect_submodules

block_cipher = None

# Сбор всех необходимых модулей и плагинов PySide6 (стили, платформы, qwindows.dll)
pyside6_datas, pyside6_binaries, pyside6_hidden = collect_all('PySide6')

# Скрытые импорты для аппаратных библиотек и баз данных
hidden_imports = [
    'win32print',
    'win32api',
    'win32con',
    'serial',
    'serial.tools.list_ports',
    'pyodbc',
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
] + pyside6_hidden

# Проверяем наличие sqlcipher3
try:
    import sqlcipher3
    hidden_imports.append('sqlcipher3')
except ImportError:
    pass

datas = [
    # Если в проекте есть папки со стилями, шаблонами или документацией:
] + pyside6_datas

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

# Единый исполняемый EXE-файл (One-File) без черной консоли (--windowed)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='ChestZn',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,  # Оконный режим без консоли
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    uac_admin=False,  # Запуск от имени обычного пользователя (без вымогательства UAC)
    icon='app.ico',   # Фирменная иконка Dotmark (D-куб из точек DataMatrix)
)
