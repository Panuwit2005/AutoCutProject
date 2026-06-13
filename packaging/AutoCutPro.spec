# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for AutoCut Pro (onedir).

ffmpeg, the Whisper model and fonts are NOT packed in here — build.ps1 copies
them next to the exe after the build so they are easy to inspect/update and the
PyInstaller archive stays small.  Resolution of those resources is handled at
runtime by launcher.bundle_dir() / autocut.tools.bundle_dir().
"""

import os
from PyInstaller.utils.hooks import collect_all

PROJ = os.path.abspath(os.path.join(SPECPATH, ".."))

datas = [
    (os.path.join(PROJ, "index.html"), "."),
    (os.path.join(PROJ, "static"), "static"),
]
binaries = []
hiddenimports = ["waitress", "flask_cors",
                 # pywebview backends (native desktop window)
                 "webview", "webview.platforms.winforms",
                 "webview.platforms.edgechromium", "clr"]

# Native-extension packages that need their compiled libs/data collected.
# (v1.4 dropped the AI stack — no faster_whisper/ctranslate2/onnxruntime/av.)
for pkg in ("cryptography",):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

# Desktop shell (pywebview + pythonnet). Best-effort: if any of these can't be
# collected, the app falls back at runtime to Edge app-mode / browser, so a
# packaging gap here never bricks the build.
for pkg in ("webview", "pythonnet", "clr_loader"):
    try:
        d, b, h = collect_all(pkg)
        datas += d
        binaries += b
        hiddenimports += h
    except Exception as _e:  # noqa: BLE001
        print(f"[spec] optional package not collected: {pkg} ({_e})")

a = Analysis(
    [os.path.join(SPECPATH, "launcher.py")],
    pathex=[PROJ],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # NB: tkinter is intentionally NOT excluded — the native "choose folder"
    # dialog (autocut.folder_picker) needs it.
    excludes=["matplotlib", "torch", "torchaudio", "torchvision",
              "pandas", "scipy", "PyQt5", "PySide2", "notebook", "IPython",
              # AI stack removed in v1.4 — keep them out of the build
              "faster_whisper", "ctranslate2", "onnxruntime", "av",
              "tokenizers", "huggingface_hub", "numpy"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="AutoCutPro",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,   # windowed app — server runs hidden, logs go to a file
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=os.path.join(SPECPATH, "app.ico") if os.path.exists(
        os.path.join(SPECPATH, "app.ico")) else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="AutoCutPro",
)
