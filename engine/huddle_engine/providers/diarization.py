"""Diarization providers.

``SherpaDiarizationProvider`` is a real diarization pipeline: pyannote segmentation 3.0 finds speaker turns and speaker changes at frame level,
an ONNX speaker-embedding model describes each stretch of speech, and the embeddings are
clustered into people. Whisper's words are then assigned to turns, so a transcript segment
that contains two speakers is cut at the right word.

Output: ``DiarizationOutput``. Cluster labels are ``"Speaker N"`` by first appearance;
``embeddings`` holds one mean vector per label for voice recognition, tagged with the model
that produced it (vectors of different models are never compared).
"""
from __future__ import annotations

import traceback
from collections.abc import Callable
from dataclasses import dataclass, field

import numpy as np

from .base import ProviderError, Segment, Word

WIN_SEC = 3.0
HOP_SEC = 1.5

# sherpa: agglomerative (average-link, cosine) threshold on window embeddings. Calibrated on
# real meetings + the synthetic fixtures, see docs/DECISIONS.md.
DEFAULT_SHERPA_THRESHOLD = 0.60
# clusters with less speech than this are folded into the nearest big cluster
MIN_CLUSTER_SEC = 8.0
MIN_CLUSTER_SHARE = 0.03
# voice profiles created before the sherpa models carry no model tag; they are never compared with new ones
LEGACY_EMBEDDING_MODEL = "resemblyzer"


@dataclass
class DiarizedSegment:
    segment: Segment
    label: str


@dataclass
class DiarizationOutput:
    segments: list[DiarizedSegment]              # may be MORE than the input (splits)
    embeddings: dict[str, list[float]] = field(default_factory=dict)
    provider: str = "sherpa-onnx"
    embedding_model: str = LEGACY_EMBEDDING_MODEL
    notes: list[str] = field(default_factory=list)  # human-readable remarks for the stage detail


class _Cancelled(Exception):
    pass


# ---------------------------------------------------------------------------------------- #
# shared helpers (pure, unit-tested)
# ---------------------------------------------------------------------------------------- #
def merge_small_clusters(X: np.ndarray, labels: np.ndarray, dur: np.ndarray,
                         min_sec: float = MIN_CLUSTER_SEC, min_share: float = MIN_CLUSTER_SHARE) -> np.ndarray:
    """Fold clusters with little speech into the nearest big cluster (by centroid cosine).
    Average-link clustering tends to leave a few outlier windows as their own 'speaker'."""
    labels = labels.copy()
    total = float(dur.sum()) or 1.0
    for _ in range(20):
        ids = np.unique(labels)
        size = {k: float(dur[labels == k].sum()) for k in ids}
        big = [k for k in ids if size[k] >= max(min_sec, min_share * total)] or [max(ids, key=lambda k: size[k])]
        small = [k for k in ids if k not in big]
        if not small:
            break
        cents = {k: _unit(X[labels == k].mean(axis=0)) for k in big}
        for k in small:
            c = _unit(X[labels == k].mean(axis=0))
            labels[labels == k] = max(big, key=lambda b: float(c @ cents[b]))
    return labels


def limit_clusters(X: np.ndarray, labels: np.ndarray, max_count: int) -> np.ndarray:
    """Merge the two most similar clusters until at most `max_count` remain (speaker-count hint)."""
    labels = labels.copy()
    while len(np.unique(labels)) > max(1, max_count):
        ids = list(np.unique(labels))
        cents = {k: _unit(X[labels == k].mean(axis=0)) for k in ids}
        best, best_sim = None, -2.0
        for i in range(len(ids)):
            for j in range(i + 1, len(ids)):
                s = float(cents[ids[i]] @ cents[ids[j]])
                if s > best_sim:
                    best, best_sim = (ids[i], ids[j]), s
        a, b = best  # type: ignore[misc]
        labels[labels == b] = a
    return labels


def _unit(v: np.ndarray) -> np.ndarray:
    n = float(np.linalg.norm(v))
    return v / n if n > 0 else v


def label_for_time(t: float, turns: list[tuple[float, float, str]], tolerance: float = 1.0) -> str | None:
    """Label of the turn covering t; else the nearest turn edge within `tolerance` seconds."""
    best, best_gap = None, tolerance
    for a, b, lab in turns:
        if a <= t <= b:
            return lab
        gap = a - t if t < a else t - b
        if gap < best_gap:
            best, best_gap = lab, gap
    return best


