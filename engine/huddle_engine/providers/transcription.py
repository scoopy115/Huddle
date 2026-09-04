"""Transcription providers.

``FasterWhisperProvider`` loads CTranslate2 models with faster-whisper
(download root, CPU fallback) and vocabulary prompt.

One language per transcript (product decision): people do not switch languages inside a
meeting, and per-utterance detection produced mixed-up transcripts. In ``auto`` mode we

1. run the VAD to find speech regions,
2. detect the language on a sample of regions with the *tiny* model (cheap) and take the
   majority weighted by duration — once for the whole recording,
3. group regions into chunks ≤ MAX_CHUNK_SEC that end only at silence and transcribe them
   with the main model and that single forced language.

The user can override the language per meeting (re-transcribes). Whisper pads every
window to 30 s, so long chunks keep CPU cost near one encoder pass per ~28 s of speech.
"""
from __future__ import annotations

import gc
import logging
import math
import traceback
from collections.abc import Callable
from itertools import pairwise

import numpy as np

from .base import ProviderError, Segment, TranscriptResult, Word

log = logging.getLogger(__name__)

MAX_CHUNK_SEC = 28.0
MIN_GAP_SEC = 0.3
DETECT_MODEL = "tiny"          # language detection only; ~75 MB, downloaded on first use
ProgressFn = Callable[[float], None]
CancelFn = Callable[[], bool]


def _conf(lp) -> float | None:
    if lp is None:
        return None
    try:
        return round(max(0.0, min(1.0, math.exp(float(lp)))), 3)
    except (ValueError, OverflowError):
        return None


def vocab_prompt(terms: list[str], limit: int = 60) -> str | None:
    """Whisper `initial_prompt` from the user's vocabulary, so names and jargon are spelled the
    way the user writes them. Capped so it never crowds the context window."""
    terms = [t.strip() for t in terms if t and t.strip()][:limit]
    return ("Glossary of names and terms: " + ", ".join(terms) + ".") if terms else None


def release_models() -> None:
    """Drop every cached model so an idle engine holds no model memory.

    faster-whisper models are created per call and die with their provider, but mlx_whisper keeps
    the last model in a module-level holder and MLX keeps freed Metal buffers in its own cache;
    together they left several GB resident between meetings. Safe to call at any time."""
    freed = 0
    try:
        import importlib
        import sys

        if "mlx_whisper.transcribe" in sys.modules:
            holder = importlib.import_module("mlx_whisper.transcribe").ModelHolder
            holder.model = None
            holder.model_path = None
        if "mlx.core" in sys.modules:
            import mlx.core as mx
            before = mx.get_active_memory() + mx.get_cache_memory()
            mx.clear_cache()
            freed = before - (mx.get_active_memory() + mx.get_cache_memory())
    except Exception:  # never let housekeeping fail a job
        log.debug("model release skipped", exc_info=True)
    gc.collect()
    try:
        import mlx.core as mx  # a second pass: gc may have dropped the last references to buffers
        mx.clear_cache()
    except Exception:
        pass
    if freed:
        log.info("released %.1f GB of model memory", freed / 1024**3)


def load_whisper(model_name: str, device: str = "auto", compute_type: str | None = None):
    """A faster-whisper `WhisperModel`; falls back to CPU int8 when the requested device or
    precision is not available on this machine."""
    from faster_whisper import WhisperModel

    dev = "auto" if not device or device == "auto" else device
    try:
        return WhisperModel(model_name, device=dev, compute_type=compute_type or "int8")
    except Exception:
        if dev == "cpu" and (compute_type or "int8") == "int8":
            raise
        return WhisperModel(model_name, device="cpu", compute_type="int8")


class _CT2Handle:
    """What the transcription code expects from a loaded CTranslate2 model."""

    def __init__(self, model, prompt: str | None):
        self.model = model
        self._initial_prompt = prompt


class Cancelled(Exception):
    pass


