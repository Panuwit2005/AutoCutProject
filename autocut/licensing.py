"""Offline, machine-locked activation.

How it works (no internet required on either side):

1. The app shows the customer a short **Machine ID** derived from their PC.
2. The customer sends that ID to the admin (Line / phone / email).
3. The admin runs ``packaging/keygen.py`` (which holds the secret private key)
   to produce an **activation key** bound to that exact Machine ID.
4. The customer pastes the key once; the app verifies it with the *public* key
   embedded below and stores it.  From then on it runs fully offline.

Security model: the key is an Ed25519 signature over the Machine ID (+ optional
owner name and expiry).  The app only carries the **public** key, so it can
*verify* but never *generate* keys — a customer cannot forge or share a working
key, and a key made for one PC fails on any other (different Machine ID).
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import platform

# Public verification key (admin keeps the matching private key offline).
_PUBLIC_KEY_B64 = "bJSuE1OU1rPAB/LTnuQdXhqUVJyeBTHAP4Iit/evgeg="

_cache: dict | None = None


# ---------------------------------------------------------------------------
# Machine fingerprint
# ---------------------------------------------------------------------------
def _raw_fingerprint() -> str:
    parts: list[str] = []
    if os.name == "nt":
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                                r"SOFTWARE\Microsoft\Cryptography") as k:
                parts.append(str(winreg.QueryValueEx(k, "MachineGuid")[0]))
        except OSError:
            pass
    parts.append(platform.node())
    if not any(parts):
        import uuid
        parts.append(str(uuid.getnode()))
    return "|".join(p for p in parts if p)


def machine_id() -> str:
    """A short, stable, human-friendly code for this PC (e.g. ABCD-EF12-…)."""
    digest = hashlib.sha256(_raw_fingerprint().encode("utf-8")).digest()
    b32 = base64.b32encode(digest).decode("ascii").rstrip("=")[:16]
    return "-".join(b32[i:i + 4] for i in range(0, 16, 4))


# ---------------------------------------------------------------------------
# Key encoding helpers
# ---------------------------------------------------------------------------
def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _b64url_decode(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


def canonical(mid: str, name: str, exp: str) -> bytes:
    """The exact bytes that get signed — admin and app must agree on this."""
    return f"{mid}|{name}|{exp}".encode("utf-8")


def make_key(mid: str, name: str, exp: str, signature: bytes) -> str:
    payload = {"mid": mid, "name": name, "exp": exp,
               "sig": base64.b64encode(signature).decode("ascii")}
    return _b64url_encode(json.dumps(payload, separators=(",", ":")).encode("utf-8"))


# ---------------------------------------------------------------------------
# License file storage
# ---------------------------------------------------------------------------
def _license_path() -> str:
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") or os.path.expanduser("~")
    d = os.path.join(base, "AutoCutPro")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, "license.key")


def _load_saved_key() -> str | None:
    try:
        with open(_license_path(), encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return None


def _save_key(key: str) -> None:
    with open(_license_path(), "w", encoding="utf-8") as f:
        f.write(key.strip())


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------
def verify(key: str) -> dict:
    """Validate *key* against this machine. Returns {ok, error?, name, exp}."""
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    try:
        payload = json.loads(_b64url_decode(key.strip()).decode("utf-8"))
        mid = str(payload["mid"])
        name = str(payload.get("name", ""))
        exp = str(payload.get("exp", ""))
        sig = base64.b64decode(payload["sig"])
    except (ValueError, KeyError, TypeError):
        return {"ok": False, "error": "คีย์ไม่ถูกต้อง (รูปแบบผิด)"}

    if mid != machine_id():
        return {"ok": False, "error": "คีย์นี้ไม่ตรงกับเครื่องนี้ — ใช้ได้เฉพาะเครื่องที่ขอคีย์"}

    pub = Ed25519PublicKey.from_public_bytes(base64.b64decode(_PUBLIC_KEY_B64))
    try:
        pub.verify(sig, canonical(mid, name, exp))
    except InvalidSignature:
        return {"ok": False, "error": "คีย์ไม่ถูกต้อง (ลายเซ็นไม่ผ่าน)"}

    if exp:
        from datetime import date
        try:
            y, m, d = (int(x) for x in exp.split("-"))
            if date.today() > date(y, m, d):
                return {"ok": False, "error": f"คีย์หมดอายุแล้ว ({exp})"}
        except ValueError:
            pass

    return {"ok": True, "name": name, "exp": exp}


# ---------------------------------------------------------------------------
# Public API used by the app
# ---------------------------------------------------------------------------
def is_activated() -> bool:
    global _cache
    if _cache and _cache.get("ok"):
        return True
    key = _load_saved_key()
    if not key:
        return False
    res = verify(key)
    _cache = res if res.get("ok") else None
    return bool(res.get("ok"))


def activate(key: str) -> dict:
    """Verify and persist *key*. Returns the same dict as :func:`status`."""
    global _cache
    res = verify(key)
    if not res.get("ok"):
        return {"activated": False, "machine_id": machine_id(), "error": res.get("error")}
    _save_key(key)
    _cache = res
    return status()


def status() -> dict:
    activated = is_activated()
    out = {"activated": activated, "machine_id": machine_id()}
    if activated and _cache:
        out["name"] = _cache.get("name", "")
        out["expiry"] = _cache.get("exp", "")
    return out
