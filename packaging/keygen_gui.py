"""ADMIN-ONLY desktop app for AutoCut.

Two tabs:
  • สร้างคีย์ (Activation keys) — paste a customer's Machine ID, mint a key.
  • เผยแพร่อัปเดต (Publish update) — build a SIGNED code patch that customer apps
    download and apply automatically (see autocut.updater).

Both sign with ``admin_private_key.pem``.  Keep this exe together with that key
and never give either to a customer — anyone with the private key can mint keys
*and* publish updates to every installed app.
"""

from __future__ import annotations

import os
import sys
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

# Make `autocut` importable when run from source (project root is one level up).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cryptography.hazmat.primitives import serialization  # noqa: E402

from autocut import licensing, updater  # noqa: E402

try:
    from autocut import __version__ as CURRENT_VERSION  # noqa: E402
except Exception:  # noqa: BLE001
    CURRENT_VERSION = "4.0"

FG, BG, BG2, ACC = "#eee", "#15151f", "#0e0e16", "#00E5A0"


def _here() -> str:
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def _project_root() -> str:
    # The repo root that holds the source + the update/ folder.
    # From source: parent of packaging/.  Frozen: AutoCutKeygen.exe ships in
    # <repo>\release\admin\, so the repo is two levels up from the exe folder.
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.dirname(_here()))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_private_key():
    for path in (os.path.join(_here(), "admin_private_key.pem"),
                 os.path.join(os.path.dirname(os.path.abspath(__file__)), "admin_private_key.pem")):
        if os.path.isfile(path):
            with open(path, "rb") as f:
                return serialization.load_pem_private_key(f.read(), password=None)
    return None


