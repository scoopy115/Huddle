"""NVIDIA Parakeet TDT 0.6B v3 on Apple Silicon through MLX (`parakeet-mlx`).

A second transcription family next to Whisper: 25 European languages (Dutch included),
punctuation and capitalisation from the model, token timestamps from the TDT decoder, and
roughly Whisper-turbo speed on Metal. The provider plugs into the shared VAD → language →
chunk path of `transcription.py` by duck-typing a faster-whisper model handle, so the live
transcription and the mixed-language path need no changes.

Two things `parakeet-mlx` wants that the packaged engine does not have: `ffmpeg` on the PATH
(its `transcribe()` shells out to decode audio — we feed it samples we decoded ourselves and
call `generate()` directly) and `librosa` (only for `librosa.filters.mel`; a numpy copy of that
filterbank is registered as a stand-in module when librosa is absent, and `tests/` checks the
two agree). The model itself does not know which language it heard; the tiny Whisper detector
of the shared path supplies the language tag, as it does for MLX Whisper.
"""
from __future__ import annotations

import logging
import sys
import traceback
import types

import numpy as np

from .base import ProviderError
from .transcription import FasterWhisperProvider, _DictSeg, _Info, split_at_pauses, vocab_prompt

log = logging.getLogger(__name__)

# Model repo whose weights `parakeet-mlx` loads; also the marketplace download.
DEFAULT_REPO = "mlx-community/parakeet-tdt-0.6b-v3"
_STATE: dict[str, object] = {}


