"""Native folder chooser, run in its own process.

A Tk dialog must own the main thread, which the Flask/waitress worker threads
don't have — so the server shells out to a fresh process that runs *only* this
dialog and prints the selected path on stdout (empty line if cancelled).

Invoked as:
    AutoCutPro.exe --pick-folder        (frozen build; handled in launcher)
    python -m autocut.folder_picker     (from source)
"""

from __future__ import annotations


def pick(initial: str | None = None) -> str | None:
    try:
        import tkinter as tk
        from tkinter import filedialog
    except Exception:
        return None
    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    try:
        path = filedialog.askdirectory(
            title="เลือกโฟลเดอร์สำหรับเก็บไฟล์ AutoCut Pro",
            initialdir=initial or None,
            mustexist=False,
        )
    finally:
        root.destroy()
    return path or None


if __name__ == "__main__":
    print(pick() or "")
