"""Caption overlays rendered with HyperFrames.

This is where HyperFrames is the engine: we generate a HyperFrames composition
(an HTML file following the framework's ``class="clip"`` / ``data-*`` / paused
``window.__timelines`` contract), render it to a *transparent* WebM with
``hyperframes render --format webm``, and composite that over the cut video with
ffmpeg.

Everything here is best-effort.  If Chrome/HyperFrames is unavailable or a render
fails, :func:`burn` returns the original video unchanged so a missing caption
track never breaks the whole job.
"""

from __future__ import annotations

import html
import json
import os
import tempfile

from . import tools
from .analyze import Clip
from .transcribe import Transcript

# Keywords that promote a sentence to an on-screen caption (matches the UI copy:
# "AI shows only the important lines, not every sentence").
IMPORTANT_KEYWORDS = [
    "ดี", "แนะนำ", "คุณภาพ", "ราคา", "คุ้ม", "ประทับ", "ชอบ", "เยี่ยม", "เหมาะ",
    "สวย", "ครบ", "พอใจ", "สุดยอด", "เด่น", "ต้องลอง", "ไม่ผิดหวัง", "โอเค",
    "good", "great", "recommend", "quality", "best", "love", "worth", "nice",
    "perfect", "amazing", "excellent",
]

# All selectable caption looks (rendered through HyperFrames online; the libass
# fallback approximates them offline).  Names mirror the HyperFrames catalog.
STYLES = (
    "highlight", "pill", "neon-glow", "neon-accent", "kinetic-slam",
    "clip-wipe", "gradient-fill", "glitch-rgb", "matrix-decode",
    "weight-shift", "editorial", "emoji-pop", "pill-karaoke",
)
# Back-compat aliases for older saved choices.
_STYLE_ALIASES = {"neon": "neon-glow", "kinetic": "kinetic-slam"}


def _norm_style(style: str) -> str:
    style = _STYLE_ALIASES.get(style, style)
    return style if style in STYLES else "highlight"


# Curated "hit" Thai social-media fonts (all Google Fonts with Thai + Latin).
# id → (Google family name, weights to load).
FONTS = {
    "Kanit": ("Kanit", "700;800;900"),
    "Prompt": ("Prompt", "700;800;900"),
    "Mitr": ("Mitr", "600;700"),
    "Bai Jamjuree": ("Bai Jamjuree", "700"),
    "Chakra Petch": ("Chakra Petch", "700"),
    "Anuphan": ("Anuphan", "700;800"),
    "Mali": ("Mali", "700"),
    "Noto Sans Thai": ("Noto Sans Thai", "700;800;900"),
}
DEFAULT_FONT = "Kanit"


def _norm_font(font: str) -> str:
    return font if font in FONTS else DEFAULT_FONT


# ---------------------------------------------------------------------------
# Caption timing: map source-time transcript onto the final cut timeline
# ---------------------------------------------------------------------------
def build_captions(clips: list[Clip], transcript: Transcript | None, *,
                   important_only: bool = False, max_chars: int = 22,
                   max_words: int = 7) -> list[Clip]:
    """Return short, accurately-timed captions in *final* (post-cut) coordinates.

    The previous version showed one (truncated, 32-char) caption per *segment*
    and only for "important" lines — so most speech showed nothing and long
    sentences lost their tail.  This version works at the **word** level: every
    spoken word is mapped onto the cut timeline and grouped into small readable
    chunks that follow the voice, so captions stay in sync and nothing is
    dropped.  Falls back to segment text (re-chunked, never truncated) when a
    segment has no word timings.

    ``clips`` are the kept source ranges in output order; we accumulate an offset
    as we lay them end to end.
    """
    if not transcript or not transcript.segments:
        return []

    captions: list[Clip] = []
    offset = 0.0
    for clip in clips:
        for seg in transcript.segments:
            if min(seg.end, clip.end) - max(seg.start, clip.start) < 0.2:
                continue
            if important_only and not _is_important(seg.text):
                continue
            captions.extend(_segment_to_chunks(seg, clip, offset, max_chars, max_words))
        offset += clip.duration

    captions.sort(key=lambda c: c.start)
    return _dedupe(captions)


