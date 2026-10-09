"""NVIDIA Parakeet TDT 0.6B v3 on Windows and Linux through sherpa-onnx (ONNX Runtime).

The same family as `parakeet.py` (which runs the MLX build on Apple Silicon): 25 European
languages, punctuation and capitalisation from the model, token timestamps from the TDT decoder.
sherpa-onnx is already in the engine for speaker separation, so nothing new is shipped; the model
is a marketplace download (the int8 export from the sherpa-onnx releases, ~490 MB) that lands in
``<models>/parakeet/<name>/`` as encoder, decoder, joiner and ``tokens.txt``.

Like the MLX provider it plugs into the shared VAD → language → chunk path of `transcription.py`
by duck-typing a faster-whisper model handle: every chunk (≤ 30 s of speech) is decoded on its
own, token timestamps become word timestamps, and the tiny Whisper detector supplies the
language tag (Parakeet itself does not say what it heard). CPU only: the PyPI sherpa-onnx wheel
has no CUDA provider, and the model runs well above real time on a few cores anyway.
"""
from __future__ import annotations

import logging
import os
import traceback
from pathlib import Path

import numpy as np

from .base import ProviderError
from .transcription import FasterWhisperProvider, _DictSeg, _Info, split_at_pauses

log = logging.getLogger(__name__)

# The marketplace download: sherpa-onnx's int8 export of parakeet-tdt-0.6b-v3.
ARCHIVE_NAME = "sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8"
ARCHIVE_URL = f"https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/{ARCHIVE_NAME}.tar.bz2"
ARCHIVE_SIZE = 487_000_000
MODEL_FILES = ("encoder.int8.onnx", "decoder.int8.onnx", "joiner.int8.onnx", "tokens.txt")
# Timestamps come per token at the encoder's frame rate; a token with no successor ends here later.
LAST_TOKEN_SEC = 0.24

_STATE: dict[str, object] = {}


def is_model_dir(path: Path) -> bool:
    return all((path / f).exists() for f in MODEL_FILES)


def release() -> None:
    _STATE.pop("recognizer", None)
    _STATE.pop("model_path", None)


def _load(path: str):
    if _STATE.get("model_path") == path and "recognizer" in _STATE:
        return _STATE["recognizer"]
    import sherpa_onnx
    d = Path(path)
    rec = sherpa_onnx.OfflineRecognizer.from_transducer(
        encoder=str(d / "encoder.int8.onnx"), decoder=str(d / "decoder.int8.onnx"), joiner=str(d / "joiner.int8.onnx"),
        tokens=str(d / "tokens.txt"), num_threads=max(2, min(8, (os.cpu_count() or 4) // 2)),
        sample_rate=16000, feature_dim=80, model_type="nemo_transducer", decoding_method="greedy_search")
    _STATE["recognizer"], _STATE["model_path"] = rec, path
    return rec


def words_from_tokens(tokens: list[str], starts: list[float], end: float) -> list[dict]:
    """Sentencepiece pieces with their start times → words. A piece starting with "▁" (or a
    space) begins a word; a word ends where the next one starts, the last at ``end``."""
    words: list[dict] = []
    for i, tok in enumerate(tokens):
        start = float(starts[i]) if i < len(starts) else (words[-1]["end"] if words else 0.0)
        piece = tok.replace("▁", " ")
        if not piece.strip():
            continue
        if piece.startswith(" ") or not words:
            if words:
                words[-1]["end"] = max(words[-1]["start"], start)
            words.append({"word": " " + piece.strip(), "start": start, "end": start, "probability": None})
        else:
            words[-1]["word"] += piece
    if words:
        words[-1]["end"] = max(words[-1]["start"] + 0.05, end)
    return words


class _ParakeetOnnxHandle:
    """Duck-types the faster-whisper model's `transcribe(clip, language=…)` for the shared path.
    The prompt (vocabulary) is accepted and ignored: Parakeet has no prompt input."""

    def __init__(self, recognizer, prompt: str | None):
        self.model = recognizer
        self._initial_prompt = prompt

    def transcribe(self, clip, language=None, beam_size=5, vad_filter=False, word_timestamps=True,
                   initial_prompt=None, condition_on_previous_text=False):
        audio = np.asarray(clip, dtype=np.float32)
        if len(audio) < 1600:   # < 0.1 s: nothing to say
            return [], _Info(language)
        stream = self.model.create_stream()
        stream.accept_waveform(16000, audio)
        self.model.decode_stream(stream)
        r = stream.result
        tokens = list(getattr(r, "tokens", []) or [])
        starts = list(getattr(r, "timestamps", []) or [])
        text = (getattr(r, "text", "") or "").strip()
        if not text:
            return [], _Info(language)
        clip_end = len(audio) / 16000
        last = (float(starts[-1]) + LAST_TOKEN_SEC) if starts else clip_end
        words = words_from_tokens(tokens, starts, min(clip_end, last))
        seg = {"start": words[0]["start"] if words else 0.0, "end": words[-1]["end"] if words else clip_end,
               "text": "".join(w["word"] for w in words) if words else " " + text, "words": words, "avg_logprob": None}
        return [_DictSeg(seg)], _Info(language)


class ParakeetOnnxProvider(FasterWhisperProvider):
    id = "parakeet_onnx"

    def __init__(self, model_path: str, vocab: list[str] | None = None):
        super().__init__(model=model_path, device="cpu", vocab=vocab)
        self.model_path = model_path

    def load(self):
        if not is_model_dir(Path(self.model_path)):
            raise ProviderError(f"The Parakeet model at '{self.model_path}' is incomplete. Download it again under Settings → Models.")
        try:
            return _ParakeetOnnxHandle(_load(self.model_path), None), None
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