class FasterWhisperProvider:
    """CTranslate2 Whisper. CPU int8 on Apple Silicon (no Metal in CT2); CUDA fp16 elsewhere."""

    id = "faster_whisper"

    def __init__(self, model: str, device: str = "auto", compute_type: str | None = None,
                 vocab: list[str] | None = None):
        self.model = model
        self._device = device
        self._compute_type = compute_type
        self._vocab = vocab or []

    def _transcriber(self, model: str | None = None) -> _CT2Handle:
        return _CT2Handle(load_whisper(model or self.model, self._device, self._compute_type), vocab_prompt(self._vocab))

    def load(self):
        try:
            t = self._transcriber()
            return t, t.model
        except Exception as e:
            raise ProviderError(f"The transcription model '{self.model}' could not be loaded.",
                                detail=traceback.format_exc()) from e

    def transcribe(self, wav_path: str, language: str | None, progress: ProgressFn | None = None,
                   cancelled: CancelFn | None = None, start_sec: float = 0.0) -> TranscriptResult:
        t, model = self.load()
        auto = language in (None, "auto")
        try:
            if auto:
                out, lang = self._transcribe_mixed(model, wav_path, t._initial_prompt, progress, cancelled, start_sec)
            else:
                segments, info = model.transcribe(
                    wav_path, language=language, beam_size=5, vad_filter=True, word_timestamps=True,
                    initial_prompt=t._initial_prompt, condition_on_previous_text=False)
                out = _collect(segments, 0.0)
                lang = getattr(info, "language", None)
                for s in out:
                    s.language = lang
                if progress:
                    progress(1.0)
        except (ProviderError, Cancelled):
            raise
        except Exception as e:
            raise ProviderError("Transcription failed while processing the audio.",
                                detail=traceback.format_exc()) from e
        out = split_at_pauses(out)
        return TranscriptResult(segments=out, language=lang, provider=self.id, model=str(self.model))

    # ---- mixed-language path ----------------------------------------------------- #
    def _detector(self):
        return self._transcriber(DETECT_MODEL).model

    def _transcribe_mixed(self, model, wav_path: str, prompt: str | None, progress: ProgressFn | None,
                          cancelled: CancelFn | None, start_sec: float) -> tuple[list[Segment], str | None]:
        from faster_whisper.audio import decode_audio
        from faster_whisper.vad import VadOptions, get_speech_timestamps

        sr = 16000
        audio = decode_audio(wav_path, sampling_rate=sr)
        if start_sec > 0:
            audio = audio[int(start_sec * sr):]
        speech = get_speech_timestamps(audio, VadOptions(min_silence_duration_ms=300, speech_pad_ms=200))
        regions = [(s["start"] / sr, s["end"] / sr) for s in speech]
        if not regions:
            return [], None
        lang = pick_language(self._detector(), audio, regions, sr, cancelled)
        chunks = [(a, b, lang) for a, b in group_regions(regions)]
        total = sum(b - a for a, b, _ in chunks) or 1.0
        done = 0.0
        out: list[Segment] = []
        lang_time: dict[str, float] = {}
        for c0, c1, lang in chunks:
            if cancelled and cancelled():
                raise Cancelled()
            clip = audio[int(c0 * sr):int(c1 * sr)]
            segments, _info = model.transcribe(
                clip, language=lang, beam_size=5, vad_filter=False, word_timestamps=True,
                initial_prompt=prompt, condition_on_previous_text=False)
            segs = _collect(segments, c0 + start_sec)
            for s in segs:
                s.language = lang
            out.extend(segs)
            lang_time[lang] = lang_time.get(lang, 0.0) + (c1 - c0)
            done += c1 - c0
            if progress:
                progress(min(0.99, done / total))
        total_t = sum(lang_time.values()) or 1.0
        ordered = [line for line, t in sorted(lang_time.items(), key=lambda kv: -kv[1]) if t / total_t >= 0.10]
        if progress:
            progress(1.0)
        return out, (",".join(ordered) if ordered else None)