def _segment_to_chunks(seg, clip: Clip, offset: float, max_chars: int,
                       max_words: int) -> list[Clip]:
    """Turn one transcript segment into one or more timed caption chunks,
    clipped to *clip* and shifted onto the final timeline by *offset*."""
    def to_final(t: float) -> float:
        return offset + (min(max(t, clip.start), clip.end) - clip.start)

    # Prefer word-level timing when we have it.
    words = [w for w in (seg.words or [])
             if w.end > w.start and w.end > clip.start and w.start < clip.end]
    out: list[Clip] = []
    if words:
        cur: list = []
        cur_text = ""
        for w in words:
            cur.append(w)
            cur_text += w.text
            gap_break = (len(cur) >= 2 and
                         w.start - cur[-2].end > 0.6)
            if (len(cur_text.strip()) >= max_chars or len(cur) >= max_words
                    or gap_break
                    or to_final(w.end) - to_final(cur[0].start) >= 2.4):
                out.append(_mk_chunk(cur, to_final))
                cur, cur_text = [], ""
        if cur:
            out.append(_mk_chunk(cur, to_final))
        return [c for c in out if c.text and c.duration >= 0.15]

    # No word timings: split the segment text into time-proportional pieces.
    text = " ".join((seg.text or "").split())
    if not text:
        return []
    s, e = to_final(seg.start), to_final(seg.end)
    pieces = _split_text(text, max_chars)
    if len(pieces) <= 1:
        return [Clip(start=s, end=e, text=text)]
    span = (e - s) / len(pieces)
    return [Clip(start=s + i * span, end=s + (i + 1) * span, text=p)
            for i, p in enumerate(pieces)]


def _mk_chunk(words, to_final) -> Clip:
    text = "".join(w.text for w in words).strip()
    return Clip(start=to_final(words[0].start), end=to_final(words[-1].end), text=text)


def _split_text(text: str, max_chars: int) -> list[str]:
    """Greedy wrap by spaces (English) or by length (spaceless Thai)."""
    if len(text) <= max_chars:
        return [text]
    out, line = [], ""
    if " " in text:
        for word in text.split():
            if line and len(line) + 1 + len(word) > max_chars:
                out.append(line)
                line = word
            else:
                line = f"{line} {word}".strip()
        if line:
            out.append(line)
    else:
        for i in range(0, len(text), max_chars):
            out.append(text[i:i + max_chars])
    return out


def _is_important(text: str) -> bool:
    low = text.lower()
    return any(kw in low for kw in IMPORTANT_KEYWORDS)


def _dedupe(captions: list[Clip]) -> list[Clip]:
    out: list[Clip] = []
    for c in captions:
        if out and c.text == out[-1].text and c.start - out[-1].end < 0.5:
            out[-1].end = max(out[-1].end, c.end)
        else:
            out.append(c)
    return out


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------
def burn(video: str, captions: list[Clip], dst: str, *, canvas: tuple[int, int],
         duration: float, style: str = "highlight", fmt: str = "mp4",
         font: str = DEFAULT_FONT, overlay_out: str | None = None,
         work_dir: str | None = None, log=None) -> str:
    """Overlay *captions* on *video* → *dst*. Falls back to *video* on failure.

    If *overlay_out* is given and HyperFrames produced a transparent overlay,
    a copy is saved there (a .mov with alpha) so the customer can composite the
    subtitle themselves in CapCut / Premiere.
    """
    if not captions:
        return video
    style = _norm_style(style)
    font = _norm_font(font)

    work_dir = work_dir or tempfile.mkdtemp(prefix="hf_overlay_")

    # Primary: render captions as a HyperFrames composition and composite them.
    overlay = _render_overlay(captions, canvas, duration, style, work_dir, font, log)
    if overlay:
        if overlay_out:
            try:
                import shutil as _sh
                _sh.copyfile(overlay, overlay_out)
            except OSError:
                pass
        try:
            return _composite(video, overlay, dst, fmt=fmt, log=log)
        except tools.ToolError as e:
            if log:
                log(f"⚠️ caption composite failed: {e}")

    # Fallback: burn captions directly with ffmpeg/libass (no Chrome needed).
    if log:
        log("↩️ ใช้ ffmpeg burn subtitle แทน HyperFrames")
    try:
        return _burn_ass(video, captions, dst, canvas, style, fmt, work_dir, log)
    except Exception as e:  # noqa: BLE001
        if log:
            log(f"⚠️ subtitle ทั้งสองวิธีไม่สำเร็จ ({e}) — ส่งคลิปแบบไม่มี subtitle")
        return video