def assign_words(seg: Segment, turns: list[tuple[float, float, str]], fallback: str, min_run: int = 2) -> list[DiarizedSegment]:
    """Cut a transcript segment where the speaker (per diarization turns) changes between words.
    Runs shorter than `min_run` words take the label of their predecessor, which removes
    single-word flicker while keeping genuine one-line interjections when the turn is clear."""
    if not seg.words:
        return [DiarizedSegment(seg, majority_overlap(seg.start, seg.end, turns) or fallback)]
    labels: list[str] = []
    for w in seg.words:
        lab = label_for_time((w.start + w.end) / 2, turns) or (labels[-1] if labels else None) or fallback
        labels.append(lab)
    i = 0
    while i < len(labels):
        j = i
        while j < len(labels) and labels[j] == labels[i]:
            j += 1
        if j - i < min_run and i > 0:
            for k in range(i, j):
                labels[k] = labels[i - 1]
        i = j
    out: list[DiarizedSegment] = []
    start = 0
    words = seg.words
    for k in range(1, len(words) + 1):
        if k == len(words) or labels[k] != labels[start]:
            ws: list[Word] = words[start:k]
            out.append(DiarizedSegment(
                Segment(start=ws[0].start if start else seg.start, end=ws[-1].end if k < len(words) else seg.end,
                        text=" ".join(w.word for w in ws).strip(), confidence=seg.confidence, words=ws,
                        speaker_label=None, language=seg.language),
                labels[start]))
            start = k
    return out


def majority_overlap(start: float, end: float, turns: list[tuple[float, float, str]]) -> str | None:
    votes: dict[str, float] = {}
    for a, b, lab in turns:
        ov = min(end, b) - max(start, a)
        if ov > 0:
            votes[lab] = votes.get(lab, 0.0) + ov
    return max(votes, key=votes.get) if votes else None


def bandwidth_note(audio: np.ndarray, sr: int) -> str | None:
    """Detects narrowband recordings (Bluetooth headset microphones deliver ~4 kHz of audio),
    where every speaker model works noticeably worse. Returns a remark for the stage detail."""
    n = min(len(audio), sr * 300)
    if n < sr * 10:
        return None
    x = audio[:n].astype(np.float32) * np.hanning(n).astype(np.float32)
    spec = np.abs(np.fft.rfft(x)) ** 2
    f = np.fft.rfftfreq(n, 1 / sr)
    total = float(spec.sum()) or 1.0
    high = float(spec[f > 4000].sum()) / total
    if high < 0.01:
        return "narrowband audio (Bluetooth headset microphone?) — speaker separation is less reliable"
    return None


