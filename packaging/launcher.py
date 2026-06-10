"""Frozen entry point for AutoCut Pro — a single-window desktop app.

What this does:

1. Enforces a single running instance (no more multiple servers / stale tabs —
   that was the cause of the recurring "Failed to fetch").
2. Wires up a fully self-contained runtime *before* importing the app
   (bundled ffmpeg, offline Whisper model, Hugging Face offline).
3. Runs the web server hidden in a background thread (no console window).
4. Shows the UI in a real app window:
       pywebview (native)  →  Edge app-mode window  →  default browser
   each step is a fallback for the previous one, so a customer always gets a
   working window even if the native shell can't load.

``autocut.tools`` resolves ffmpeg at *import* time, so all environment variables
are set before ``from app import app`` runs.
"""

from __future__ import annotations

import importlib.abc
import importlib.util
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import webbrowser

WINDOW_TITLE = "AutoCut Pro"
WINDOW_W, WINDOW_H = 1180, 850
_mutex_handle = None  # keep the single-instance mutex alive for the process life


# ---------------------------------------------------------------------------
# Console-less safety: a windowed build has sys.stdout/stderr == None.
# ---------------------------------------------------------------------------
def _ensure_streams() -> None:
    for name, fd in (("stdout", 1), ("stderr", 2)):
        if getattr(sys, name, None) is None:
            try:
                stream = os.fdopen(fd, "w", encoding="utf-8", errors="replace")
            except OSError:
                stream = open(os.devnull, "w", encoding="utf-8")
            setattr(sys, name, stream)


