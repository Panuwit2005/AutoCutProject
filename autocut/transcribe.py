"""Speech-to-text with graceful degradation.

Engines are tried in order of reliability *on this machine*:

1. faster-whisper (pip, in the venv) — self-contained, good Thai, word-level
   timestamps, built-in VAD.  This is the primary engine on Windows.
2. HyperFrames `transcribe` — used only if whisper.cpp is installed (it shells
   out to whisper-cpp under the hood).
3. Nothing — the caller falls back to silence-based cutting.

Whatever engine runs, the output is the same shape (:class:`Transcript`) so the
rest of the pipeline never has to care which one produced it.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field

from . import tools


@dataclass
class Word:
    start: float
    end: float
    text: str


@dataclass
class Segment:
    start: float
    end: float
    text: str
    words: list[Word] = field(default_factory=list)


@dataclass
class Transcript:
    segments: list[Segment]
    language: str = ""
    engine: str = ""

    @property
    def words(self) -> list[Word]:
        out: list[Word] = []
        for s in self.segments:
            out.extend(s.words)
        return out


class TranscribeUnavailable(RuntimeError):
    """No working speech-to-text engine is installed."""


# ---------------------------------------------------------------------------
# faster-whisper (primary)
# ---------------------------------------------------------------------------
_FW_MODELS: dict[str, object] = {}


def _faster_whisper_model(model_size: str):
    """Load and cache a WhisperModel (loading is the slow part)."""
    if model_size in _FW_MODELS:
        return _FW_MODELS[model_size]
    from faster_whisper import WhisperModel  # may raise ImportError

    model = WhisperModel(model_size, device="cpu", compute_type="int8")
    _FW_MODELS[model_size] = model
    return model


def _transcribe_faster_whisper(path: str, language: str, model_size: str,
                               log=None) -> Transcript:
    model = _faster_whisper_model(model_size)
    if log:
        log(f"faster-whisper ({model_size}) transcribing…")
    segments_iter, info = model.transcribe(
        path,
        language=language or None,
        word_timestamps=True,
        vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 500},
        # Stop the model from spiralling into hallucinated foreign words when the
        # audio is music-heavy (common in review intros).
        condition_on_previous_text=False,
        no_speech_threshold=0.6,
    )
    segments: list[Segment] = []
    for seg in segments_iter:
        words = [
            Word(float(w.start), float(w.end), w.word)
            for w in (seg.words or [])
            if w.start is not None and w.end is not None
        ]
        segments.append(Segment(float(seg.start), float(seg.end),
                                (seg.text or "").strip(), words))
        if log and len(segments) % 10 == 0:
            log(f"  …{len(segments)} segments")
    return Transcript(segments, getattr(info, "language", language) or language,
                      f"faster-whisper:{model_size}")


# ---------------------------------------------------------------------------
# HyperFrames transcribe (secondary — needs whisper.cpp)
# ---------------------------------------------------------------------------
def _transcribe_hyperframes(path: str, language: str, model_size: str,
                            log=None) -> Transcript:
    if not tools.NPX:
        raise TranscribeUnavailable("npx not found")
    # HyperFrames only ships .en + large-v3; map our size onto something valid.
    hf_model = "large-v3" if language not in ("en", "") else f"{model_size}.en"
    if log:
        log(f"hyperframes transcribe ({hf_model})…")
    res = tools.run(
        [tools.NPX, "--yes", "hyperframes@0.6.80", "transcribe",
         "-m", hf_model, "-l", language or "en", "--json", path],
        timeout=3600, log=log,
    )
    out = (res.stdout or "").strip()
    # The CLI prints a single JSON object; find the last JSON line.
    payload = None
    for line in reversed(out.splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                payload = json.loads(line)
                break
            except json.JSONDecodeError:
                continue
    if not payload or payload.get("ok") is False:
        err = (payload or {}).get("error", "unknown error")
        raise TranscribeUnavailable(f"hyperframes transcribe: {err}")
    return _parse_hyperframes_payload(payload, language)


def _parse_hyperframes_payload(payload: dict, language: str) -> Transcript:
    raw_segments = payload.get("segments") or payload.get("result", {}).get("segments") or []
    segments: list[Segment] = []
    for s in raw_segments:
        words = [
            Word(float(w["start"]), float(w["end"]), str(w.get("word", w.get("text", ""))))
            for w in s.get("words", [])
            if w.get("start") is not None and w.get("end") is not None
        ]
        segments.append(Segment(
            float(s.get("start", 0)), float(s.get("end", 0)),
            str(s.get("text", "")).strip(), words,
        ))
    return Transcript(segments, language, "hyperframes")


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------
def transcribe(path: str, *, language: str = "th", model_size: str = "small",
               log=None) -> Transcript:
    """Return a :class:`Transcript`, or raise :class:`TranscribeUnavailable`."""
    errors = []
    for name, fn in (
        ("faster-whisper", _transcribe_faster_whisper),
        ("hyperframes", _transcribe_hyperframes),
    ):
        try:
            return fn(path, language, model_size, log)
        except TranscribeUnavailable as e:
            errors.append(f"{name}: {e}")
        except ImportError as e:
            errors.append(f"{name}: not installed ({e})")
        except Exception as e:  # noqa: BLE001 - we want to try the next engine
            errors.append(f"{name}: {e}")
            if log:
                log(f"⚠️ {name} failed: {e}")
    raise TranscribeUnavailable("; ".join(errors) or "no engine available")


def available() -> bool:
    try:
        import faster_whisper  # noqa: F401
        return True
    except ImportError:
        return bool(tools.NPX)
