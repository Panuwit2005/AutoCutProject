# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the ADMIN keygen GUI (single small windowed exe).

Only needs tkinter + cryptography (it imports autocut.licensing, which is light).
The private key is NOT bundled — it stays as admin_private_key.pem next to the exe.
"""

import os
from PyInstaller.utils.hooks import collect_all

PROJ = os.path.abspath(os.path.join(SPECPATH, ".."))

datas = []
binaries = []
hiddenimports = []
for pkg in ("cryptography",):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

a = Analysis(
    [os.path.join(SPECPATH, "keygen_gui.py")],
    pathex=[PROJ],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["faster_whisper", "ctranslate2", "onnxruntime", "av", "numpy",
              "torch", "matplotlib", "pandas", "scipy", "flask", "waitress",
              "webview", "pythonnet", "clr"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="AutoCutAdmin",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=os.path.join(SPECPATH, "admin.ico") if os.path.exists(
        os.path.join(SPECPATH, "admin.ico")) else None,
)