# ---- librosa stand-in -------------------------------------------------------------------- #
def mel_filterbank(sr: int, n_fft: int, n_mels: int, fmin: float = 0.0, fmax: float | None = None) -> np.ndarray:
    """`librosa.filters.mel(sr, n_fft, n_mels, fmin, fmax, htk=False, norm="slaney")`, in numpy:
    Slaney's mel scale (linear below 1 kHz, log above), triangular filters, area-normalised."""
    fmax = float(fmax if fmax is not None else sr / 2)

    def hz_to_mel(f):
        f = np.asarray(f, dtype=np.float64)
        f_sp, min_log_hz = 200.0 / 3, 1000.0
        min_log_mel = min_log_hz / f_sp
        logstep = np.log(6.4) / 27.0
        mels = f / f_sp
        return np.where(f >= min_log_hz, min_log_mel + np.log(np.maximum(f, 1e-9) / min_log_hz) / logstep, mels)

    def mel_to_hz(m):
        m = np.asarray(m, dtype=np.float64)
        f_sp, min_log_hz = 200.0 / 3, 1000.0
        min_log_mel = min_log_hz / f_sp
        logstep = np.log(6.4) / 27.0
        return np.where(m >= min_log_mel, min_log_hz * np.exp(logstep * (m - min_log_mel)), f_sp * m)

    fft_freqs = np.linspace(0, sr / 2, 1 + n_fft // 2)
    mel_f = mel_to_hz(np.linspace(hz_to_mel(fmin), hz_to_mel(fmax), n_mels + 2))
    fdiff = np.diff(mel_f)
    ramps = mel_f[:, None] - fft_freqs[None, :]
    lower = -ramps[:-2] / fdiff[:-1, None]
    upper = ramps[2:] / fdiff[1:, None]
    weights = np.maximum(0, np.minimum(lower, upper))
    enorm = 2.0 / (mel_f[2:n_mels + 2] - mel_f[:n_mels])
    return (weights * enorm[:, None]).astype(np.float32)


def _ensure_librosa_stub() -> None:
    """Register a minimal `librosa` (just `filters.mel`) when the real one is not installed."""
    try:
        import librosa  # noqa: F401
        return
    except ImportError:
        pass
    filters = types.ModuleType("librosa.filters")

    def mel(*, sr, n_fft, n_mels=128, fmin=0.0, fmax=None, htk=False, norm="slaney", dtype=np.float32):
        if htk or norm != "slaney":
            raise ValueError("stand-in mel filterbank supports Slaney scale/norm only")
        return mel_filterbank(sr, n_fft, n_mels, fmin, fmax).astype(dtype)

    filters.mel = mel
    pkg = types.ModuleType("librosa")
    pkg.filters = filters
    sys.modules["librosa"] = pkg
    sys.modules["librosa.filters"] = filters


def parakeet_available() -> bool:
    """Whether `parakeet-mlx` imports (cached; a failed nanobind import is never retried)."""
    if "ok" in _STATE:
        return bool(_STATE["ok"])
    try:
        _ensure_librosa_stub()
        import parakeet_mlx  # noqa: F401
        _STATE["ok"] = True
    except Exception as e:
        log.warning("Parakeet (MLX) unavailable: %r", e)
        _STATE["ok"] = False
    return bool(_STATE["ok"])


def release() -> None:
    _STATE.pop("model", None)
    _STATE.pop("model_path", None)


def _load_model(path: str):
    if _STATE.get("model_path") == path and "model" in _STATE:
        return _STATE["model"]
    _ensure_librosa_stub()
    from parakeet_mlx import from_pretrained
    model = from_pretrained(path)
    _STATE["model"], _STATE["model_path"] = model, path
    return model


class _ParakeetHandle:
    """Duck-types the faster-whisper model's `transcribe(clip, language=…)` for the shared path.
    The prompt (vocabulary) is accepted and ignored: Parakeet has no prompt input."""

    def __init__(self, model, prompt: str | None):
        self.model = model
        self._initial_prompt = prompt

    def transcribe(self, clip, language=None, beam_size=5, vad_filter=False, word_timestamps=True,
                   initial_prompt=None, condition_on_previous_text=False):
        import mlx.core as mx
        from parakeet_mlx.audio import get_logmel
        audio = np.asarray(clip, dtype=np.float32)
        if len(audio) < 1600:   # < 0.1 s: nothing to say
            return [], _Info(language)
        mel = get_logmel(mx.array(audio), self.model.preprocessor_config)
        result = self.model.generate(mel)[0]
        segs = [_DictSeg(_sentence_dict(s)) for s in result.sentences if s.tokens]
        return segs, _Info(language)


def _sentence_dict(sentence) -> dict:
    """AlignedSentence → the dict shape `_DictSeg` reads. Tokens are sentencepiece pieces; a
    leading space marks the start of a word."""
    words: list[dict] = []
    for tok in sentence.tokens:
        text = tok.text
        if not text.strip():
            continue
        if text.startswith(" ") or not words:
            words.append({"word": text, "start": tok.start, "end": tok.end, "probability": tok.confidence})
        else:
            w = words[-1]
            w["word"] += text
            w["end"] = tok.end
            w["probability"] = min(w["probability"], tok.confidence) if w.get("probability") is not None else tok.confidence
    for w in words:
        w["word"] = " " + w["word"].strip()
    text = "".join(w["word"] for w in words)
    return {"start": sentence.start, "end": sentence.end, "text": text, "words": words,
            "avg_logprob": None}


class ParakeetMlxProvider(FasterWhisperProvider):
    id = "parakeet_mlx"

    def __init__(self, model_path: str, vocab: list[str] | None = None):
        super().__init__(model=model_path, device="cpu", vocab=vocab)
        self.model_path = model_path

    def load(self):
        if not parakeet_available():
            raise ProviderError("Parakeet (MLX) is not available in this build.")
        try:
            return _ParakeetHandle(_load_model(self.model_path), vocab_prompt(self._vocab)), None
        except ProviderError:
            raise
        except Exception as e:
            raise ProviderError(f"The Parakeet model at '{self.model_path}' could not be loaded.",
                                detail=traceback.format_exc()) from e

    def transcribe(self, wav_path: str, language: str | None, progress=None, cancelled=None, start_sec: float = 0.0):
        from .base import TranscriptResult
        from .transcription import Cancelled
        handle, _ = self.load()
        try:
            forced = None if language in (None, "auto") else language
            out, lang = self._transcribe_mixed(handle, wav_path, None, progress, cancelled, start_sec, forced=forced)
        except (ProviderError, Cancelled):
            raise
        except Exception as e:
            raise ProviderError("Transcription failed while processing the audio.", detail=traceback.format_exc()) from e
        out = split_at_pauses(out)
        return TranscriptResult(segments=out, language=lang, provider=self.id, model=str(self.model_path))
