"""Cut, concatenate and mix audio with ffmpeg.

Because every source has already been run through :mod:`media.normalize`, all
clips share one codec / fps / resolution / sample rate.  That makes the editing
operations here simple and reliable: cuts re-encode from the canonical format,
and concatenation uses the ffmpeg ``concat`` filter (which re-encodes) so there
are never timestamp or parameter mismatches at the seams.

The L-cut / J-cut hook lives in :func:`concat` (``audio_overlap``): when it is
positive the audio of adjacent clips is cross-laid across each seam so it
overlaps the visual cut, giving professional-feeling transitions.  The graph is
built defensively — if anything in the L/J path fails it falls back to the plain
``concat`` so a job is never lost to a fancy transition.
"""

from __future__ import annotations

import os

from . import tools
from .analyze import Clip

# Final-delivery container/codec settings.
FORMATS = {
    "mp4":  {"v": ["libx264"], "a": ["aac"],        "extra": ["-movflags", "+faststart"]},
    "mov":  {"v": ["libx264"], "a": ["aac"],        "extra": []},
    "avi":  {"v": ["mpeg4", "-q:v", "5"], "a": ["libmp3lame"], "extra": []},
    "webm": {"v": ["libvpx-vp9", "-b:v", "0", "-crf", "32"], "a": ["libopus"], "extra": []},
}
MIME = {
    "mp4": "video/mp4", "mov": "video/quicktime",
    "avi": "video/x-msvideo", "webm": "video/webm",
}


def fmt_settings(fmt: str) -> dict:
    return FORMATS.get(fmt, FORMATS["mp4"])


def _venc(fmt: str) -> list[str]:
    s = fmt_settings(fmt)
    return ["-c:v", *s["v"]]


def _aenc(fmt: str) -> list[str]:
    s = fmt_settings(fmt)
    return ["-c:a", *s["a"]]


# ---------------------------------------------------------------------------
# Cutting
# ---------------------------------------------------------------------------
def cut_clip(src: str, clip: Clip, dst: str, *, fmt: str = "mp4", log=None) -> str | None:
    """Extract [clip.start, clip.end] from *src* into *dst*. Returns dst or None."""
    dur = clip.duration
    if dur <= 0:
        return None
    args = [
        "-y", "-ss", f"{clip.start:.3f}", "-i", src, "-t", f"{dur:.3f}",
        "-map", "0:v:0", "-map", "0:a:0?",
        *_venc(fmt), "-crf", "20" if fmt in ("mp4", "mov") else "23",
        "-preset", "veryfast" if fmt in ("mp4", "mov") else "good",
        "-pix_fmt", "yuv420p",
        *_aenc(fmt), "-ar", "48000", "-ac", "2",
        "-avoid_negative_ts", "make_zero", "-reset_timestamps", "1",
        *fmt_settings(fmt)["extra"],
        dst,
    ]
    try:
        tools.ffmpeg(args, check=True, log=log, timeout=1800)
    except tools.ToolError as e:
        if log:
            log(f"⚠️ cut failed ({os.path.basename(dst)}): {e}")
        return None
    if os.path.exists(dst) and os.path.getsize(dst) > 1024:
        return dst
    return None


def cut_clips(src: str, clips: list[Clip], out_dir: str, *, prefix: str = "clip",
              fmt: str = "mp4", log=None) -> list[str]:
    paths = []
    for i, clip in enumerate(clips, 1):
        dst = os.path.join(out_dir, f"{prefix}_{i:02d}.{fmt}")
        result = cut_clip(src, clip, dst, fmt=fmt, log=log)
        if result:
            paths.append(result)
            if log:
                log(f"  ✂️ {prefix}_{i:02d}: {clip.start:.1f}s→{clip.end:.1f}s")
    return paths