# ---------------------------------------------------------------------------
# HyperFrames render
# ---------------------------------------------------------------------------
def _render_overlay(captions: list[Clip], canvas: tuple[int, int],
                    duration: float, style: str, work_dir: str,
                    font: str = DEFAULT_FONT, log=None) -> str | None:
    if not tools.NPX:
        return None
    proj = os.path.join(work_dir, "hf_proj")
    os.makedirs(proj, exist_ok=True)

    w, h = canvas
    with open(os.path.join(proj, "meta.json"), "w", encoding="utf-8") as f:
        json.dump({"id": "captions", "name": "captions"}, f)
    with open(os.path.join(proj, "hyperframes.json"), "w", encoding="utf-8") as f:
        json.dump({"paths": {"blocks": "compositions", "assets": "assets"}}, f)
    with open(os.path.join(proj, "index.html"), "w", encoding="utf-8") as f:
        f.write(_composition_html(captions, w, h, duration, style, font))

    # HyperFrames emits a *transparent* overlay as MOV (ProRes 4444 w/ alpha).
    # WebM came out opaque (yuv420p) in testing, so MOV is the reliable choice.
    out = os.path.join(work_dir, "overlay.mov")
    if log:
        log(f"🎬 HyperFrames rendering caption overlay ({style})…")
    try:
        res = tools.run(
            [tools.NPX, "--yes", "hyperframes@0.6.80", "render", proj,
             "-o", out, "--format", "mov", "--fps", "30", "-q", "standard",
             "--quiet"],
            timeout=1800, log=log,
        )
    except tools.ToolError as e:
        if log:
            log(f"⚠️ hyperframes render error: {e}")
        return None
    if res.returncode != 0:
        if log:
            log(f"⚠️ hyperframes render failed: {(res.stderr or '')[-300:]}")
        return None
    if not (os.path.exists(out) and os.path.getsize(out) > 1024):
        return None
    # Guard: only use the overlay if it actually carries an alpha channel,
    # otherwise compositing would paint the whole frame opaque.
    if not _has_alpha(out):
        if log:
            log("⚠️ overlay has no alpha channel — skipping HyperFrames overlay")
        return None
    return out


def _has_alpha(path: str) -> bool:
    try:
        res = tools.ffprobe([
            "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=pix_fmt", "-of", "default=noprint_wrappers=1:nokey=1",
            path,
        ])
        pix = (res.stdout or "").strip().lower()
    except tools.ToolError:
        return False
    return any(tok in pix for tok in ("yuva", "rgba", "argb", "abgr", "bgra", "ya8", "ya16"))


def _composite(video: str, overlay: str, dst: str, *, fmt: str, log=None) -> str:
    from . import editor
    args = [
        "-y", "-i", video, "-i", overlay,
        "-filter_complex", "[0:v][1:v]overlay=0:0:format=auto[v]",
        "-map", "[v]", "-map", "0:a:0?",
        "-c:v", *editor.fmt_settings(fmt)["v"],
        "-c:a", *editor.fmt_settings(fmt)["a"], "-ar", "48000", "-ac", "2",
        *editor.fmt_settings(fmt)["extra"],
        dst,
    ]
    if log:
        log("🖼  Compositing captions onto video…")
    tools.ffmpeg(args, check=True, log=log, timeout=1800)
    return dst


