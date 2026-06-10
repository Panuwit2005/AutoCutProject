"""Signed, offline-safe in-app updates ("OTA patch").

Why this exists
---------------
The customer app is a frozen PyInstaller build where the *heavy* parts
(Whisper model ~460 MB, ffmpeg) almost never change, while the *code*
(``index.html``, ``static/``, ``app.py``, the ``autocut`` package) changes
often.  Rebuilding and re-shipping the whole 600 MB+ app for a one-line fix is
wasteful, so this module lets the app pull a **small, signed code patch** and
apply it as an *overlay* that the launcher loads ahead of the bundled code.

Security
--------
A patch is only applied if its Ed25519 signature verifies against the **same
public key** the licensing system already embeds (the admin holds the matching
private key — see ``packaging/keygen_gui.py`` → "เผยแพร่อัปเดต").  A customer or a
man-in-the-middle cannot forge a patch.  Extraction is zip-slip safe.

Layout of the overlay (under LOCALAPPDATA so the launcher can find it with the
stdlib only, independent of the chosen data dir)::

    %LOCALAPPDATA%/AutoCutPro/app_update/
        current/        <- active overlay (launcher loads code from here)
        staged/         <- freshly downloaded patch, promoted on next launch
        staged.ready    <- marker file: staged is complete (holds its version)
        quarantine/     <- overlays that failed to import (auto-disabled)

Both this module and ``launcher.py`` compute ``overlay_root()`` the same way.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import shutil
import tempfile
import time
import urllib.request
import zipfile

# Baked default — the GitHub repo's raw URL for the ``update/`` folder.  The dev
# can still override per-machine via config.json ``update_url`` or the
# AUTOCUT_UPDATE_URL env var.  raw.githubusercontent.com serves the latest pushed
# file, so updating = push the new update.json + zip to the repo's update/ folder.
DEFAULT_UPDATE_URL = "https://raw.githubusercontent.com/Panuwit2005/AutoCutProject/main/update"

# Files/dirs that make up a code patch (everything that is NOT a heavy asset).
CODE_INCLUDE = ["index.html", "app.py", "static", "autocut"]


# ---------------------------------------------------------------------------
# Locations
# ---------------------------------------------------------------------------
def overlay_root() -> str:
    base = (os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
            or tempfile.gettempdir())
    return os.path.join(base, "AutoCutPro", "app_update")


def update_base_url() -> str:
    """Where update.json + the zip live (no trailing slash)."""
    url = os.environ.get("AUTOCUT_UPDATE_URL")
    if not url:
        try:
            from . import storage
            url = storage.load_config().get("update_url")
        except Exception:  # noqa: BLE001
            url = None
    return (url or DEFAULT_UPDATE_URL).strip().rstrip("/")


# ---------------------------------------------------------------------------
# Version helpers
# ---------------------------------------------------------------------------
def current_version() -> str:
    """Version of the code that is actually running (overlay or bundled)."""
    try:
        from autocut import __version__
        return str(__version__)
    except Exception:  # noqa: BLE001
        return "0"


def _parse(v: str) -> list[int]:
    out: list[int] = []
    for part in str(v).split("."):
        digits = "".join(ch for ch in part if ch.isdigit())
        out.append(int(digits) if digits else 0)
    return out


def is_newer(candidate: str, base: str) -> bool:
    a, b = _parse(candidate), _parse(base)
    n = max(len(a), len(b))
    a += [0] * (n - len(a))
    b += [0] * (n - len(b))
    return a > b


# ---------------------------------------------------------------------------
# Customer side: check / download / verify / stage
# ---------------------------------------------------------------------------
_cache: dict | None = None
_cache_at: float = 0.0


def check(timeout: float = 6.0, ttl: float = 45.0) -> dict:
    """Return update status, lightly cached so the UI can poll freely.

    ``{enabled, current, latest?, available?, notes?, manifest?, base?, error?}``
    """
    global _cache, _cache_at
    if _cache and (time.time() - _cache_at) < ttl:
        return _cache

    base = update_base_url()
    if not base:
        _cache, _cache_at = {"enabled": False, "current": current_version()}, time.time()
        return _cache

    out: dict
    try:
        req = urllib.request.Request(base + "/update.json",
                                     headers={"Cache-Control": "no-cache"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            manifest = json.loads(r.read().decode("utf-8"))
        latest = str(manifest.get("version", ""))
        out = {
            "enabled": True,
            "current": current_version(),
            "latest": latest,
            "available": bool(latest) and is_newer(latest, current_version()),
            "notes": manifest.get("notes", ""),
            "manifest": manifest,
            "base": base,
        }
    except Exception as e:  # noqa: BLE001 — network/json errors are non-fatal
        out = {"enabled": True, "current": current_version(), "error": str(e)}

    _cache, _cache_at = out, time.time()
    return out


def _download(url: str, timeout: float) -> bytes:
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return r.read()


def _verify_signature(data: bytes, sig: bytes) -> None:
    """Raise if *sig* is not a valid admin signature over *data*."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    from . import licensing
    pub = Ed25519PublicKey.from_public_bytes(base64.b64decode(licensing._PUBLIC_KEY_B64))
    pub.verify(sig, data)  # raises cryptography.exceptions.InvalidSignature


