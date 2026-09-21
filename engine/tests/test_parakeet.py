"""Parakeet (MLX) provider: the librosa stand-in and the token → word conversion. The model
itself is not downloaded in tests."""
from dataclasses import dataclass

import numpy as np
import pytest

from huddle_engine.providers import parakeet
from huddle_engine.providers.transcription import _DictSeg


def test_mel_filterbank_matches_librosa():
    librosa = pytest.importorskip("librosa")
    a = librosa.filters.mel(sr=16000, n_fft=512, n_mels=128, fmin=0, fmax=8000, norm="slaney")
    b = parakeet.mel_filterbank(16000, 512, 128, 0, 8000)
    assert a.shape == b.shape == (128, 257)
    assert float(np.abs(a - b).max()) < 1e-6


@dataclass
class _Tok:
    text: str
    start: float
    end: float
    confidence: float = 1.0


@dataclass
class _Sent:
    text: str
    tokens: list
    start: float = 0.0
    end: float = 0.0


def test_tokens_become_words_with_timestamps():
    toks = [_Tok(" L", 0.0, 0.1), _Tok("et", 0.1, 0.2), _Tok(" me", 0.2, 0.4, 0.8), _Tok(" sh", 0.4, 0.5), _Tok("are", 0.5, 0.7, 0.6)]
    d = parakeet._sentence_dict(_Sent("Let me share", toks, 0.0, 0.7))
    seg = _DictSeg(d)
    assert seg.text == " Let me share"
    assert [(w.word, round(w.start, 2), round(w.end, 2)) for w in seg.words] == [(" Let", 0.0, 0.2), (" me", 0.2, 0.4), (" share", 0.4, 0.7)]
    # a word's confidence is its weakest piece
    assert seg.words[2].probability == 0.6


def test_resolver_never_picks_parakeet_automatically(db, cfg):
    from huddle_engine.discovery.registry import Registry
    from huddle_engine.resolver import ResolverContext, _transcription_provider_id, resolve_transcription
    from huddle_engine.schemas import LocalModel
    reg = Registry(db, cfg)
    para = LocalModel(id="huggingface:mlx-community/parakeet-tdt-0.6b-v3", name="mlx-community/parakeet-tdt-0.6b-v3",
                      family="parakeet", task="transcription", source="huggingface", format="MLX", path="/x",
                      compatible_runtimes=["parakeet-mlx"], compatible=True)
    whisper = LocalModel(id="huggingface:Systran/faster-whisper-small", name="Systran/faster-whisper-small",
                         family="whisper", task="transcription", source="huggingface", format="CTranslate2", path="/y",
                         compatible_runtimes=["faster-whisper"], compatible=True, meta={"whisperSize": "small"})
    reg._models = {m.id: m for m in (para, whisper)}  # bypass the scan
    reg.models = lambda task=None: [m for m in (para, whisper) if task is None or m.task == task]  # type: ignore[method-assign]
    reg.model = lambda mid: {m.id: m for m in (para, whisper)}.get(mid)  # type: ignore[method-assign]
    auto = resolve_transcription(ResolverContext(registry=reg, settings={}, memory_bytes=None))
    assert auto.model.id == whisper.id and auto.provider == "faster_whisper"
    chosen = resolve_transcription(ResolverContext(registry=reg, settings={"models.whisper": para.id}, memory_bytes=None))
    assert chosen.model.id == para.id and chosen.provider == "parakeet_mlx"
    assert _transcription_provider_id(para) == "parakeet_mlx"
