"""Transcript data types (speech-to-text was removed in v1.4).

AutoCut now selects clips purely from **silence/speech detection** (ffmpeg), so
there is no AI transcription, no model download, and nothing online — the app is
fully offline and much smaller.  These lightweight dataclasses are kept because
``analyze`` still uses the :class:`Transcript` shape for type clarity, and
``transcribe()``/``available()`` remain as no-ops so the rest of the pipeline
keeps its graceful "no transcript → cut by silence" path.
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
    """Speech-to-text is not part of this build."""


def transcribe(path: str, *, language: str = "th", model_size: str = "small",
               log=None) -> Transcript:
    """No transcription in this build — callers fall back to silence cutting."""
    raise TranscribeUnavailable("transcription disabled")


def available() -> bool:
    return False
