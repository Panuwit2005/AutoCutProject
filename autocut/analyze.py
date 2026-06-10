"""Decide which parts of the footage to keep.

Two strategies, depending on what we have:

* With a transcript — score every spoken segment for "review value" (mentions of
  the product, quality, recommendation, price …), drop filler, then greedily keep
  the best until we hit the target duration, in chronological order.
* Without a transcript — fall back to ffmpeg silence detection and keep the
  spoken zones.  This guarantees the pipeline always produces *something*.

:func:`trim_dead_air` then tightens whatever was chosen by dropping the pauses
where nobody is actually speaking.  It prefers the transcript's word-level
timing (which survives loud background music, common in these review intros) and
falls back to acoustic silence detection when no transcript is available.
"""

from __future__ import annotations

import random
import re
from dataclasses import dataclass

from . import tools
from .transcribe import Transcript

# Words that signal a useful review moment (Thai + English).
REVIEW_KEYWORDS = [
    "ดี", "แนะนำ", "คุณภาพ", "ราคา", "ซื้อ", "ใช้", "สินค้า", "รีวิว", "ชอบ",
    "เยี่ยม", "สุด", "มาก", "จริง", "เหมาะ", "คุ้ม", "ประทับ", "น่า", "สวย",
    "ครบ", "ใหม่", "พอใจ", "ต้องการ", "เด่น", "โดด", "ลอง", "ผิดหวัง", "โอเค",
    "good", "great", "recommend", "quality", "buy", "product", "review", "like",
    "love", "best", "value", "worth", "nice", "perfect", "amazing", "excellent",
]

# Pure filler — if a whole segment is just this, it is worthless.
FILLERS = {"อืม", "เอ่อ", "ก็", "นะ", "อ่า", "เอ้อ", "ครับ", "ค่ะ",
           "hmm", "uh", "um", "ah", "er", "you know"}

MIN_CLIP = 0.6      # seconds — anything shorter is a fragment
MERGE_GAP = 0.6     # merge kept clips separated by a gap this small


@dataclass
class Clip:
    start: float
    end: float
    text: str = ""
    score: float = 0.0

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)


def _score(text: str, duration: float) -> float:
    low = text.lower()
    score = 0.0
    for kw in REVIEW_KEYWORDS:
        if kw in low:
            score += 2.0
    if 2.0 <= duration <= 8.0:
        score += 1.0          # natural sentence length
    elif duration < MIN_CLIP:
        score -= 5.0          # too short to be useful
    stripped = re.sub(r"[\s.,!?]", "", low)
    if stripped in FILLERS or not stripped:
        score -= 4.0
    return score