# ---------------------------------------------------------------------------
# Concatenation
# ---------------------------------------------------------------------------
def concat(clip_paths: list[str], dst: str, *, fmt: str = "mp4",
           max_duration: float | None = None, audio_overlap: float = 0.0,
           lj_mode: str = "l", log=None) -> str:
    """Concatenate clips into one file (re-encode).

    With ``audio_overlap <= 0`` this is a plain hard-cut concat.  With
    ``audio_overlap > 0`` and at least two clips it performs a real **L-cut /
    J-cut**: the video is still hard-cut, but the audio of adjacent clips is
    cross-laid across each seam so it overlaps the visual cut by
    ``audio_overlap`` seconds.

    * ``lj_mode='l'`` — the *current* clip's audio keeps ringing over the next
      clip's picture (audio lags the cut).
    * ``lj_mode='j'`` — the *next* clip's audio comes in under the current
      picture (audio leads the cut).

    If the L/J graph fails for any reason it degrades to :func:`_concat_plain`,
    so a fancy transition can never lose a job.
    """
    if not clip_paths:
        raise tools.ToolError("concat: no clips")
    if len(clip_paths) == 1:
        return _reencode(clip_paths[0], dst, fmt=fmt, max_duration=max_duration, log=log)

    if audio_overlap and audio_overlap > 0:
        try:
            return _concat_lj(clip_paths, dst, fmt=fmt, max_duration=max_duration,
                              overlap=audio_overlap, mode=lj_mode, log=log)
        except tools.ToolError as e:
            if log:
                log(f"⚠️ L/J cut ไม่สำเร็จ — ใช้การต่อแบบปกติแทน: {e}")

    return _concat_plain(clip_paths, dst, fmt=fmt, max_duration=max_duration, log=log)


def _concat_plain(clip_paths: list[str], dst: str, *, fmt: str = "mp4",
                  max_duration: float | None = None, log=None) -> str:
    """Hard-cut concatenation via the ffmpeg ``concat`` filter."""
    inputs: list[str] = []
    for p in clip_paths:
        inputs += ["-i", p]
    n = len(clip_paths)
    streams = "".join(f"[{i}:v:0][{i}:a:0]" for i in range(n))
    filtergraph = f"{streams}concat=n={n}:v=1:a=1[v][a]"

    args = ["-y", *inputs, "-filter_complex", filtergraph,
            "-map", "[v]", "-map", "[a]",
            *_venc(fmt), *_aenc(fmt), "-ar", "48000", "-ac", "2"]
    if max_duration:
        args += ["-t", f"{max_duration:.3f}"]
    args += [*fmt_settings(fmt)["extra"], dst]
    tools.ffmpeg(args, check=True, log=log, timeout=3600)
    return dst


def _duration(path: str) -> float:
    """Best-effort container duration in seconds (0.0 if unknown)."""
    try:
        res = tools.ffprobe([
            "-v", "quiet", "-show_entries", "format=duration",
            "-of", "default=nokey=1:noprint_wrappers=1", path,
        ])
        return float((res.stdout or "").strip() or 0.0)
    except (tools.ToolError, ValueError):
        return 0.0


