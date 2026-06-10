"""Pick a roomy, writable working directory and keep it tidy.

A large upload is spooled to disk by waitress *and* werkzeug, and the pipeline
then writes several intermediate files.  On a machine whose system drive is
nearly full this raises ``OSError: [Errno 28] No space left on device`` while the
request body is still being received — before any of our code runs.  So:

1. Route every temp file to a roomy, writable drive (never Program Files).  The
   customer can override the location; otherwise we auto-pick the drive with the
   most free space.
2. Clean up after ourselves: stale dirs from old runs, intermediates once a job
   is done, and the whole dir if a job fails.
3. Let callers check free space up front and refuse a job with a clear message
   instead of crashing.

The customer's chosen folder is remembered in ``config.json`` under a fixed,
always-writable per-user location (independent of where the data itself lives).
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import time

_DRIVE_FIXED = 3  # DRIVE_FIXED from the Win32 API


# ---------------------------------------------------------------------------
# Config (remembers the customer's chosen folder)
# ---------------------------------------------------------------------------
def _config_dir() -> str:
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") \
        or tempfile.gettempdir()
    return os.path.join(base, "AutoCutPro")


def config_path() -> str:
    return os.path.join(_config_dir(), "config.json")


def load_config() -> dict:
    try:
        with open(config_path(), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_config(cfg: dict) -> None:
    try:
        os.makedirs(_config_dir(), exist_ok=True)
        with open(config_path(), "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Disk helpers
# ---------------------------------------------------------------------------
def free_bytes(path: str) -> int:
    """Free bytes on the volume that holds *path* (0 if it can't be read)."""
    try:
        return shutil.disk_usage(path).free
    except OSError:
        return 0


def _fixed_drives() -> list[str]:
    if os.name != "nt":
        return ["/"]
    import ctypes
    import string

    get_type = ctypes.windll.kernel32.GetDriveTypeW
    drives = []
    for letter in string.ascii_uppercase:
        root = f"{letter}:\\"
        if os.path.exists(root) and get_type(root) == _DRIVE_FIXED:
            drives.append(root)
    return drives or ["C:\\"]


def _writable(path: str) -> bool:
    try:
        os.makedirs(path, exist_ok=True)
        probe = os.path.join(path, ".write_test")
        with open(probe, "w", encoding="ascii") as f:
            f.write("ok")
        os.remove(probe)
        return True
    except OSError:
        return False


def _auto_candidates() -> list[str]:
    """Auto-pick locations (most-free wins later), guaranteed-writable first."""
    out: list[str] = []
    local = os.environ.get("LOCALAPPDATA")
    if local:
        out.append(os.path.join(local, "AutoCutPro"))

    sysdrive = (os.environ.get("SystemDrive", "C:") + "\\").upper()
    for root in _fixed_drives():
        if root.upper() == sysdrive:
            continue  # system drive is already covered by LocalAppData
        out.append(os.path.join(root, "AutoCutPro"))

    out.append(os.path.join(tempfile.gettempdir(), "AutoCutPro"))
    seen, uniq = set(), []
    for p in out:
        key = os.path.normcase(os.path.abspath(p))
        if key not in seen:
            seen.add(key)
            uniq.append(p)
    return uniq


# ---------------------------------------------------------------------------
# Applying a location
# ---------------------------------------------------------------------------
def apply(data_dir: str) -> tuple[str, str]:
    """Route stdlib tempfile + child processes + upload spooling into *data_dir*.

    Returns ``(data_dir, tmp_dir)``.
    """
    tmp = os.path.join(data_dir, "tmp")
    os.makedirs(tmp, exist_ok=True)
    os.environ["TEMP"] = tmp
    os.environ["TMP"] = tmp
    os.environ["TMPDIR"] = tmp
    os.environ["AUTOCUT_DATA_DIR"] = data_dir
    tempfile.tempdir = tmp
    return data_dir, tmp


def setup() -> tuple[str, str]:
    """Choose where temp files live and route everything there.

    Priority: an explicit env override → the customer's saved folder → the
    roomiest writable auto location.  Idempotent.
    """
    override = os.environ.get("AUTOCUT_DATA_DIR")
    if override and _writable(override):
        return apply(override)

    chosen = load_config().get("data_dir")
    if chosen and _writable(chosen):
        return apply(chosen)

    writable = [c for c in _auto_candidates() if _writable(c)]
    if not writable:
        tmp = tempfile.gettempdir()
        return tmp, tmp
    return apply(max(writable, key=free_bytes))


def set_data_dir(path: str, *, min_free_gb: float = 1.0) -> dict:
    """Validate, remember and switch to a customer-chosen folder.

    Returns ``{"ok": bool, "error"?: str, ...info}``.
    """
    path = os.path.abspath(path.strip().strip('"'))
    if not _writable(path):
        return {"ok": False, "error": f"เขียนไฟล์ในโฟลเดอร์นี้ไม่ได้: {path}"}
    free_gb = free_bytes(path) / (1024 ** 3)
    if free_gb < min_free_gb:
        return {"ok": False,
                "error": f"พื้นที่ว่างไม่พอ (เหลือ {free_gb:.1f} GB) ที่ {path}"}
    cfg = load_config()
    cfg["data_dir"] = path
    save_config(cfg)
    apply(path)
    return info()


def reset_data_dir() -> dict:
    """Forget the customer's choice and go back to auto-selection."""
    cfg = load_config()
    cfg.pop("data_dir", None)
    save_config(cfg)
    os.environ.pop("AUTOCUT_DATA_DIR", None)
    setup()
    return info()


def info() -> dict:
    data_dir = os.environ.get("AUTOCUT_DATA_DIR") or tempfile.gettempdir()
    return {
        "ok": True,
        "data_dir": data_dir,
        "output_root": output_root(),
        "free_gb": round(free_bytes(data_dir) / (1024 ** 3), 1),
        "custom": bool(load_config().get("data_dir")),
    }


# ---------------------------------------------------------------------------
# Finished-project output (the customer's deliverables — NOT a temp/zip dir)
# ---------------------------------------------------------------------------
def output_root() -> str:
    """The visible folder where finished projects are saved.

    Lives inside the data dir but is *separate* from ``tmp`` so the customer's
    clips never end up buried in a temp directory.
    """
    data_dir = os.environ.get("AUTOCUT_DATA_DIR") or tempfile.gettempdir()
    root = os.path.join(data_dir, "AutoCut Output")
    try:
        os.makedirs(root, exist_ok=True)
    except OSError:
        pass
    return root


def new_project_dir(base_name: str) -> str:
    """Create and return ``<output_root>/<base_name> <DD-MM-YYYY HHMM>``.

    The timestamp keeps every run in its own tidy folder; a ``(2)`` suffix is
    added in the rare case two jobs finish in the same minute.
    """
    stamp = time.strftime("%d-%m-%Y %H%M")
    path = os.path.join(output_root(), f"{base_name} {stamp}")
    if os.path.exists(path):
        i = 2
        while os.path.exists(f"{path} ({i})"):
            i += 1
        path = f"{path} ({i})"
    os.makedirs(path, exist_ok=True)
    return path


# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------
def purge_stale(prefix: str = "autocut_", max_age_h: float = 6.0,
                root: str | None = None) -> int:
    """Delete leftover work dirs (``<prefix>*``) older than *max_age_h* hours."""
    root = root or tempfile.gettempdir()
    cutoff = time.time() - max_age_h * 3600
    removed = 0
    try:
        names = os.listdir(root)
    except OSError:
        return 0
    for name in names:
        if not name.startswith(prefix):
            continue
        p = os.path.join(root, name)
        try:
            if os.path.getmtime(p) < cutoff:
                shutil.rmtree(p, ignore_errors=True)
                removed += 1
        except OSError:
            pass
    return removed


def cleanup_dir(path: str, keep: str | None = None) -> None:
    """Remove everything under *path* except the file *keep* (kept for download)."""
    keep_abs = os.path.abspath(keep) if keep else None
    try:
        names = os.listdir(path)
    except OSError:
        return
    for name in names:
        p = os.path.join(path, name)
        if keep_abs and os.path.abspath(p) == keep_abs:
            continue
        try:
            if os.path.isdir(p):
                shutil.rmtree(p, ignore_errors=True)
            else:
                os.remove(p)
        except OSError:
            pass