def select_for_review(transcript: Transcript, max_duration: float,
                      variation: int = 0) -> list[Clip]:
    """Pick the best review moments up to *max_duration* seconds.

    ``variation`` makes the result reproducibly *different* from the same footage
    (the "🎲 สุ่มรูปแบบใหม่" button): it seeds a RNG that jitters the segment
    scores so a different — but still sensible — set of moments is chosen, and
    nudges clip boundaries a little.  ``variation == 0`` keeps the deterministic
    "best" cut.
    """
    rnd = random.Random(variation) if variation else None

    candidates: list[Clip] = []
    for seg in transcript.segments:
        text = seg.text.strip()
        dur = seg.end - seg.start
        if dur < 0.2:
            continue
        candidates.append(Clip(seg.start, seg.end, text, _score(text, dur)))

    if not candidates:
        return []

    # Keep anything that is not actively bad.
    usable = [c for c in candidates if c.score > -3]
    if not usable:
        usable = candidates

    # Greedily take the highest-scoring clips that fit the time budget.  With a
    # variation seed we add a random jitter to each score so the ranking — and
    # therefore the chosen moments — shifts between runs.
    def rank(c: Clip) -> float:
        return c.score + (rnd.uniform(-2.0, 2.0) if rnd else 0.0)

    chosen: list[Clip] = []
    total = 0.0
    for c in sorted(usable, key=rank, reverse=True):
        if c.duration < MIN_CLIP and len(chosen) > 0:
            continue
        if total + c.duration > max_duration and chosen:
            continue
        chosen.append(c)
        total += c.duration
        if total >= max_duration:
            break

    if not chosen:
        chosen = usable[: max(1, len(usable) // 2)]

    chosen.sort(key=lambda x: x.start)
    merged = _merge_adjacent(chosen)
    return _jitter_bounds(merged, rnd) if rnd else merged


def _jitter_bounds(clips: list[Clip], rnd: random.Random) -> list[Clip]:
    """Nudge each clip's in/out points slightly for visible variety (safe-clamped)."""
    out: list[Clip] = []
    for c in clips:
        start = max(0.0, c.start + rnd.uniform(-0.20, 0.20))
        end = max(start + MIN_CLIP, c.end + rnd.uniform(-0.20, 0.40))
        out.append(Clip(start, end, c.text, c.score))
    return out


def _merge_adjacent(clips: list[Clip]) -> list[Clip]:
    """Join clips separated by only a tiny gap to avoid choppy micro-cuts."""
    if not clips:
        return []
    merged = [clips[0]]
    for c in clips[1:]:
        last = merged[-1]
        if c.start - last.end <= MERGE_GAP:
            last.end = max(last.end, c.end)
            last.text = (last.text + " " + c.text).strip()
            last.score = max(last.score, c.score)
        else:
            merged.append(c)
    return merged


# ---------------------------------------------------------------------------
# Fallback: cut by silence when there is no transcript
# ---------------------------------------------------------------------------
def speech_zones(path: str, duration: float, max_duration: float, *,
                 noise_db: int = -32, min_silence: float = 0.6,
                 variation: int = 0, log=None) -> list[Clip]:
    """Return spoken (non-silent) zones via ffmpeg silencedetect."""
    starts, ends = _silence_intervals(path, noise_db, min_silence, log)
    if not starts:
        return [Clip(0.0, min(duration, max_duration) or max_duration,
                     "ทั้งคลิป", 0.0)]

    zones: list[Clip] = []
    prev_end = 0.0
    for i, s_start in enumerate(starts):
        if s_start > prev_end + 0.2:
            zones.append(Clip(prev_end, s_start, "", 1.0))
        prev_end = ends[i] if i < len(ends) else s_start
    if duration and prev_end < duration - 0.2:
        zones.append(Clip(prev_end, duration, "", 1.0))

    # With a variation seed, drop a few zones at random so a re-run keeps a
    # different subset of the spoken parts (still chronological).
    if variation and len(zones) > 2:
        rnd = random.Random(variation)
        zones = [z for z in zones if rnd.random() > 0.25] or zones

    # Trim to the time budget, keeping chronological order.
    out: list[Clip] = []
    total = 0.0
    for z in zones:
        if z.duration < MIN_CLIP:
            continue
        take = min(z.duration, max_duration - total)
        if take <= 0:
            break
        out.append(Clip(z.start, z.start + take, "", 1.0))
        total += take
        if total >= max_duration:
            break
    return out or [Clip(0.0, min(duration, max_duration) or max_duration, "", 0.0)]


def _silence_intervals(path: str, noise_db: int, min_silence: float, log=None):
    try:
        # silencedetect logs its results at the *info* level; tools.ffmpeg pins
        # -loglevel error, so override it here (the last -loglevel wins) or we
        # would silently parse an empty stderr and "find" no silence at all.
        res = tools.ffmpeg([
            "-i", path,
            "-af", f"silencedetect=noise={noise_db}dB:d={min_silence}",
            "-loglevel", "info", "-f", "null", "-",
        ], log=log, timeout=600)
        stderr = res.stderr or ""
    except tools.ToolError as e:
        if log:
            log(f"⚠️ silencedetect failed: {e}")
        return [], []
    starts, ends = [], []
    for line in stderr.splitlines():
        m1 = re.search(r"silence_start:\s*(-?[0-9.]+)", line)
        m2 = re.search(r"silence_end:\s*(-?[0-9.]+)", line)
        if m1:
            starts.append(max(0.0, float(m1.group(1))))
        if m2:
            ends.append(float(m2.group(1)))
    return starts, ends


# ---------------------------------------------------------------------------
# Dead-air removal — tighten the chosen clips by dropping non-speech pauses
# ---------------------------------------------------------------------------
# How long a pause has to be before we treat it as dead air, per aggressiveness.
DEAD_AIR_PRESETS = {
    "gentle": {"max_gap": 0.80, "min_silence": 0.80},
    "medium": {"max_gap": 0.50, "min_silence": 0.55},
    "strong": {"max_gap": 0.30, "min_silence": 0.35},
}
EDGE_PAD = 0.10     # keep this much around kept speech so onsets aren't clipped


def trim_dead_air(clips: list[Clip], transcript=None, path: str | None = None, *,
                  aggressiveness: str = "medium", log=None) -> list[Clip]:
    """Drop the dead air *inside* the chosen clips, returning tighter clips.

    Prefers the transcript's word timing (robust to loud background music); falls
    back to acoustic silence detection on *path* when there are no words.  Never
    raises and never returns nothing — on any doubt it returns *clips* unchanged
    so the pipeline degrades gracefully.
    """
    if not clips:
        return clips
    preset = DEAD_AIR_PRESETS.get(aggressiveness, DEAD_AIR_PRESETS["medium"])
    words = getattr(transcript, "words", []) if transcript else []

    try:
        if words:
            out = _dead_air_by_words(clips, words, preset["max_gap"])
            method = "transcript"
        elif path:
            out = _dead_air_by_silence(clips, path, preset["min_silence"], log=log)
            method = "silence"
        else:
            return clips
    except Exception as e:  # noqa: BLE001 - tightening must never break a job
        if log:
            log(f"⚠️ ตัด dead air ไม่สำเร็จ ({e}) — ใช้ช่วงเดิม")
        return clips

    before = sum(c.duration for c in clips)
    after = sum(c.duration for c in out)
    if not out or after < min(1.0, before * 0.25):
        return clips                       # suspiciously aggressive — keep originals
    if log:
        log(f"🤫 ตัด dead air ({method}/{aggressiveness}) ~{max(0.0, before - after):.1f}s "
            f"→ {len(out)} ช่วง")
    return out


def _dead_air_by_words(clips: list[Clip], words, max_gap: float,
                       edge_pad: float = EDGE_PAD) -> list[Clip]:
    """Split each clip into runs of speech, dropping word gaps > *max_gap*."""
    out: list[Clip] = []
    for c in clips:
        ws = sorted((w for w in words
                     if w.end > w.start and w.end > c.start and w.start < c.end),
                    key=lambda w: w.start)
        if not ws:
            out.append(c)                  # no word timing here — leave it alone
            continue
        run_start = max(c.start, ws[0].start)
        run_end = ws[0].end
        runs: list[tuple[float, float]] = []
        for w in ws[1:]:
            if w.start - run_end > max_gap:
                runs.append((run_start, run_end))
                run_start = w.start
            run_end = max(run_end, w.end)
        runs.append((run_start, run_end))
        for s, e in runs:
            s = max(c.start, s - edge_pad)
            e = min(c.end, e + edge_pad)
            if e - s >= MIN_CLIP:
                out.append(Clip(s, e, c.text, c.score))
    return out


def _dead_air_by_silence(clips: list[Clip], path: str, min_silence: float, *,
                         noise_db: int = -32, edge_pad: float = EDGE_PAD,
                         log=None) -> list[Clip]:
    """Subtract detected silence spans from each clip."""
    spans = _silence_spans(path, noise_db, min_silence, log)
    if not spans:
        return clips
    out: list[Clip] = []
    for c in clips:
        for s, e in _subtract_silences(c.start, c.end, spans, edge_pad):
            if e - s >= MIN_CLIP:
                out.append(Clip(s, e, c.text, c.score))
    return out or clips


def _silence_spans(path: str, noise_db: int, min_silence: float, log=None):
    """Paired (start, end) silence spans; an unmatched final start runs to EOF."""
    starts, ends = _silence_intervals(path, noise_db, min_silence, log)
    spans: list[tuple[float, float | None]] = []
    for i, s in enumerate(starts):
        spans.append((s, ends[i] if i < len(ends) else None))
    return spans


def _subtract_silences(cs: float, ce: float, spans, pad: float):
    """Return the speech pieces of [cs, ce] after removing the padded silences."""
    cuts: list[tuple[float, float]] = []
    for ss, se in spans:
        se = ce if se is None else se
        a, b = max(cs, ss) + pad, min(ce, se) - pad
        if b > a:
            cuts.append((a, b))
    cuts.sort()
    pieces: list[tuple[float, float]] = []
    cur = cs
    for a, b in cuts:
        if a > cur:
            pieces.append((cur, a))
        cur = max(cur, b)
    if ce > cur:
        pieces.append((cur, ce))
    return pieces