# ---------------------------------------------------------------------------
# Composition HTML generation
# ---------------------------------------------------------------------------
def _composition_html(captions: list[Clip], w: int, h: int, duration: float,
                      style: str, font: str = DEFAULT_FONT) -> str:
    family, weights = FONTS[_norm_font(font)]
    font_url = ("https://fonts.googleapis.com/css2?family="
                + family.replace(" ", "+") + ":wght@" + weights + "&display=swap")
    caption_divs = []
    tweens = []
    for i, c in enumerate(captions):
        cid = f"cap{i}"
        text = html.escape(c.text)
        dur = max(0.3, c.duration)
        caption_divs.append(
            f'<div id="{cid}" class="clip caption {style}" '
            f'data-start="{c.start:.3f}" data-duration="{dur:.3f}" '
            f'data-track-index="1"><span class="cap-inner">{text}</span></div>'
        )
        tweens.append(_tween_for(style, cid, c.start))

    return f"""<!doctype html>
<html lang="th">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width={w}, height={h}" />
<link href="{font_url}" rel="stylesheet">
<script src="https://cdn.jsdelivr.net/npm/gsap@3.14.2/dist/gsap.min.js"></script>
<style>
  * {{ margin:0; padding:0; box-sizing:border-box; }}
  html, body {{ width:{w}px; height:{h}px; overflow:hidden; background:transparent; }}
  #root {{ position:relative; width:{w}px; height:{h}px; }}
  .caption {{
    position:absolute; left:6%; right:6%; bottom:16%;
    display:flex; justify-content:center; text-align:center;
    font-family:'{family}','Noto Sans Thai',sans-serif;
  }}
  .cap-inner {{ display:inline-block; line-height:1.25; padding:10px 22px; }}
{_style_css(w)}
</style>
</head>
<body>
  <div id="root" data-composition-id="main" data-start="0"
       data-duration="{max(1.0, duration):.3f}" data-width="{w}" data-height="{h}">
    {"".join(caption_divs)}
  </div>
  <script>
    window.__timelines = window.__timelines || {{}};
    const tl = gsap.timeline({{ paused: true }});
    {chr(10).join("    " + t for t in tweens)}
    window.__timelines["main"] = tl;
  </script>
</body>
</html>
"""


def _font_px(w: int) -> int:
    # Scale font with canvas width so it reads well on both 1080 and 576 widths.
    return max(34, round(w * 0.055))


def _style_css(w: int) -> str:
    fs = _font_px(w)
    big = round(fs * 1.12)
    return f"""
  .highlight .cap-inner {{
    font-size:{fs}px; font-weight:800; color:#111;
    background:#FFD600; border-radius:10px; box-shadow:0 6px 18px rgba(0,0,0,.45);
  }}
  .pill .cap-inner {{
    font-size:{fs}px; font-weight:700; color:#fff;
    background:#1a1a2e; border:3px solid #00E5A0; border-radius:999px;
    box-shadow:0 8px 22px rgba(0,0,0,.5);
  }}
  .neon-glow .cap-inner {{
    font-size:{fs}px; font-weight:800; color:#fff;
    text-shadow:0 0 8px #00E5A0,0 0 18px #00E5A0,0 0 36px #00E5A0;
  }}
  .neon-accent .cap-inner {{
    font-size:{fs}px; font-weight:800; color:#fff;
    border-bottom:6px solid #00E5FF; padding-bottom:4px;
    text-shadow:0 0 10px rgba(0,229,255,.6),0 3px 8px rgba(0,0,0,.6);
  }}
  .kinetic-slam .cap-inner {{
    font-size:{big}px; font-weight:900; color:#FFD600;
    -webkit-text-stroke:2px #FF6B35; text-shadow:0 4px 10px rgba(0,0,0,.6);
  }}
  .clip-wipe .cap-inner {{
    font-size:{fs}px; font-weight:900; color:#fff;
    text-shadow:0 3px 10px rgba(0,0,0,.7);
  }}
  .gradient-fill .cap-inner {{
    font-size:{big}px; font-weight:900;
    background:linear-gradient(90deg,#FFD600,#FF6B35,#FF2D95,#FFD600);
    background-size:300% 100%;
    -webkit-background-clip:text; background-clip:text; color:transparent;
    -webkit-text-fill-color:transparent;
    filter:drop-shadow(0 4px 8px rgba(0,0,0,.6));
  }}
  .glitch-rgb .cap-inner {{
    font-size:{fs}px; font-weight:900; color:#fff;
    text-shadow:3px 0 #ff003c,-3px 0 #00e5ff,0 3px 8px rgba(0,0,0,.5);
  }}
  .matrix-decode .cap-inner {{
    font-size:{fs}px; font-weight:800; color:#00FF6A;
    font-family:'Consolas','Courier New',monospace;
    text-shadow:0 0 10px rgba(0,255,106,.7);
  }}
  .weight-shift .cap-inner {{
    font-size:{fs}px; font-weight:900; color:#fff; letter-spacing:1px;
    text-shadow:0 3px 8px rgba(0,0,0,.6);
  }}
  .editorial .cap-inner {{
    font-size:{fs}px; font-weight:700; color:#fff; letter-spacing:2px;
    text-transform:uppercase; text-shadow:0 3px 10px rgba(0,0,0,.6);
  }}
  .emoji-pop .cap-inner {{
    font-size:{big}px; font-weight:900; color:#fff;
    text-shadow:0 0 4px #000,0 6px 14px rgba(0,0,0,.6);
  }}
  .pill-karaoke .cap-inner {{
    font-size:{fs}px; font-weight:800; color:#111;
    background:#FFD600; border-radius:999px; padding:10px 30px;
    box-shadow:0 8px 22px rgba(0,0,0,.45);
  }}"""