# ---------------------------------------------------------------------------------------- #
# sherpa-onnx: pyannote segmentation + speaker embeddings
# ---------------------------------------------------------------------------------------- #
class SherpaDiarizationProvider:
    id = "sherpa-onnx"

    def __init__(self, segmentation_model: str, embedding_model: str, embedding_id: str,
                 threshold: float = DEFAULT_SHERPA_THRESHOLD, speaker_count: int | None = None,
                 max_speakers: int = 8, threads: int = 4):
        self.segmentation_model = segmentation_model
        self.embedding_model = embedding_model
        self.embedding_id = embedding_id
        self.threshold = threshold
        self.speaker_count = speaker_count
        self.max_speakers = max_speakers
        self.threads = threads

    def diarize(self, wav_path: str, segments: list[Segment], progress: Callable[[float], None] | None = None,
                cancelled: Callable[[], bool] | None = None) -> DiarizationOutput:
        import sherpa_onnx
        import soundfile as sf
        from sklearn.cluster import AgglomerativeClustering

        if not segments:
            return DiarizationOutput(segments=[], provider=self.id, embedding_model=self.embedding_id)
        try:
            audio, sr = sf.read(wav_path, dtype="float32")
            if getattr(audio, "ndim", 1) > 1:
                audio = audio.mean(axis=1)
            if sr != 16000:
                from math import gcd

                from scipy.signal import resample_poly
                g = gcd(16000, sr)
                audio = resample_poly(audio, 16000 // g, sr // g).astype(np.float32)
                sr = 16000
            notes = [n for n in [bandwidth_note(audio, sr)] if n]

            # 1. speaker turns (pyannote segmentation; sherpa's own clustering only serves to
            #    stitch locally consistent turns — our clustering below decides who is who)
            emb_cfg = sherpa_onnx.SpeakerEmbeddingExtractorConfig(model=self.embedding_model, num_threads=self.threads)
            cfg = sherpa_onnx.OfflineSpeakerDiarizationConfig(
                segmentation=sherpa_onnx.OfflineSpeakerSegmentationModelConfig(
                    pyannote=sherpa_onnx.OfflineSpeakerSegmentationPyannoteModelConfig(model=self.segmentation_model),
                    num_threads=self.threads),
                embedding=emb_cfg,
                clustering=sherpa_onnx.FastClusteringConfig(num_clusters=-1, threshold=0.7),
                min_duration_on=0.2, min_duration_off=0.3)
            sd = sherpa_onnx.OfflineSpeakerDiarization(cfg)

            def cb(done: int, total: int, *_: object) -> int:
                if progress and total:
                    progress(0.55 * done / total)
                return 0

            raw = sd.process(audio, callback=cb).sort_by_start_time()
            if cancelled and cancelled():
                raise _Cancelled()
            turns = [(float(r.start), float(r.end), int(r.speaker)) for r in raw]

            # 2. windows over turns (turns are speaker-homogeneous, so windows never straddle a change)
            windows: list[tuple[int, float, float]] = []
            for ti, (a, b, _) in enumerate(turns):
                if b - a < 0.8:
                    continue
                t = a
                while t + WIN_SEC <= b + 0.01:
                    windows.append((ti, t, t + WIN_SEC))
                    t += HOP_SEC
                if not windows or windows[-1][0] != ti:
                    windows.append((ti, a, min(b, a + WIN_SEC)))
                elif windows[-1][2] < b - 0.5:
                    windows.append((ti, max(a, b - WIN_SEC), b))
            if not windows:
                return DiarizationOutput(segments=[DiarizedSegment(s, "Speaker 1") for s in segments],
                                         provider=self.id, embedding_model=self.embedding_id, notes=notes)
            extractor = sherpa_onnx.SpeakerEmbeddingExtractor(emb_cfg)
            X = np.zeros((len(windows), extractor.dim), dtype=np.float32)
            for k, (_, a, b) in enumerate(windows):
                if cancelled and cancelled() and k % 20 == 0:
                    raise _Cancelled()
                stream = extractor.create_stream()
                stream.accept_waveform(sr, audio[int(a * sr):int(b * sr)])
                stream.input_finished()
                X[k] = np.asarray(extractor.compute(stream), dtype=np.float32)
                if progress and k % 10 == 0:
                    progress(0.55 + 0.4 * (k + 1) / len(windows))
            X /= np.maximum(np.linalg.norm(X, axis=1, keepdims=True), 1e-9)
            dur = np.array([b - a for _, a, b in windows], dtype=np.float32)

            # 3. who is who
            if len(X) == 1:
                labels = np.zeros(1, dtype=int)
            else:
                labels = AgglomerativeClustering(n_clusters=None, distance_threshold=self.threshold,
                                                 metric="cosine", linkage="average").fit_predict(X)
            labels = merge_small_clusters(X, labels, dur)
            labels = limit_clusters(X, labels, self.speaker_count or self.max_speakers)

            # 4. turn → speaker (duration-weighted vote of its windows), stable numbering
            votes: dict[int, dict[int, float]] = {}
            for (ti, a, b), lab in zip(windows, labels):
                votes.setdefault(ti, {}).setdefault(int(lab), 0.0)
                votes[ti][int(lab)] += b - a
            order: dict[int, str] = {}
            labeled: list[tuple[float, float, str]] = []
            for ti, (a, b, _) in enumerate(turns):
                v = votes.get(ti)
                if not v:
                    continue  # turn too short to embed: words fall to the neighbour / previous label
                cid = max(v, key=v.get)
                if cid not in order:
                    order[cid] = f"Speaker {len(order) + 1}"
                labeled.append((a, b, order[cid]))

            # 5. words → turns; segments cut where the speaker changes
            out: list[DiarizedSegment] = []
            last = "Speaker 1"
            for seg in segments:
                parts = assign_words(seg, labeled, fallback=last)
                out.extend(parts)
                last = parts[-1].label

            # 6. one mean embedding per final speaker (for voice recognition)
            by_label: dict[str, list[np.ndarray]] = {}
            for (_ti, _, _), lab, x in zip(windows, labels, X):
                name = order.get(int(lab))
                if name:
                    by_label.setdefault(name, []).append(x)
            embeddings = {k: _unit(np.mean(v, axis=0)).tolist() for k, v in by_label.items()}
            if progress:
                progress(1.0)
            return DiarizationOutput(segments=out, embeddings=embeddings, provider=f"{self.id}:{self.embedding_id}",
                                     embedding_model=self.embedding_id, notes=notes)
        except _Cancelled:
            raise
        except Exception as e:
            raise ProviderError("Speaker detection failed.", detail=traceback.format_exc()) from e