# ===========================================================================
#  Tab 1 — Activation keys
# ===========================================================================
class KeygenTab:
    def __init__(self, parent: tk.Widget, priv):
        self.priv = priv
        box = tk.Frame(parent, bg=BG, padx=26, pady=22)
        box.pack(fill="both", expand=True)

        tk.Label(box, text="🔑 สร้างคีย์เปิดใช้งาน", bg=BG, fg=FG,
                 font=("Segoe UI", 18, "bold")).pack(anchor="w")
        tk.Label(box, text="กรอกข้อมูลของลูกค้า แล้วกดสร้างคีย์", bg=BG, fg="#aaa",
                 font=("Segoe UI", 10)).pack(anchor="w", pady=(0, 16))

        self.mid = self._field(box, "รหัสเครื่องของลูกค้า (Machine ID) *")
        self.name = self._field(box, "ชื่อร้าน / ลูกค้า (ไม่บังคับ)")
        self.exp = self._field(box, "วันหมดอายุ (YYYY-MM-DD) — เว้นว่าง = ถาวร")

        # Quick rental presets — set the expiry to today + N days in one click.
        rent = tk.Frame(box, bg=BG)
        rent.pack(fill="x", pady=(10, 4))
        tk.Label(rent, text="📅 เช่าด่วน:", bg=BG, fg="#bbb",
                 font=("Segoe UI", 10)).pack(side="left", padx=(0, 10))
        for label, days in (("1 วัน", 1), ("7 วัน", 7), ("30 วัน", 30), ("♾ ถาวร", None)):
            tk.Button(rent, text=label, command=lambda d=days: self._set_rent(d),
                      bg="#10242f", fg=ACC, relief="flat", cursor="hand2",
                      activebackground="#16323f", activeforeground=ACC, bd=0,
                      font=("Segoe UI", 10, "bold"), padx=16, pady=7).pack(side="left", padx=4)

        tk.Button(box, text="สร้างคีย์", command=self.generate, bg=ACC, fg="#053",
                  font=("Segoe UI", 13, "bold"), relief="flat", cursor="hand2",
                  activebackground="#00c98d", pady=12).pack(fill="x", pady=(16, 10))

        tk.Label(box, text="คีย์ที่ได้ (ส่งให้ลูกค้า):", bg=BG, fg=FG,
                 font=("Segoe UI", 10, "bold")).pack(anchor="w")
        self.out = tk.Text(box, height=5, wrap="char", bg=BG2, fg=ACC,
                           insertbackground=FG, relief="flat", font=("Consolas", 10),
                           padx=10, pady=10)
        self.out.pack(fill="x", pady=(4, 8))

        self.copy_btn = tk.Button(box, text="📋 คัดลอกคีย์", command=self.copy,
                                  bg="#222", fg=ACC, relief="flat", cursor="hand2",
                                  font=("Segoe UI", 11), pady=8, state="disabled")
        self.copy_btn.pack(fill="x")

        # --- Kill switch: revoke / un-revoke a key by Machine ID -------------
        tk.Frame(box, bg="#2a2a3a", height=1).pack(fill="x", pady=(18, 8))
        tk.Label(box, text="🚫 ระงับ / ปลดระงับคีย์ (ใช้ Machine ID ช่องบนสุด)", bg=BG,
                 fg="#ddd", font=("Segoe UI", 10, "bold")).pack(anchor="w")
        kill = tk.Frame(box, bg=BG); kill.pack(fill="x", pady=(8, 0))
        tk.Button(kill, text="🚫 ระงับคีย์นี้ทันที", command=lambda: self._revoke(True),
                  bg="#3a1620", fg="#ff8c94", relief="flat", cursor="hand2", bd=0,
                  activebackground="#4a1c28", activeforeground="#ff8c94",
                  font=("Segoe UI", 10, "bold"), padx=16, pady=8).pack(side="left", padx=(0, 8))
        tk.Button(kill, text="✅ ปลดระงับ", command=lambda: self._revoke(False),
                  bg="#10242f", fg=ACC, relief="flat", cursor="hand2", bd=0,
                  activebackground="#16323f", activeforeground=ACC,
                  font=("Segoe UI", 10, "bold"), padx=16, pady=8).pack(side="left")

        self.status = tk.Label(box, text="", bg=BG, fg="#ff6b6b",
                               font=("Segoe UI", 10), wraplength=500, justify="left")
        self.status.pack(anchor="w", pady=(12, 0))
        if self.priv is None:
            self._err("ไม่พบไฟล์กุญแจ admin_private_key.pem (วางไว้โฟลเดอร์เดียวกับโปรแกรมนี้)")

    def _field(self, parent, label: str) -> tk.Entry:
        tk.Label(parent, text=label, bg=BG, fg="#ddd",
                 font=("Segoe UI", 10)).pack(anchor="w", pady=(8, 2))
        e = tk.Entry(parent, bg=BG2, fg="#eee", insertbackground="#eee",
                     relief="flat", font=("Segoe UI", 12))
        e.pack(fill="x", ipady=7)
        return e

    def _err(self, msg: str): self.status.config(fg="#ff6b6b", text="⚠️ " + msg)
    def _ok(self, msg: str): self.status.config(fg="#00E5A0", text="✓ " + msg)

    def _revoke(self, revoke: bool):
        """Add/remove the Machine ID from the signed revocation list (kill switch)."""
        if self.priv is None:
            self._err("ไม่มีกุญแจสำหรับเซ็นรายการระงับ"); return
        mid = self.mid.get().strip().upper()
        if not mid:
            self._err("กรอก Machine ID ของลูกค้าก่อน (ช่องบนสุด)"); return
        action = "ระงับ" if revoke else "ปลดระงับ"
        if not messagebox.askyesno(f"ยืนยันการ{action}",
                f"ยืนยัน{action}คีย์ของเครื่อง:\n{mid}\n\n"
                "⚠️ ต้อง Push ไฟล์ขึ้น GitHub ถึงจะมีผล (และมีผลตอนลูกค้าออนไลน์)"):
            return
        out = os.path.join(_project_root(), "update")
        ids = updater.read_revocation_ids(out)
        if revoke and mid not in ids:
            ids.append(mid)
        elif not revoke:
            ids = [x for x in ids if x != mid]
        try:
            updater.build_revocation(out, ids, self.priv.sign)
        except Exception as e:  # noqa: BLE001
            self._err(f"{action}ไม่สำเร็จ: {e}"); return
        self._ok(f"{action}เครื่อง {mid} แล้ว · รวม {len(ids)} เครื่องในรายการ — "
                 "กด Push ขึ้น GitHub เพื่อให้มีผล")

    def _set_rent(self, days):
        """Fill the expiry field with today + *days* (or clear it for lifetime)."""
        from datetime import date, timedelta
        self.exp.delete(0, "end")
        if days is None:
            self._ok("ตั้งเป็นถาวร (ไม่มีวันหมดอายุ)")
            return
        d = (date.today() + timedelta(days=days)).isoformat()
        self.exp.insert(0, d)
        self._ok(f"เช่า {days} วัน → หมดอายุวันที่ {d}")

    def generate(self):
        if self.priv is None:
            self._err("ไม่มีกุญแจสำหรับสร้างคีย์"); return
        mid = self.mid.get().strip().upper()
        name = self.name.get().strip()
        exp = self.exp.get().strip()
        if not mid:
            self._err("กรุณากรอกรหัสเครื่องของลูกค้า"); return
        if exp:
            import re
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", exp):
                self._err("วันหมดอายุต้องเป็นรูปแบบ YYYY-MM-DD"); return
        try:
            sig = self.priv.sign(licensing.canonical(mid, name, exp))
            key = licensing.make_key(mid, name, exp, sig)
        except Exception as e:  # noqa: BLE001
            self._err(f"สร้างคีย์ไม่สำเร็จ: {e}"); return
        self.out.delete("1.0", "end"); self.out.insert("1.0", key)
        self.copy_btn.config(state="normal")
        self._ok(f"สร้างคีย์สำเร็จสำหรับเครื่อง {mid}" + (f" (หมดอายุ {exp})" if exp else ""))

    def copy(self):
        key = self.out.get("1.0", "end").strip()
        if not key:
            return
        self.out.clipboard_clear(); self.out.clipboard_append(key); self.out.update()
        self._ok("คัดลอกคีย์แล้ว — วางส่งให้ลูกค้าได้เลย")