def _tween_for(style: str, cid: str, start: float) -> str:
    sel = f'"#{cid}"'
    s = f"{start:.3f}"
    tweens = {
        "kinetic-slam": f'tl.from({sel}, {{opacity:0, y:70, scale:0.55, duration:0.26, ease:"back.out(2.2)"}}, {s});',
        "pill": f'tl.from({sel}, {{opacity:0, scale:0.8, duration:0.25, ease:"power2.out"}}, {s});',
        "pill-karaoke": f'tl.from({sel}, {{opacity:0, scale:0.85, duration:0.22, ease:"power2.out"}}, {s});',
        "neon-glow": f'tl.from({sel}, {{opacity:0, duration:0.3, ease:"power1.out"}}, {s});',
        "neon-accent": f'tl.from({sel}, {{opacity:0, y:18, duration:0.28, ease:"power2.out"}}, {s});',
        "clip-wipe": f'tl.from({sel}, {{clipPath:"inset(0 100% 0 0)", duration:0.42, ease:"power2.out"}}, {s});',
        "gradient-fill": (f'tl.from({sel}, {{opacity:0, y:20, duration:0.3, ease:"power2.out"}}, {s});'
                          f'tl.to({sel}, {{backgroundPosition:"-100% 0", duration:1.2, ease:"none"}}, {s});'),
        "glitch-rgb": f'tl.from({sel}, {{opacity:0, x:-18, duration:0.18, ease:"steps(3)"}}, {s});',
        "matrix-decode": f'tl.from({sel}, {{opacity:0, filter:"blur(6px)", duration:0.3, ease:"power1.out"}}, {s});',
        "weight-shift": f'tl.from({sel}, {{opacity:0, letterSpacing:"14px", duration:0.32, ease:"power2.out"}}, {s});',
        "editorial": f'tl.from({sel}, {{opacity:0, letterSpacing:"20px", duration:0.4, ease:"power2.out"}}, {s});',
        "emoji-pop": f'tl.from({sel}, {{opacity:0, scale:0.4, duration:0.3, ease:"back.out(3)"}}, {s});',
    }
    # highlight (default): sweep up
    return tweens.get(style,
                      f'tl.from({sel}, {{opacity:0, y:24, duration:0.25, ease:"power2.out"}}, {s});')


# ---------------------------------------------------------------------------
# Fallback: burn captions with ffmpeg / libass (no Chrome required)
# ---------------------------------------------------------------------------
import glob as _glob  # noqa: E402

# ASS colours are &HAABBGGRR (alpha 00 = opaque). Per-style look:
_ASS_STYLE = {
    #               primary(text)   back(box)      outline        border out shad bold
    "highlight":    ("&H00111111", "&H0000D6FF", "&H00000000", 3, 6, 0, -1),
    "pill":         ("&H00FFFFFF", "&H002E1A1A", "&H00A0E500", 3, 8, 0, -1),
    "pill-karaoke": ("&H00111111", "&H0000D6FF", "&H00000000", 3, 8, 0, -1),
    "neon-glow":    ("&H00FFFFFF", "&H00000000", "&H00A0E500", 1, 3, 4,  0),
    "neon-accent":  ("&H00FFFFFF", "&H00000000", "&H00FFE500", 1, 3, 3,  0),
    "kinetic-slam": ("&H0000D6FF", "&H00000000", "&H00356BFF", 1, 4, 3, -1),
    "gradient-fill":("&H0000D6FF", "&H00000000", "&H00356BFF", 1, 4, 3, -1),
    "glitch-rgb":   ("&H00FFFFFF", "&H00000000", "&H003C00FF", 1, 3, 3,  0),
    "matrix-decode":("&H006AFF00", "&H00000000", "&H00000000", 1, 2, 4,  0),
    "weight-shift": ("&H00FFFFFF", "&H00000000", "&H00000000", 1, 4, 3, -1),
    "editorial":    ("&H00FFFFFF", "&H00000000", "&H00000000", 1, 3, 3,  0),
    "emoji-pop":    ("&H00FFFFFF", "&H00000000", "&H00000000", 1, 5, 4, -1),
}