def _safe_extract(zf: zipfile.ZipFile, dest: str) -> None:
    dest_abs = os.path.abspath(dest)
    for name in zf.namelist():
        target = os.path.abspath(os.path.join(dest, name))
        if target != dest_abs and not target.startswith(dest_abs + os.sep):
            raise ValueError("แพ็กเกจอัปเดตมีพาธไฟล์ไม่ปลอดภัย")
    zf.extractall(dest)


def stage(manifest: dict, base: str, timeout: float = 180.0) -> str:
    """Download, verify and unpack a patch into ``staged/``; mark it ready.

    The patch becomes active on the next launch (the launcher promotes it).
    Returns the staged version string.  Raises on any verification failure.
    """
    version = str(manifest["version"])
    zip_name = manifest.get("zip") or f"update-{version}.zip"
    data = _download(base + "/" + zip_name, timeout)

    want_sha = manifest.get("sha256")
    if want_sha and hashlib.sha256(data).hexdigest() != want_sha:
        raise ValueError("ไฟล์อัปเดตเสียหาย (sha256 ไม่ตรง)")
    _verify_signature(data, base64.b64decode(manifest["sig"]))

    root = overlay_root()
    staged = os.path.join(root, "staged")
    shutil.rmtree(staged, ignore_errors=True)
    os.makedirs(staged, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        _safe_extract(zf, staged)

    if not os.path.isfile(os.path.join(staged, "index.html")) \
            or not os.path.isfile(os.path.join(staged, "app.py")):
        shutil.rmtree(staged, ignore_errors=True)
        raise ValueError("แพ็กเกจอัปเดตไม่สมบูรณ์")

    with open(os.path.join(root, "staged.ready"), "w", encoding="utf-8") as f:
        f.write(version)
    return version


# ---------------------------------------------------------------------------
# Admin side: build a signed patch (used by the Keygen/Admin tool)
# ---------------------------------------------------------------------------
def build_package(src_dir: str, out_dir: str, version: str, notes: str, sign) -> dict:
    """Zip the code files under *src_dir*, sign them, and write update.json.

    *sign* is a callable ``bytes -> bytes`` (the admin's Ed25519 private key's
    ``.sign``).  Returns paths + size.  The packaged ``autocut/__init__.py`` has
    its ``__version__`` rewritten to *version* so the customer's version check
    always matches the manifest.
    """
    os.makedirs(out_dir, exist_ok=True)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for item in CODE_INCLUDE:
            p = os.path.join(src_dir, item)
            if os.path.isfile(p):
                zf.write(p, item)
            elif os.path.isdir(p):
                for dp, _dn, fns in os.walk(p):
                    if "__pycache__" in dp.split(os.sep):
                        continue
                    for fn in fns:
                        if fn.endswith((".pyc", ".pyo")):
                            continue
                        full = os.path.join(dp, fn)
                        rel = os.path.relpath(full, src_dir).replace(os.sep, "/")
                        if rel == "autocut/__init__.py":
                            zf.writestr(rel, _bump_version(full, version))
                        else:
                            zf.write(full, rel)
    data = buf.getvalue()
    signature = sign(data)
    manifest = {
        "version": version,
        "zip": f"update-{version}.zip",
        "sha256": hashlib.sha256(data).hexdigest(),
        "sig": base64.b64encode(signature).decode("ascii"),
        "notes": notes or "",
    }
    zip_path = os.path.join(out_dir, manifest["zip"])
    with open(zip_path, "wb") as f:
        f.write(data)
    with open(os.path.join(out_dir, "update.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    return {"zip": zip_path, "manifest": os.path.join(out_dir, "update.json"),
            "version": version, "size": len(data)}


def _bump_version(init_path: str, version: str) -> bytes:
    import re
    with open(init_path, encoding="utf-8") as f:
        text = f.read()
    new, n = re.subn(r'__version__\s*=\s*["\'][^"\']*["\']',
                     f'__version__ = "{version}"', text)
    if n == 0:
        new = text + f'\n__version__ = "{version}"\n'
    return new.encode("utf-8")
