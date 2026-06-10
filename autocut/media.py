"""Probe media and normalize it to a uniform intermediate.

The single most important step for "supports any codec, rarely errors" is
*normalization*: whatever the customer throws at us (HEVC, VP9, AV1, odd frame
rates, variable frame rate, rotated phone clips, no audio track …) we transcode
it once into a predictable shape — H.264 / yuv420p / constant fps / AAC stereo /
one fixed canvas size.  After that, cutting, concatenating and overlaying are
trivial and reliable because every clip is identical in format.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from . import tools


@dataclass
class MediaInfo:
    path: str
    duration: float
    width: int
    height: int
    fps: float
    has_audio: bool
    vcodec: str
    acodec: str

    @property
    def is_portrait(self) -> bool:
        return self.height >= self.width


def probe(path: str) -> MediaInfo:
    """Read stream info with ffprobe. Never raises — returns sane defaults."""
    duration = 0.0
    width, height, fps = 0, 0, 30.0
    has_audio = False
    vcodec, acodec = "", ""
    try:
        res = tools.ffprobe([
            "-v", "quiet", "-print_format", "json",
            "-show_streams", "-show_format", path,
        ])
        data = json.loads((res.stdout or "").strip() or "{}")
        for s in data.get("streams", []):
            kind = s.get("codec_type")
            if kind == "video" and width == 0:
                vcodec = s.get("codec_name", "")
                width = int(s.get("width", 0) or 0)
                height = int(s.get("height", 0) or 0)
                fps = _parse_fps(s.get("avg_frame_rate") or s.get("r_frame_rate"))
                # Honor rotation metadata (phones record sideways).
                rot = _rotation(s)
                if rot in (90, 270):
                    width, height = height, width
                duration = duration or _to_float(s.get("duration"))
            elif kind == "audio":
                has_audio = True
                acodec = acodec or s.get("codec_name", "")
        if duration <= 0:
            duration = _to_float(data.get("format", {}).get("duration"))
    except Exception as e:  # pragma: no cover - defensive
        print(f"[media] probe failed for {path}: {e}")

    if duration <= 0:
        duration = 0.0  # caller decides fallback once it knows more
    if width <= 0 or height <= 0:
        width, height = 1080, 1920
    if fps <= 0:
        fps = 30.0
    return MediaInfo(path, duration, width, height, fps, has_audio, vcodec, acodec)


def _to_float(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _parse_fps(rate) -> float:
    if not rate or rate == "0/0":
        return 30.0
    try:
        if "/" in str(rate):
            num, den = str(rate).split("/")
            den = float(den)
            return float(num) / den if den else 30.0
        return float(rate)
    except (ValueError, ZeroDivisionError):
        return 30.0


def _rotation(stream: dict) -> int:
    try:
        tags = stream.get("tags", {})
        if "rotate" in tags:
            return abs(int(tags["rotate"])) % 360
        for sd in stream.get("side_data_list", []):
            if "rotation" in sd:
                return abs(int(sd["rotation"])) % 360
    except (ValueError, TypeError):
        pass
    return 0


# ---------------------------------------------------------------------------
# Canvas selection
# ---------------------------------------------------------------------------
def decide_canvas(infos: list[MediaInfo]) -> tuple[int, int]:
    """Pick one output canvas from the first usable clip's orientation.

    Review clips are usually vertical (TikTok / Reels), but we detect it so a
    landscape upload is not awkwardly cropped.
    """
    ref = next((i for i in infos if i.width and i.height), None)
    if ref is None:
        return 1080, 1920
    ratio = ref.width / ref.height
    if ratio > 1.2:
        return 1920, 1080      # landscape
    if ratio < 0.85:
        return 1080, 1920      # portrait
    return 1080, 1080          # square-ish


_RES_SHORT = {"720": 720, "1080": 1080, "4k": 2160}


def resolve_canvas(infos: list[MediaInfo], aspect: str = "auto",
                   resolution: str = "auto") -> tuple[int, int]:
    """Final output canvas from the customer's aspect + resolution choices.

    ``aspect``     : "auto" | "16:9" | "9:16" | "1:1"
    ``resolution`` : "auto" | "720" | "1080" | "4k"  (the standard short side)

    "auto/auto" keeps the current behaviour (match the source orientation at
    1080-ish).  Anything else builds an exact even-dimension canvas; normalize()
    then scales-to-fit and pads, so nothing is ever cropped.
    """
    base = decide_canvas(infos)
    if aspect == "auto" and resolution == "auto":
        return base

    if aspect in ("16:9", "9:16", "1:1"):
        orient = aspect
    else:  # derive orientation from the source
        bw, bh = base
        orient = "16:9" if bw > bh else ("9:16" if bw < bh else "1:1")

    short = _RES_SHORT.get(str(resolution).lower(), 1080)
    long_ = round(short * 16 / 9)
    if orient == "16:9":
        w, h = long_, short
    elif orient == "9:16":
        w, h = short, long_
    else:
        w, h = short, short
    return (w - w % 2, h - h % 2)  # H.264 needs even dimensions


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------
def normalize(src: str, dst: str, canvas: tuple[int, int], *, fps: int = 30,
              crf: int = 20, log=None) -> str:
    """Transcode *src* into the canonical intermediate at *dst* (H.264/AAC).

    Scales to fit the canvas while preserving aspect ratio, then pads with black
    so every normalized clip is byte-for-byte compatible for concat.  ``fps`` and
    ``crf`` (quality) come from the customer's settings.
    """
    tools.require_core()
    w, h = canvas
    info = probe(src)

    vf = (
        f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
        f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=black,"
        f"setsar=1,fps={fps},format=yuv420p"
    )

    args = ["-y", "-fflags", "+genpts", "-i", src]
    if info.has_audio:
        audio_map = ["-map", "0:v:0", "-map", "0:a:0"]
        shortest = []
    else:
        # Synthesize a silent stereo track so downstream audio filters always work.
        args += ["-f", "lavfi", "-i",
                 "anullsrc=channel_layout=stereo:sample_rate=48000"]
        audio_map = ["-map", "0:v:0", "-map", "1:a:0"]
        shortest = ["-shortest"]

    args += [
        *audio_map,
        "-vf", vf,
        "-c:v", "libx264", "-preset", "veryfast", "-crf", str(crf),
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-ar", "48000", "-ac", "2", "-b:a", "192k",
        *shortest,
        "-movflags", "+faststart",
        dst,
    ]
    tools.ffmpeg(args, check=True, log=log, timeout=3600)
    return dst
