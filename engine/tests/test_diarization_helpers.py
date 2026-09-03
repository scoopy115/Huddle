"""Pure helpers of the sherpa-onnx diarizer (no models needed)."""
import numpy as np

from huddle_engine.providers.base import Segment, Word
from huddle_engine.providers.diarization import assign_words, bandwidth_note, label_for_time, limit_clusters, merge_small_clusters


def _unit(v):
    v = np.asarray(v, dtype=np.float32)
    return v / np.linalg.norm(v)


def test_merge_small_clusters_folds_outliers_into_nearest_big_cluster():
    a, b = _unit([1, 0, 0]), _unit([0, 1, 0])
    X = np.vstack([a, a, a, b, b, b, _unit([0.9, 0.1, 0]), _unit([0.1, 0.95, 0])])
    labels = np.array([0, 0, 0, 1, 1, 1, 2, 3])
    dur = np.array([30, 30, 30, 30, 30, 30, 2, 2], dtype=np.float32)
    out = merge_small_clusters(X, labels, dur, min_sec=8.0, min_share=0.03)
    assert out.tolist() == [0, 0, 0, 1, 1, 1, 0, 1]


def test_merge_small_clusters_keeps_a_single_cluster():
    X = np.vstack([_unit([1, 0]), _unit([1, 0.1])])
    out = merge_small_clusters(X, np.array([0, 1]), np.array([1.0, 1.0]))
    assert len(set(out.tolist())) == 1


def test_limit_clusters_merges_most_similar_first():
    X = np.vstack([_unit([1, 0, 0]), _unit([0.95, 0.3, 0]), _unit([0, 0, 1])])
    out = limit_clusters(X, np.array([0, 1, 2]), 2)
    assert out[0] == out[1] and out[2] != out[0]


def test_label_for_time_prefers_overlap_then_nearest_edge():
    turns = [(0.0, 2.0, "A"), (2.5, 4.0, "B")]
    assert label_for_time(1.0, turns) == "A"
    assert label_for_time(2.2, turns) == "A"      # 0.2 s after A, 0.3 s before B
    assert label_for_time(2.4, turns) == "B"
    assert label_for_time(9.0, turns) is None      # further than tolerance from every turn


def test_assign_words_cuts_segment_at_speaker_change():
    words = [Word(0.0, 0.5, "Hallo"), Word(0.5, 1.0, "Daan,"), Word(1.2, 1.6, "kun"), Word(1.6, 2.0, "jij"),
             Word(2.6, 3.0, "Ja"), Word(3.0, 3.4, "prima")]
    seg = Segment(start=0.0, end=3.4, text="Hallo Daan, kun jij Ja prima", confidence=None, words=words)
    turns = [(0.0, 2.1, "Speaker 1"), (2.5, 3.5, "Speaker 2")]
    parts = assign_words(seg, turns, fallback="Speaker 1")
    assert [(p.label, p.segment.text) for p in parts] == [("Speaker 1", "Hallo Daan, kun jij"), ("Speaker 2", "Ja prima")]
    assert parts[0].segment.start == 0.0 and parts[1].segment.end == 3.4


def test_assign_words_smooths_single_word_flicker_and_uses_fallback():
    words = [Word(0.0, 0.4, "a"), Word(0.4, 0.8, "b"), Word(0.8, 1.2, "c"), Word(1.2, 1.6, "d")]
    seg = Segment(start=0.0, end=1.6, text="a b c d", confidence=None, words=words)
    turns = [(0.0, 0.85, "Speaker 1"), (0.9, 1.05, "Speaker 2"), (1.1, 1.6, "Speaker 1")]
    parts = assign_words(seg, turns, fallback="Speaker 1")
    assert [p.label for p in parts] == ["Speaker 1"]
    no_words = Segment(start=50.0, end=51.0, text="x", confidence=None, words=[])
    assert assign_words(no_words, turns, fallback="Speaker 2")[0].label == "Speaker 2"


def test_bandwidth_note_flags_narrowband_audio():
    sr = 16000
    t = np.arange(sr * 20) / sr
    wide = (np.sin(2 * np.pi * 300 * t) + np.sin(2 * np.pi * 6000 * t)).astype(np.float32)
    narrow = (np.sin(2 * np.pi * 300 * t) + 0.5 * np.sin(2 * np.pi * 2500 * t)).astype(np.float32)
    assert bandwidth_note(wide, sr) is None
    assert "narrowband" in (bandwidth_note(narrow, sr) or "")