# ===========================================================================
#  Tab 2 — Publish update
# ===========================================================================
class PublishTab:
    def __init__(self, parent: tk.Widget, priv):
        self.priv = priv
        box = tk.Frame(parent, bg=BG, padx=26, pady=22)
        box.pack(fill="both", expand=True)

        tk.Label(box, text="🚀 เผยแพร่อัปเดต", bg=BG, fg=FG,
                 font=("Segoe UI", 18, "bold")).pack(anchor="w")
        tk.Label(box, text=f"เวอร์ชันที่ใช้อยู่ตอนนี้: {CURRENT_VERSION}  —  ตั้งเวอร์ชันใหม่ให้สูงกว่านี้",
                 bg=BG, fg="#aaa", font=("Segoe UI", 10)).pack(anchor="w", pady=(0, 14))

        self.src = self._path_field(box, "โฟลเดอร์ซอร์สโค้ด (โปรเจกต์)", _project_root(), self._pick_src)
        self.out = self._path_field(box, "โฟลเดอร์ที่จะเก็บไฟล์อัปเดต (push ขึ้น GitHub)",
                                    os.path.join(_project_root(), "update"), self._pick_out)
        self.ver = self._field(box, "เวอร์ชันใหม่ (เช่น 4.1)")
        self.notes = self._field(box, "บันทึกการแก้ไข (ไม่บังคับ)")

        tk.Button(box, text="สร้างแพ็กเกจอัปเดต (เซ็นลายเซ็น)", command=self.publish,
                  bg=ACC, fg="#053", font=("Segoe UI", 13, "bold"), relief="flat",
                  cursor="hand2", activebackground="#00c98d", pady=10).pack(fill="x", pady=(16, 10))

        self.result = tk.Text(box, height=6, wrap="word", bg=BG2, fg="#cfe",
                              insertbackground=FG, relief="flat", font=("Consolas", 9),
                              padx=10, pady=10)
        self.result.pack(fill="x", pady=(4, 6))

        self.status = tk.Label(box, text="", bg=BG, fg="#ff6b6b",
                               font=("Segoe UI", 10), wraplength=500, justify="left")
        self.status.pack(anchor="w", pady=(8, 0))
        if self.priv is None:
            self._err("ไม่พบไฟล์กุญแจ admin_private_key.pem")

    def _field(self, parent, label: str) -> tk.Entry:
        tk.Label(parent, text=label, bg=BG, fg="#ddd",
                 font=("Segoe UI", 10)).pack(anchor="w", pady=(8, 2))
        e = tk.Entry(parent, bg=BG2, fg="#eee", insertbackground="#eee",
                     relief="flat", font=("Segoe UI", 12))
        e.pack(fill="x", ipady=7)
        return e

    def _path_field(self, parent, label: str, default: str, picker) -> tk.Entry:
        tk.Label(parent, text=label, bg=BG, fg="#ddd",
                 font=("Segoe UI", 10)).pack(anchor="w", pady=(8, 2))
        row = tk.Frame(parent, bg=BG)
        row.pack(fill="x")
        e = tk.Entry(row, bg=BG2, fg="#eee", insertbackground="#eee",
                     relief="flat", font=("Segoe UI", 11))
        e.pack(side="left", fill="x", expand=True, ipady=6)
        e.insert(0, default)
        tk.Button(row, text="…", command=lambda: picker(e), bg="#222", fg=ACC,
                  relief="flat", cursor="hand2", font=("Segoe UI", 11), padx=10).pack(side="left", padx=(6, 0))
        return e

    def _pick_src(self, entry):
        d = filedialog.askdirectory(initialdir=entry.get() or _project_root())
        if d:
            entry.delete(0, "end"); entry.insert(0, d)

    def _pick_out(self, entry):
        d = filedialog.askdirectory(initialdir=entry.get() or _project_root())
        if d:
            entry.delete(0, "end"); entry.insert(0, d)

    def _err(self, msg: str): self.status.config(fg="#ff6b6b", text="⚠️ " + msg)
    def _ok(self, msg: str): self.status.config(fg="#00E5A0", text="✓ " + msg)

    def publish(self):
        if self.priv is None:
            self._err("ไม่มีกุญแจสำหรับเซ็นอัปเดต"); return
        src = self.src.get().strip()
        out = self.out.get().strip()
        ver = self.ver.get().strip()
        notes = self.notes.get().strip()
        if not ver:
            self._err("กรุณากรอกเวอร์ชันใหม่"); return
        if not updater.is_newer(ver, CURRENT_VERSION):
            if not messagebox.askyesno("ยืนยัน",
                    f"เวอร์ชัน {ver} ไม่สูงกว่า {CURRENT_VERSION} ลูกค้าจะไม่เห็นเป็นอัปเดต — สร้างต่อไหม?"):
                return
        if not os.path.isfile(os.path.join(src, "index.html")) \
                or not os.path.isfile(os.path.join(src, "app.py")):
            self._err("โฟลเดอร์ซอร์สไม่ถูกต้อง (ไม่พบ index.html / app.py)"); return
        try:
            res = updater.build_package(src, out, ver, notes, self.priv.sign)
        except Exception as e:  # noqa: BLE001
            self._err(f"สร้างแพ็กเกจไม่สำเร็จ: {e}"); return
        self.result.delete("1.0", "end")
        self.result.insert("1.0",
            f"สร้างสำเร็จ เวอร์ชัน {res['version']}  ({res['size']/1024:.0f} KB)\n\n"
            f"ไฟล์ที่ต้องอัปโหลดขึ้นที่เก็บอัปเดต:\n"
            f"  • {os.path.basename(res['manifest'])}\n"
            f"  • {os.path.basename(res['zip'])}\n\n"
            f"โฟลเดอร์: {out}\n"
            f"อัปโหลดทั้งสองไฟล์ไปไว้ที่ URL อัปเดต แล้วลูกค้าจะเห็นอัปเดตอัตโนมัติ")
        self._ok(f"สร้างแพ็กเกจอัปเดต {res['version']} สำเร็จ")


# ===========================================================================
def main() -> None:
    root = tk.Tk()
    root.title("AutoCut Admin — สร้างคีย์ & อัปเดต")
    root.geometry("620x720")
    root.configure(bg=BG)
    try:
        ttk.Style().theme_use("clam")
    except tk.TclError:
        pass

    priv = _load_private_key()
    nb = ttk.Notebook(root)
    nb.pack(fill="both", expand=True)

    tab1 = tk.Frame(nb, bg=BG)
    tab2 = tk.Frame(nb, bg=BG)
    nb.add(tab1, text="  สร้างคีย์  ")
    nb.add(tab2, text="  เผยแพร่อัปเดต  ")
    KeygenTab(tab1, priv)
    PublishTab(tab2, priv)

    root.mainloop()


if __name__ == "__main__":
    main()
