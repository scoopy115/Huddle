import numpy as np

from huddle_engine.providers.diarization import cluster_labels


def _embeddings(groups: int, per_group: int, spread: float, seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    centers = rng.normal(size=(groups, 32))
    X = np.concatenate([c + spread * rng.normal(size=(per_group, 32)) for c in centers])
    return X / np.linalg.norm(X, axis=1, keepdims=True)


def test_hint_forces_the_requested_number_of_speakers():
    # Five voices that a loose threshold would merge into fewer clusters.
    X = _embeddings(5, 12, spread=0.9)
    dur = np.ones(len(X), dtype=np.float32)
    auto = cluster_labels(X, dur, threshold=0.99, speaker_count=None, max_speakers=8)
    assert len(np.unique(auto)) < 5
    forced = cluster_labels(X, dur, threshold=0.99, speaker_count=5, max_speakers=8)
    assert len(np.unique(forced)) == 5


def test_hint_larger_than_windows_is_capped():
    X = _embeddings(2, 2, spread=0.1)
    labels = cluster_labels(X, np.ones(4, dtype=np.float32), threshold=0.6, speaker_count=9, max_speakers=8)
    assert len(np.unique(labels)) <= 4


def test_hint_of_one_speaker():
    X = _embeddings(3, 5, spread=0.5)
    labels = cluster_labels(X, np.ones(len(X), dtype=np.float32), threshold=0.6, speaker_count=1, max_speakers=8)
    assert len(np.unique(labels)) == 1
