from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Word:
    start: float
    end: float
    word: str
    confidence: float | None = None


@dataclass
class Segment:
    start: float
    end: float
    text: str
    confidence: float | None = None
    words: list[Word] = field(default_factory=list)
    speaker_label: str | None = None
    language: str | None = None


@dataclass
class TranscriptResult:
    segments: list[Segment]
    language: str | None
    provider: str
    model: str


@dataclass
class DiarizationResult:
    labels: list[str]                       # one per input segment
    embeddings: dict[str, list[float]]      # label -> mean embedding
    provider: str


class ProviderError(RuntimeError):
    """User-facing error. ``detail`` carries the technical cause for diagnostics."""

    def __init__(self, message: str, detail: str | None = None):
        super().__init__(message)
        self.detail = detail
