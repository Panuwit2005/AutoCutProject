"""Speech-to-text used *behind the scenes* for smarter cutting (v1.5).

AutoCut does NOT show subtitles.  It runs faster-whisper only to get **word-level
timestamps + VAD speech boundaries**, which let the cutter:
  • keep whole words (cut only in the gaps between words — never mid-word),
  • trim dead air precisely, and
  • pick the parts where someone is actually talking.

It's the `small` model (plenty for *timing*; text accuracy doesn't matter when
nothing is displayed), bundled for fully-offline use.  If the model/engine isn't
available the pipeline falls back to ffmpeg silence detection, so a job never
fails for lack of AI.
"""

from __future__ import annotations

from dataclasses import dataclass, field


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
    """No working speech-to-text engine is available."""


_FW_MODELS: dict[str, object] = {}


def _model(model_size: str):
    if model_size in _FW_MODELS:
        return _FW_MODELS[model_size]
    from faster_whisper import WhisperModel  # may raise ImportError
    m = WhisperModel(model_size, device="cpu", compute_type="int8")
    _FW_MODELS[model_size] = m
    return m


def transcribe(path: str, *, language: str = "th", model_size: str = "small",
               log=None) -> Transcript:
    """Return a :class:`Transcript` with word timings, or raise on failure."""
    try:
        model = _model(model_size)
    except ImportError as e:
        raise TranscribeUnavailable(f"faster-whisper not installed ({e})") from e
    if log:
        log("🤖 AI กำลังวิเคราะห์เสียง (เบื้องหลัง)…")
    try:
        segments_iter, info = model.transcribe(
            path,
            language=language or None,
            word_timestamps=True,        # precise word boundaries → no clipped words
            vad_filter=True,             # Silero VAD → accurate speech regions
            vad_parameters={"min_silence_duration_ms": 400},
            condition_on_previous_text=False,
            no_speech_threshold=0.6,
        )
        segments: list[Segment] = []
        for seg in segments_iter:
            words = [Word(float(w.start), float(w.end), w.word)
                     for w in (seg.words or [])
                     if w.start is not None and w.end is not None]
            segments.append(Segment(float(seg.start), float(seg.end),
                                    (seg.text or "").strip(), words))
    except Exception as e:  # noqa: BLE001
        raise TranscribeUnavailable(str(e)) from e
    return Transcript(segments, getattr(info, "language", language) or language,
                      f"faster-whisper:{model_size}")


def available() -> bool:
    try:
        import faster_whisper  # noqa: F401
        return True
    except ImportError:
        return False
