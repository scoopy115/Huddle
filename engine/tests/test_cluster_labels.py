import numpy as np

from huddle_engine.providers.diarization import cluster_labels


def _embeddings(groups: int, per_group: int, spread: float, seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    centers = rng.normal(size=(groups, 32))
    X = np.concatenate([c + spread * rng.normal(size=(per_group, 32)) for c in centers])
    return X / np.linalg.norm(X, axis=1, keepdims=True)


def test_hint_forces_the_requested_number_of_speakers():
    # Five voices that a loose threshold merges into fewer clusters: the threshold cut alone
    # under-counts, the count estimate recovers them, and a hint always wins.
    from huddle_engine.providers.diarization import _cut
    X = _embeddings(5, 12, spread=0.9)
    dur = np.ones(len(X), dtype=np.float32)
    assert len(np.unique(_cut(X, dur, 0.99))) < 5
    auto = cluster_labels(X, dur, threshold=0.99, speaker_count=None, max_speakers=8)
    assert len(np.unique(auto)) == 5
    forced = cluster_labels(X, dur, threshold=0.99, speaker_count=5, max_speakers=8)
    assert len(np.unique(forced)) == 5
    forced = cluster_labels(X, dur, threshold=0.99, speaker_count=3, max_speakers=8)
    assert len(np.unique(forced)) == 3


def test_hint_larger_than_windows_is_capped():
    X = _embeddings(2, 2, spread=0.1)
    labels = cluster_labels(X, np.ones(4, dtype=np.float32), threshold=0.6, speaker_count=9, max_speakers=8)
    assert len(np.unique(labels)) <= 4


def test_hint_of_one_speaker():
    X = _embeddings(3, 5, spread=0.5)
    labels = cluster_labels(X, np.ones(len(X), dtype=np.float32), threshold=0.6, speaker_count=1, max_speakers=8)
    assert len(np.unique(labels)) == 1


def test_count_estimate_finds_more_voices_than_a_loose_threshold():
    # Nine well-separated voices; a threshold tuned for two or three merges them, the silhouette
    # pass sees the better cut.
    from huddle_engine.providers.diarization import estimate_speaker_count
    X = _embeddings(9, 10, spread=0.35, seed=3)
    dur = np.full(len(X), 3.0, dtype=np.float32)
    labels, scores = estimate_speaker_count(X, dur, threshold=0.99, max_speakers=12)
    assert len(np.unique(labels)) == 9
    assert max(scores, key=lambda k: scores[k]) == 9
    auto = cluster_labels(X, dur, threshold=0.99, speaker_count=None, max_speakers=12)
    assert len(np.unique(auto)) == 9


def test_count_estimate_keeps_the_threshold_when_no_cut_is_clearly_better():
    # Two voices with wide, overlapping spread: stricter cuts split noise, not people.
    from huddle_engine.providers.diarization import estimate_speaker_count
    X = _embeddings(2, 40, spread=1.2, seed=5)
    dur = np.full(len(X), 3.0, dtype=np.float32)
    labels, _ = estimate_speaker_count(X, dur, threshold=0.6, max_speakers=12)
    from huddle_engine.providers.diarization import _cut
    assert len(np.unique(labels)) == len(np.unique(_cut(X, dur, 0.6)))


def test_max_speakers_caps_the_estimate():
    X = _embeddings(9, 10, spread=0.35, seed=3)
    dur = np.full(len(X), 3.0, dtype=np.float32)
    auto = cluster_labels(X, dur, threshold=0.99, speaker_count=None, max_speakers=4)
    assert len(np.unique(auto)) <= 4