class MlxWhisperProvider(FasterWhisperProvider):
    """Whisper on Apple Silicon through MLX (Metal). ~10–20× realtime for large-v3-turbo on an
    M-series Mac versus ~2× for CTranslate2 on the CPU. Language detection per region still uses
    the tiny CTranslate2 model (cheap, and MLX has no cheap detector API); transcription of each
    language-homogeneous chunk runs on the GPU with a forced language."""

    id = "mlx_whisper"

    def __init__(self, model_path: str, vocab: list[str] | None = None):
        super().__init__(model=model_path, device="cpu", vocab=vocab)
        self.model_path = model_path

    def load(self):
        try:
            import mlx_whisper  # noqa: F401
        except ImportError as e:
            raise ProviderError("MLX Whisper is not installed in this build.", detail=str(e)) from e
        return _MlxHandle(self.model_path, vocab_prompt(self._vocab)), None

    def transcribe(self, wav_path: str, language: str | None, progress: ProgressFn | None = None,
                   cancelled: CancelFn | None = None, start_sec: float = 0.0) -> TranscriptResult:
        handle, _ = self.load()
        try:
            out, lang = self._transcribe_mixed(handle, wav_path, handle._initial_prompt, progress, cancelled, start_sec)
        except (ProviderError, Cancelled):
            raise
        except Exception as e:
            raise ProviderError("Transcription failed while processing the audio.", detail=traceback.format_exc()) from e
        out = split_at_pauses(out)
        return TranscriptResult(segments=out, language=lang, provider=self.id, model=str(self.model_path))


class _MlxHandle:
    """Duck-types the faster-whisper model's ``transcribe`` for the shared mixed-language path."""

    def __init__(self, model_path: str, prompt: str | None):
        self.model_path = model_path
        self._initial_prompt = prompt

    def transcribe(self, clip, language=None, beam_size=5, vad_filter=False, word_timestamps=True,
                   initial_prompt=None, condition_on_previous_text=False):
        import mlx_whisper
        res = mlx_whisper.transcribe(np.asarray(clip, dtype=np.float32), path_or_hf_repo=self.model_path,
                                     language=language, word_timestamps=word_timestamps,
                                     initial_prompt=initial_prompt, condition_on_previous_text=condition_on_previous_text)
        segs = [_DictSeg(x) for x in res.get("segments", [])]
        return segs, _Info(res.get("language"))


class _Info:
    def __init__(self, language):
        self.language = language


class _DictWord:
    def __init__(self, w):
        self.start, self.end, self.word = float(w["start"]), float(w["end"]), w["word"]
        self.probability = w.get("probability")


class _DictSeg:
    def __init__(self, x):
        self.start, self.end, self.text = float(x["start"]), float(x["end"]), x["text"]
        self.avg_logprob = x.get("avg_logprob")
        self.words = [_DictWord(w) for w in x.get("words", [])]


_MLX_STATE: dict[str, bool] = {}


def mlx_available() -> bool:
    """Whether MLX Whisper can be imported. The result is cached: a failed import of a
    nanobind-based extension must never be retried in the same process (the second attempt
    re-registers its types and aborts the interpreter), and the failure is logged once."""
    if "ok" in _MLX_STATE:
        return _MLX_STATE["ok"]
    try:
        import mlx_whisper  # noqa: F401
        _MLX_STATE["ok"] = True
    except Exception as e:  # missing wheel, broken bundle, unsupported Mac
        log.warning("MLX Whisper unavailable: %r", e)
        _MLX_STATE["ok"] = False
    return _MLX_STATE["ok"]


def make_transcription_provider(model, vocab: list[str] | None, device: str = "auto"):
    """Pick the runtime for a resolved LocalModel: MLX for MLX-format models, CTranslate2 otherwise."""
    ref = model.path or model.name
    if model.format == "MLX":
        return MlxWhisperProvider(ref, vocab=vocab)
    dev = "cpu" if device in ("auto", "cpu", "apple-gpu-metal") else device
    return FasterWhisperProvider(model=ref, device=dev, vocab=vocab)


