# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all

datas_webview, binaries_webview, hidden_webview = [], [], []
datas_pyside, binaries_pyside, hidden_pyside = collect_all('PySide6')
datas_treepoem, binaries_treepoem, hidden_treepoem = collect_all('treepoem')

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=binaries_pyside + binaries_treepoem,
    datas=datas_pyside + datas_treepoem,
    hiddenimports=hidden_pyside + hidden_treepoem + [
        'PIL', 'PIL.Image',
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=[
        # Урезаем Qt — экономим десятки мегабайт
        'PySide6.QtWebEngineCore',
        'PySide6.QtWebEngineWidgets',
        'PySide6.QtQuick',
        'PySide6.QtQml',
        'PySide6.Qt3DCore',
        'PySide6.QtMultimedia',
        'PySide6.QtCharts',
        'PySide6.QtDataVisualization',
        'PySide6.QtBluetooth',
        'PySide6.QtNetworkAuth',
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='ChestZn',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    # icon='static/icon.ico',
)