def _concat_lj(clip_paths: list[str], dst: str, *, fmt: str, overlap: float,
               mode: str = "l", max_duration: float | None = None, log=None) -> str:
    """Concatenate with an L-cut / J-cut audio bleed across every seam.

    The video is hard-cut, with each clip trimmed by ``overlap`` on the side the
    neighbour's picture takes over; the audio is cross-faded with ``acrossfade``
    so it spans the cut.  Both video and audio shorten by exactly ``overlap`` per
    seam, so they stay frame-aligned with no cumulative drift — only the seam
    itself carries the intentional audio/picture offset that *is* the L/J cut.
    """
    n = len(clip_paths)
    mode = "j" if str(mode).lower().startswith("j") else "l"

    durs = [_duration(p) for p in clip_paths]
    if any(d <= 0 for d in durs):
        raise tools.ToolError("L/J: could not read clip durations")
    # Keep the overlap safely shorter than the shortest clip so every trim and
    # cross-fade has material to work with (acrossfade needs each input >= d).
    v = min(overlap, min(durs) * 0.45)
    if v < 0.05:
        raise tools.ToolError("L/J: clips too short for an audio overlap")

    inputs: list[str] = []
    for p in clip_paths:
        inputs += ["-i", p]

    chains: list[str] = []

    # --- video: hard cuts, each clip trimmed on the overlapped side ----------
    vlabels: list[str] = []
    for i, d in enumerate(durs):
        lbl = f"v{i}"
        vlabels.append(f"[{lbl}]")
        if mode == "l" and i < n - 1:
            # L-cut: this clip's picture ends early; the next clip covers the seam.
            chains.append(f"[{i}:v:0]trim=0:{d - v:.3f},setpts=PTS-STARTPTS[{lbl}]")
        elif mode == "j" and i > 0:
            # J-cut: this clip's picture starts late; the previous clip held the seam.
            chains.append(f"[{i}:v:0]trim={v:.3f}:{d:.3f},setpts=PTS-STARTPTS[{lbl}]")
        else:
            chains.append(f"[{i}:v:0]setpts=PTS-STARTPTS[{lbl}]")
    chains.append(f"{''.join(vlabels)}concat=n={n}:v=1:a=0[v]")

    # --- audio: cross-fade every seam by v seconds (overlaps the visual cut) --
    prev = "[0:a:0]"
    for i in range(1, n):
        out = "[a]" if i == n - 1 else f"[ax{i}]"
        chains.append(f"{prev}[{i}:a:0]acrossfade=d={v:.3f}:c1=tri:c2=tri{out}")
        prev = out

    filtergraph = ";".join(chains)
    args = ["-y", *inputs, "-filter_complex", filtergraph,
            "-map", "[v]", "-map", "[a]",
            *_venc(fmt), *_aenc(fmt), "-ar", "48000", "-ac", "2"]
    if max_duration:
        args += ["-t", f"{max_duration:.3f}"]
    args += [*fmt_settings(fmt)["extra"], dst]
    if log:
        log(f"🎞 L/J cut ({mode.upper()}-cut, overlap {v:.2f}s, {n} clips)")
    tools.ffmpeg(args, check=True, log=log, timeout=3600)
    return dst


def _reencode(src: str, dst: str, *, fmt: str, max_duration: float | None = None,
              log=None) -> str:
    args = ["-y", "-i", src, "-map", "0:v:0", "-map", "0:a:0?",
            *_venc(fmt), *_aenc(fmt), "-ar", "48000", "-ac", "2"]
    if max_duration:
        args += ["-t", f"{max_duration:.3f}"]
    args += [*fmt_settings(fmt)["extra"], dst]
    tools.ffmpeg(args, check=True, log=log, timeout=1800)
    return dst


# ---------------------------------------------------------------------------
# Audio extraction (separate MP3)
# ---------------------------------------------------------------------------
def extract_audio(src: str, dst_mp3: str, *, log=None) -> str | None:
    """Extract the audio track of *src* to an MP3 file. Returns dst or None."""
    args = ["-y", "-i", src, "-vn", "-map", "0:a:0?",
            "-c:a", "libmp3lame", "-q:a", "2", "-ar", "48000", "-ac", "2",
            dst_mp3]
    try:
        tools.ffmpeg(args, check=True, log=log, timeout=1800)
    except tools.ToolError as e:
        if log:
            log(f"⚠️ แยกเสียง MP3 ไม่สำเร็จ: {e}")
        return None
    if os.path.exists(dst_mp3) and os.path.getsize(dst_mp3) > 256:
        return dst_mp3
    return None


# ---------------------------------------------------------------------------
# Background music
# ---------------------------------------------------------------------------
def add_music(video: str, music: str, dst: str, *, fmt: str = "mp4",
              music_volume: float = 0.2, max_duration: float | None = None,
              log=None) -> str:
    """Duck background music under the original audio and mux it in."""
    filtergraph = (
        f"[0:a]volume=1.0[a0];"
        f"[1:a]volume={music_volume}[a1];"
        f"[a0][a1]amix=inputs=2:duration=first:dropout_transition=2[aout]"
    )
    args = ["-y", "-i", video, "-i", music,
            "-filter_complex", filtergraph,
            "-map", "0:v:0", "-map", "[aout]",
            *_venc(fmt), *_aenc(fmt), "-ar", "48000", "-ac", "2"]
    if max_duration:
        args += ["-t", f"{max_duration:.3f}"]
    args += [*fmt_settings(fmt)["extra"], dst]
    try:
        tools.ffmpeg(args, check=True, log=log, timeout=1800)
    except tools.ToolError as e:
        if log:
            log(f"⚠️ music mix failed, keeping original audio: {e}")
        return video
    return dst