def bundle_dir() -> str:
    """Folder that holds bundled resources (ffmpeg/, models/, fonts/)."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


# ---------------------------------------------------------------------------
# OTA code overlay — load an applied patch's code ahead of the frozen bundle.
#
# A frozen app loads `app` / `autocut` from its embedded archive via a meta-path
# importer.  To let a downloaded patch override that code, we register a finder
# at the FRONT of sys.meta_path that resolves those modules from the overlay
# directory when (and only when) the file exists there.  Heavy deps (flask,
# numpy, faster_whisper, …) are not in the overlay, so they keep loading from
# the bundle.  All of this is stdlib-only and must run BEFORE importing `app`.
# (Mirrors autocut.updater.overlay_root — kept duplicated so we don't import
# autocut before the overlay is wired up.)
# ---------------------------------------------------------------------------
def _overlay_root() -> str:
    base = (os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
            or tempfile.gettempdir())
    return os.path.join(base, "AutoCutPro", "app_update")


class _OverlayFinder(importlib.abc.MetaPathFinder):
    """Resolve `app` and `autocut[.*]` from the overlay dir if present there."""

    def __init__(self, root: str):
        self.root = root

    def _path_for(self, fullname: str):
        if fullname == "app":
            return os.path.join(self.root, "app.py"), False
        if fullname == "autocut":
            return os.path.join(self.root, "autocut", "__init__.py"), True
        if fullname.startswith("autocut."):
            sub = fullname.split(".", 1)[1].replace(".", os.sep)
            pkg_init = os.path.join(self.root, "autocut", sub, "__init__.py")
            if os.path.isfile(pkg_init):
                return pkg_init, True
            return os.path.join(self.root, "autocut", sub + ".py"), False
        return None, False

    def find_spec(self, fullname, path=None, target=None):  # noqa: ARG002
        p, is_pkg = self._path_for(fullname)
        if not p or not os.path.isfile(p):
            return None
        if is_pkg:
            return importlib.util.spec_from_file_location(
                fullname, p, submodule_search_locations=[os.path.dirname(p)])
        return importlib.util.spec_from_file_location(fullname, p)


def _promote_staged(root: str) -> None:
    """Make a freshly downloaded patch (staged/) the active one (current/)."""
    staged = os.path.join(root, "staged")
    ready = os.path.join(root, "staged.ready")
    current = os.path.join(root, "current")
    if not (os.path.isfile(ready) and os.path.isfile(os.path.join(staged, "index.html"))):
        return
    try:
        shutil.rmtree(current, ignore_errors=True)
        os.replace(staged, current)
        os.remove(ready)
    except OSError as e:
        print(f"[update] promote failed: {e}", flush=True)


def _activate_overlay() -> str | None:
    """Promote any staged patch, register the finder, return the overlay dir."""
    if not getattr(sys, "frozen", False):
        return None  # from source, code is read directly — no overlay
    root = _overlay_root()
    try:
        _promote_staged(root)
    except Exception as e:  # noqa: BLE001
        print(f"[update] promote error: {e}", flush=True)
    current = os.path.join(root, "current")
    if not (os.path.isfile(os.path.join(current, "index.html"))
            and os.path.isfile(os.path.join(current, "app.py"))):
        return None
    sys.meta_path.insert(0, _OverlayFinder(current))
    os.environ["AUTOCUT_CODE_DIR"] = current
    return current


def _quarantine_overlay(overlay: str) -> None:
    """A patch failed to import — disable it so the next launch is clean."""
    sys.meta_path[:] = [f for f in sys.meta_path if not isinstance(f, _OverlayFinder)]
    os.environ.pop("AUTOCUT_CODE_DIR", None)
    for name in [m for m in sys.modules if m == "app" or m == "autocut"
                 or m.startswith("autocut.")]:
        del sys.modules[name]
    try:
        q = os.path.join(_overlay_root(), "quarantine")
        os.makedirs(q, exist_ok=True)
        shutil.move(overlay, os.path.join(q, f"bad_{int(time.time())}"))
    except Exception as e:  # noqa: BLE001
        print(f"[update] quarantine failed: {e}", flush=True)


def _import_app():
    """Import the Flask app, preferring an applied patch but self-healing.

    If an overlay is active and its code can't be imported, the overlay is
    quarantined and we fall back to the known-good bundled code.
    """
    overlay = _activate_overlay()
    try:
        from app import app
        return app
    except Exception as e:  # noqa: BLE001
        if not overlay:
            raise
        print(f"[update] patched code failed to load ({e}); reverting", flush=True)
        _quarantine_overlay(overlay)
        from app import app
        return app


def _setup_environment() -> None:
    base = bundle_dir()
    exe = ".exe" if os.name == "nt" else ""

    ff_dir = os.path.join(base, "ffmpeg")
    fm = os.path.join(ff_dir, f"ffmpeg{exe}")
    fp = os.path.join(ff_dir, f"ffprobe{exe}")
    if os.path.isfile(fm):
        os.environ.setdefault("AUTOCUT_FFMPEG", fm)
    if os.path.isfile(fp):
        os.environ.setdefault("AUTOCUT_FFPROBE", fp)

    model_dir = os.path.join(base, "models", "faster-whisper-small")
    if os.path.isdir(model_dir):
        os.environ.setdefault("AUTOCUT_WHISPER_MODEL", model_dir)

    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
    os.environ.setdefault("AUTOCUT_LANGUAGE", "th")


def _free_port(preferred: int = 5000) -> int:
    for port in (preferred, 5001, 5002, 5050, 8080, 8000):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(("127.0.0.1", port)) != 0:
                return port
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_port(port: int, timeout: float = 20.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return True
        time.sleep(0.15)
    return False


# ---------------------------------------------------------------------------
# Single instance (Windows named mutex)
# ---------------------------------------------------------------------------
def _acquire_single_instance() -> bool:
    """Return True if we are the only instance; False if one is already running."""
    global _mutex_handle
    if os.name != "nt":
        return True
    import ctypes
    _mutex_handle = ctypes.windll.kernel32.CreateMutexW(None, False, "Global\\AutoCutPro_singleton")
    already = ctypes.windll.kernel32.GetLastError() == 183  # ERROR_ALREADY_EXISTS
    return not already


def _message_box(text: str, title: str = WINDOW_TITLE) -> None:
    if os.name == "nt":
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(0, text, title, 0x40)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# File logging (no console in a windowed build)
# ---------------------------------------------------------------------------
def _init_file_logging(data_dir: str) -> None:
    if not getattr(sys, "frozen", False):
        return
    try:
        d = os.path.join(data_dir, "logs")
        os.makedirs(d, exist_ok=True)
        f = open(os.path.join(d, "autocut.log"), "a", encoding="utf-8", errors="replace")
        sys.stdout = f
        sys.stderr = f
    except OSError:
        pass


# ---------------------------------------------------------------------------
# The server (hidden)
# ---------------------------------------------------------------------------
def _serve(app, port: int) -> None:
    try:
        from waitress import serve
        serve(app, host="127.0.0.1", port=port, threads=8,
              channel_timeout=3600, max_request_body_size=8 * 1024 ** 3,
              ident="AutoCutPro", _quiet=True)
    except ImportError:
        app.run(host="127.0.0.1", port=port, threaded=True)


# ---------------------------------------------------------------------------
# The window — native, else Edge app-mode, else browser
# ---------------------------------------------------------------------------
def _find_edge() -> str | None:
    import shutil
    for p in (
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    ):
        if os.path.isfile(p):
            return p
    return shutil.which("msedge")


def _open_window(url: str) -> str:
    """Show the UI. Returns the mode used; blocks until the window closes
    (except the 'browser' fallback, which returns immediately)."""
    # 1) Native window (pywebview)
    try:
        import webview
        webview.create_window(WINDOW_TITLE, url, width=WINDOW_W, height=WINDOW_H,
                              min_size=(900, 640))
        webview.start()
        return "native"
    except Exception as e:  # noqa: BLE001 - any failure → next fallback
        print(f"[window] pywebview unavailable: {e}", flush=True)

    # 2) Edge in app-mode (a clean, chrome-less window; Edge ships with Windows)
    edge = _find_edge()
    if edge:
        try:
            profile = os.path.join(
                os.environ.get("AUTOCUT_DATA_DIR", os.path.expanduser("~")), "edgeprofile")
            proc = subprocess.Popen([
                edge, f"--app={url}", f"--user-data-dir={profile}",
                f"--window-size={WINDOW_W},{WINDOW_H}", "--no-first-run",
                "--no-default-browser-check",
            ])
            proc.wait()
            return "edge"
        except Exception as e:  # noqa: BLE001
            print(f"[window] edge app-mode failed: {e}", flush=True)

    # 3) Plain browser (last resort)
    webbrowser.open(url)
    return "browser"


# ---------------------------------------------------------------------------
def main() -> None:
    _ensure_streams()

    if "--pick-folder" in sys.argv:
        from autocut import folder_picker
        print(folder_picker.pick() or "")
        sys.exit(0)

    if "--selftest" in sys.argv:
        sys.exit(_selftest())

    if not _acquire_single_instance():
        _message_box("AutoCut Pro กำลังเปิดอยู่แล้ว\n(โปรแกรมเปิดได้ครั้งละ 1 หน้าต่าง)")
        sys.exit(0)

    _setup_environment()

    # Load code from an applied OTA patch if present (else the bundled code).
    app = _import_app()
    from autocut import storage, tools, transcribe

    data_dir, _tmp = storage.setup()
    _init_file_logging(data_dir)

    port = int(os.environ.get("AUTOCUT_PORT", "0") or 0) or _free_port()
    url = f"http://127.0.0.1:{port}"

    st = tools.status()
    print("=" * 60, flush=True)
    print("  AutoCut Pro", flush=True)
    print(f"  ffmpeg : {'OK' if st.core_ok else 'NOT FOUND'}", flush=True)
    print(f"  whisper: {'offline-ready' if transcribe.available() else 'silence-only'}", flush=True)
    print(f"  data   : {data_dir}", flush=True)
    print(f"  url    : {url}", flush=True)
    print("=" * 60, flush=True)

    threading.Thread(target=_serve, args=(app, port), daemon=True).start()
    _wait_port(port, timeout=20)

    mode = _open_window(url)
    if mode == "browser":
        # No window to wait on — keep the (daemon) server alive until killed.
        while True:
            time.sleep(3600)


def _selftest() -> int:
    """Import each heavy dependency in isolation and report failures."""
    _setup_environment()
    import traceback
    mods = ["numpy", "av", "ctranslate2", "onnxruntime", "tokenizers",
            "huggingface_hub", "requests", "faster_whisper",
            "flask", "flask_cors", "waitress", "cryptography"]
    bad = 0
    for m in mods:
        try:
            __import__(m)
            print(f"[ok]   import {m}")
        except Exception:
            bad += 1
            print(f"[FAIL] import {m}")
            traceback.print_exc()
    try:
        from faster_whisper import WhisperModel
        md = os.environ.get("AUTOCUT_WHISPER_MODEL", "small")
        WhisperModel(md, device="cpu", compute_type="int8")
        print(f"[ok]   WhisperModel loaded from {md}")
    except Exception:
        bad += 1
        print("[FAIL] WhisperModel load")
        traceback.print_exc()
    print(f"SELFTEST {'PASS' if bad == 0 else 'FAIL (' + str(bad) + ')'}")
    return 1 if bad else 0


if __name__ == "__main__":
    main()
