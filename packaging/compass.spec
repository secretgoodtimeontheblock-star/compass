# -*- mode: python ; coding: utf-8 -*-
"""Сборка окна Compass: движок, интерфейс и справочники инструментов внутри exe."""

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

datas = [
    ("../src/compass/static", "static"),
    ("../src/compass/data", "data"),
]
datas += collect_data_files("certifi")

hidden = collect_submodules("uvicorn") + [
    m for m in collect_submodules("webview") if "android" not in m
] + [
    "ccxt.okx",
    "anthropic",
    "h11",
    "anyio",
    "websockets",
]

a = Analysis(
    ["../src/compass/desktop.py"],
    pathex=["../src"],
    binaries=[],
    datas=datas,
    hiddenimports=hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Compass",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="Compass",
)
