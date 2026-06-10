"""Locate external tools (ffmpeg / ffprobe / node / hyperframes) and run them.

Why this module exists
----------------------
On Windows, ffmpeg is frequently installed somewhere that is *not* on the PATH
of the process that launched Python (e.g. a winget package folder, or a PATH
change that only new shells pick up).  Rather than fail with an opaque
"ffmpeg not found", we discover the binaries ourselves and then make sure every
child process we spawn — including the HyperFrames CLI, which also needs ffmpeg
— gets a PATH that contains them.

Everything else in the package goes through :func:`run` so logging, encoding and
the augmented PATH are handled in exactly one place.
"""

from __future__ import annotations

import glob
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass


class ToolError(RuntimeError):
    """A required external tool is missing or a command failed."""


# On a windowed (no-console) Windows build, every child process would otherwise
# pop its OWN black console window (ffmpeg runs many times per job).  This flag
# keeps them hidden.  0 on non-Windows / where the flag doesn't exist.
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------
def _is_win() -> bool:
    return os.name == "nt"


def bundle_dir() -> str:
    """Root that holds bundled resources (ffmpeg/, models/).

    Frozen (PyInstaller onedir): the folder that contains the .exe.
    From source: the project root (one level above this package).
    """
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _bundled(exe: str) -> str | None:
    """A binary shipped alongside the app (``<bundle>/ffmpeg/<exe>``)."""
    p = os.path.join(bundle_dir(), "ffmpeg", exe)
    return p if os.path.isfile(p) else None


def _winget_ffmpeg_candidates(exe: str) -> list[str]:
    """Glob the per-user winget package dir for ffmpeg/ffprobe."""
    roots = []
    for var in ("LOCALAPPDATA", "ProgramFiles", "ProgramFiles(x86)"):
        base = os.environ.get(var)
        if base:
            roots.append(os.path.join(base, "Microsoft", "WinGet", "Packages"))
    hits: list[str] = []
    for root in roots:
        pattern = os.path.join(root, "Gyan.FFmpeg*", "**", exe)
        hits.extend(glob.glob(pattern, recursive=True))
    # Newest version folder last alphabetically tends to be newest; prefer it.
    return sorted(hits)


def _common_candidates(exe: str) -> list[str]:
    out = []
    for d in (
        r"C:\ffmpeg\bin",
        r"C:\Program Files\ffmpeg\bin",
        "/usr/bin",
        "/usr/local/bin",
        "/opt/homebrew/bin",
    ):
        out.append(os.path.join(d, exe))
    return out


def find_executable(name: str, env_var: str | None, candidates: list[str]) -> str | None:
    # 1. Explicit override via environment variable.
    if env_var:
        override = os.environ.get(env_var)
        if override and os.path.isfile(override):
            return override
    # 2. Already on PATH.
    on_path = shutil.which(name)
    if on_path:
        return on_path
    # 3. Known install locations.
    for c in candidates:
        if os.path.isfile(c):
            return c
    return None


def _find_ffmpeg() -> str | None:
    exe = "ffmpeg.exe" if _is_win() else "ffmpeg"
    # A bundled ffmpeg always wins so a packaged build is fully self-contained
    # and deterministic regardless of what is (or isn't) on the customer's PATH.
    return _bundled(exe) or find_executable(
        "ffmpeg", "AUTOCUT_FFMPEG",
        _winget_ffmpeg_candidates(exe) + _common_candidates(exe),
    )


def _find_ffprobe() -> str | None:
    exe = "ffprobe.exe" if _is_win() else "ffprobe"
    return _bundled(exe) or find_executable(
        "ffprobe", "AUTOCUT_FFPROBE",
        _winget_ffmpeg_candidates(exe) + _common_candidates(exe),
    )


def _find_npx() -> str | None:
    # In a packaged (frozen) build we deliberately ship only the offline
    # ffmpeg/libass subtitle path.  If the customer happens to have Node,
    # `npx --yes hyperframes` would try to fetch packages from the internet and
    # could hang — so disable HyperFrames unless explicitly re-enabled.
    if getattr(sys, "frozen", False) and not os.environ.get("AUTOCUT_ENABLE_HYPERFRAMES"):
        return None
    # npx ships with node; on Windows it is npx.cmd.
    return shutil.which("npx") or shutil.which("npx.cmd")


FFMPEG = _find_ffmpeg()
FFPROBE = _find_ffprobe()
NPX = _find_npx()


@dataclass
class ToolStatus:
    ffmpeg: str | None
    ffprobe: str | None
    npx: str | None

    @property
    def core_ok(self) -> bool:
        return bool(self.ffmpeg and self.ffprobe)


def status() -> ToolStatus:
    """Re-probe (cheap) so a freshly installed tool is picked up without restart."""
    global FFMPEG, FFPROBE, NPX
    FFMPEG = _find_ffmpeg()
    FFPROBE = _find_ffprobe()
    NPX = _find_npx()
    return ToolStatus(FFMPEG, FFPROBE, NPX)


def require_core() -> None:
    if not FFMPEG or not FFPROBE:
        raise ToolError(
            "ffmpeg/ffprobe not found. Install ffmpeg (e.g. `winget install Gyan.FFmpeg`) "
            "or set the AUTOCUT_FFMPEG / AUTOCUT_FFPROBE environment variables."
        )


# ---------------------------------------------------------------------------
# Subprocess execution
# ---------------------------------------------------------------------------
def subprocess_env() -> dict:
    """A copy of os.environ with the ffmpeg/ffprobe dirs prepended to PATH.

    HyperFrames shells out to ffmpeg/ffprobe, so injecting the dir here is what
    lets `hyperframes render` work even when ffmpeg is not globally on PATH.
    """
    env = os.environ.copy()
    extra: list[str] = []
    for tool in (FFMPEG, FFPROBE):
        if tool:
            d = os.path.dirname(tool)
            if d and d not in extra:
                extra.append(d)
    if extra:
        env["PATH"] = os.pathsep.join(extra) + os.pathsep + env.get("PATH", "")
    return env


def run(cmd: list[str], *, timeout: int | None = None, check: bool = False,
        log=None) -> subprocess.CompletedProcess:
    """Run *cmd*, capturing UTF-8 output with the augmented environment."""
    if log:
        log(f"$ {' '.join(str(c) for c in cmd[:6])}{' …' if len(cmd) > 6 else ''}")
    try:
        res = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=subprocess_env(),
            timeout=timeout,
            creationflags=_NO_WINDOW,
        )
    except FileNotFoundError as e:
        raise ToolError(f"Executable not found: {cmd[0]} ({e})") from e
    except subprocess.TimeoutExpired as e:
        raise ToolError(f"Command timed out after {timeout}s: {cmd[0]}") from e
    if check and res.returncode != 0:
        tail = (res.stderr or res.stdout or "")[-800:]
        raise ToolError(f"Command failed ({res.returncode}): {cmd[0]}\n{tail}")
    return res


def ffmpeg(args: list[str], **kw) -> subprocess.CompletedProcess:
    require_core()
    return run([FFMPEG, "-hide_banner", "-loglevel", "error", *args], **kw)


def ffprobe(args: list[str], **kw) -> subprocess.CompletedProcess:
    require_core()
    return run([FFPROBE, *args], **kw)