def pick_language(detector, audio: np.ndarray, regions: list[tuple[float, float]], sr: int = 16000,
                  cancelled: CancelFn | None = None, max_samples: int = 12) -> str:
    """One language for the whole recording: detect on up to `max_samples` speech regions
    spread over the meeting (≥1.5 s each) and take the duration-weighted majority."""
    cands = [(a, b) for a, b in regions if b - a >= 1.5] or regions
    if not cands:
        return "en"
    step = max(1, len(cands) // max_samples)
    votes: dict[str, float] = {}
    for a, b in cands[::step][:max_samples]:
        if cancelled and cancelled():
            raise Cancelled()
        lang = detect_language(detector, audio[int(a * sr):int(b * sr)], fallback="")
        if lang:
            votes[lang] = votes.get(lang, 0.0) + (b - a)
    return max(votes, key=votes.get) if votes else "en"


def detect_language(detector, clip: np.ndarray, fallback: str = "en") -> str:
    """Language of one speech region using the tiny model. Very short clips inherit `fallback`."""
    if len(clip) < 16000 * 0.6:
        return fallback
    try:
        lang, prob, _ = detector.detect_language(clip)
        return lang if prob >= 0.3 else fallback
    except Exception as e:  # pragma: no cover
        log.warning("language detection failed: %s", e)
        return fallback


def group_regions_by_language(regions: list[tuple[float, float]], langs: list[str],
                              max_len: float = MAX_CHUNK_SEC, min_gap: float = MIN_GAP_SEC,
                              ) -> list[tuple[float, float, str]]:
    """Group consecutive VAD regions into chunks that (a) share one language, (b) stay
    ≤ max_len, and (c) end only at silence. A region longer than max_len is its own
    chunk. Very short regions (< 1.5 s) take the language of their neighbour so a
    stray "ok" does not split a chunk. Pure; unit-tested."""
    if not regions:
        return []
    # smooth languages of tiny regions
    smoothed = list(langs)
    for i, (a, b) in enumerate(regions):
        if b - a < 1.5:
            prev_l = smoothed[i - 1] if i > 0 else None
            next_l = langs[i + 1] if i + 1 < len(langs) else None
            smoothed[i] = prev_l or next_l or langs[i]
    chunks: list[list] = []
    for (start, end), lang in zip(regions, smoothed):
        if not chunks:
            chunks.append([start, end, lang])
            continue
        cur = chunks[-1]
        gap = start - cur[1]
        same_lang = lang == cur[2]
        if (gap < min_gap and same_lang) or (same_lang and (end - cur[0]) <= max_len):
            cur[1] = end
        else:
            chunks.append([start, end, lang])
    return [(round(a, 3), round(b, 3), lang) for a, b, lang in chunks]


def group_regions(regions: list[tuple[float, float]], max_len: float = MAX_CHUNK_SEC,
                  min_gap: float = MIN_GAP_SEC) -> list[tuple[float, float]]:
    """Language-agnostic grouping (kept for callers/tests that do not need languages)."""
    return [(a, b) for a, b, _ in group_regions_by_language(regions, ["x"] * len(regions), max_len, min_gap)]


def _collect(segments, offset: float) -> list[Segment]:
    out: list[Segment] = []
    for seg in segments:
        text = seg.text.strip()
        if not text:
            continue
        words = [Word(start=offset + float(w.start), end=offset + float(w.end), word=w.word.strip(),
                      confidence=(round(float(w.probability), 3) if w.probability is not None else None))
                 for w in (seg.words or []) if w.word.strip()]
        out.append(Segment(start=offset + float(seg.start), end=offset + float(seg.end), text=text,
                           confidence=_conf(getattr(seg, "avg_logprob", None)), words=words))
    return out


def split_at_pauses(segments: list[Segment], min_gap: float = 0.55, max_len: float = 12.0,
                    min_words: int = 2) -> list[Segment]:
    """Split Whisper segments at word-level pauses so diarization (one label per segment)
    can follow speaker turns, and click-to-seek stays fine-grained. Pure; unit-tested."""
    out: list[Segment] = []
    for seg in segments:
        words = seg.words
        if len(words) < min_words * 2:
            out.append(seg)
            continue
        cuts: list[int] = []
        for i in range(1, len(words)):
            if words[i].start - words[i - 1].end >= min_gap and i >= min_words and len(words) - i >= min_words:
                cuts.append(i)
        if not cuts and (seg.end - seg.start) > max_len:
            gaps = [(words[i].start - words[i - 1].end, i) for i in range(min_words, len(words) - min_words + 1)]
            if gaps:
                cuts = [max(gaps)[1]]
        if not cuts:
            out.append(seg)
            continue
        bounds = [0, *cuts, len(words)]
        for a, b in pairwise(bounds):
            ws = words[a:b]
            text = " ".join(w.word for w in ws).strip()
            if not text:
                continue
            out.append(Segment(start=ws[0].start if a else seg.start, end=ws[-1].end if b < len(words) else seg.end,
                               text=text, confidence=seg.confidence, words=ws, language=seg.language))
    return out