def _thai_font() -> tuple[str | None, str]:
    """Return (fontsdir, fontname) — prefer a bundled/cached Noto Thai font."""
    # 1. Font shipped with the packaged app (<bundle>/fonts/*.ttf) — fully offline.
    bundled = os.path.join(tools.bundle_dir(), "fonts")
    ttf = _glob.glob(os.path.join(bundled, "*.ttf"))
    if ttf:
        return os.path.dirname(ttf[0]), "Noto Sans Thai"
    # 2. HyperFrames Noto Thai cache, if HyperFrames was ever used on this machine.
    cache = os.path.expanduser(r"~/.cache/hyperframes/fonts/noto-sans-thai")
    ttf = _glob.glob(os.path.join(cache, "*.ttf"))
    if ttf:
        return os.path.dirname(ttf[0]), "Noto Sans Thai"
    return None, "Tahoma"  # Tahoma ships with Windows and covers Thai


def _ass_time(t: float) -> str:
    if t < 0:
        t = 0.0
    h = int(t // 3600)
    m = int((t % 3600) // 60)
    s = int(t % 60)
    cs = int(round((t - int(t)) * 100))
    if cs == 100:
        cs, s = 0, s + 1
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _ass_escape(text: str) -> str:
    return (" ".join(text.split())
            .replace("\\", "\\\\").replace("{", "(").replace("}", ")"))


def _write_ass(captions: list[Clip], canvas: tuple[int, int], style: str,
               path: str) -> str:
    w, h = canvas
    primary, back, outline, border, out_w, shadow, bold = _ASS_STYLE.get(
        style, _ASS_STYLE["highlight"])
    _, fontname = _thai_font()
    fontsize = max(28, round(h * 0.045))
    margin_v = round(h * 0.14)

    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {w}
PlayResY: {h}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,{fontname},{fontsize},{primary},&H000000FF,{outline},{back},{bold},0,0,0,100,100,0,0,{border},{out_w},{shadow},2,60,60,{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = [
        f"Dialogue: 0,{_ass_time(c.start)},{_ass_time(c.end)},Cap,,0,0,0,,{_ass_escape(c.text)}"
        for c in captions if c.text.strip()
    ]
    with open(path, "w", encoding="utf-8") as f:
        f.write(header + "\n".join(lines) + "\n")
    return path


def _burn_ass(video: str, captions: list[Clip], dst: str, canvas: tuple[int, int],
              style: str, fmt: str, work_dir: str, log=None) -> str:
    from . import editor

    ass_path = os.path.join(work_dir, "captions.ass")
    _write_ass(captions, canvas, style, ass_path)

    fontsdir, _ = _thai_font()
    # libass wants forward slashes and escaped ':' in the filter path on Windows.
    filt_path = ass_path.replace("\\", "/").replace(":", "\\:")
    vf = f"ass='{filt_path}'"
    if fontsdir:
        vf += f":fontsdir='{fontsdir.replace(chr(92), '/').replace(':', chr(92)+':')}'"

    args = ["-y", "-i", video, "-vf", vf,
            "-map", "0:v:0", "-map", "0:a:0?",
            "-c:v", *editor.fmt_settings(fmt)["v"],
            "-c:a", *editor.fmt_settings(fmt)["a"], "-ar", "48000", "-ac", "2",
            *editor.fmt_settings(fmt)["extra"], dst]
    tools.ffmpeg(args, check=True, log=log, timeout=1800)
    if os.path.exists(dst) and os.path.getsize(dst) > 1024:
        return dst
    raise tools.ToolError("ass burn produced no output")
