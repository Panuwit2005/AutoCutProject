"""ADMIN-ONLY activation key generator.

Keep ``admin_private_key.pem`` secret and OFFLINE — anyone with it can mint keys.

Usage:
    python packaging/keygen.py <MACHINE-ID> [--name "ชื่อร้าน/ลูกค้า"] [--exp YYYY-MM-DD]

Example:
    python packaging/keygen.py ABCD-EF12-GHIJ-KLMN --name "ร้านลุงโจ" --exp 2027-12-31

Paste the printed key to the customer; they enter it once in the app.
"""

from __future__ import annotations

import argparse
import os
import sys

# Console may default to a legacy code page that can't encode Thai names.
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))  # project root, so `autocut` imports

from cryptography.hazmat.primitives import serialization  # noqa: E402

from autocut import licensing  # noqa: E402

PRIV_PATH = os.path.join(HERE, "admin_private_key.pem")


def main() -> int:
    ap = argparse.ArgumentParser(description="AutoCut Pro activation key generator")
    ap.add_argument("machine_id", help="Machine ID shown in the customer's app")
    ap.add_argument("--name", default="", help="Owner / shop name (optional)")
    ap.add_argument("--exp", default="", help="Expiry YYYY-MM-DD (optional; blank = perpetual)")
    args = ap.parse_args()

    if not os.path.exists(PRIV_PATH):
        print(f"[!] private key not found: {PRIV_PATH}", file=sys.stderr)
        return 1
    with open(PRIV_PATH, "rb") as f:
        priv = serialization.load_pem_private_key(f.read(), password=None)

    mid = args.machine_id.strip().upper()
    sig = priv.sign(licensing.canonical(mid, args.name, args.exp))
    key = licensing.make_key(mid, args.name, args.exp, sig)

    print("=" * 60)
    print(f"  Machine ID : {mid}")
    print(f"  Owner      : {args.name or '-'}")
    print(f"  Expiry     : {args.exp or 'ไม่มีวันหมดอายุ'}")
    print("=" * 60)
    print("  ส่งคีย์นี้ให้ลูกค้า (คัดลอกทั้งบรรทัด):")
    print()
    print(key)
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
