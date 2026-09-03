"""Pure transcript post-processing: silence-boundary chunking and pause splitting."""
from huddle_engine.providers.base import Segment, Word
from huddle_engine.providers.transcription import group_regions, group_regions_by_language, split_at_pauses


def test_group_regions_respects_max_len_and_gaps():
    regions = [(0.0, 6.3), (7.0, 15.5), (16.2, 23.9), (24.6, 33.0), (33.7, 39.9)]
    chunks = group_regions(regions, max_len=20.0, min_gap=0.3)
    assert chunks == [(0.0, 15.5), (16.2, 33.0), (33.7, 39.9)]


def test_group_by_language_never_mixes_languages_and_smooths_tiny_regions():
    regions = [(0.0, 6.3), (7.0, 15.5), (16.2, 17.0), (17.5, 23.9), (24.6, 33.0)]
    langs = ["nl", "en", "nl", "en", "nl"]          # 16.2–17.0 is a 0.8 s "ok" → takes neighbour's language
    chunks = group_regions_by_language(regions, langs, max_len=28.0)
    assert chunks == [(0.0, 6.3, "nl"), (7.0, 23.9, "en"), (24.6, 33.0, "nl")]
    # same language keeps merging up to max_len, then starts a new chunk at a gap
    chunks = group_regions_by_language([(0, 10), (10.5, 20), (20.5, 27), (27.5, 35)], ["nl"] * 4, max_len=28.0)
    assert chunks == [(0.0, 27.0, "nl"), (27.5, 35.0, "nl")]


def test_group_regions_keeps_tiny_gaps_together_and_long_regions_alone():
    assert group_regions([(0, 19.0), (19.1, 25.0)]) == [(0.0, 25.0)]      # 0.1 s gap: same utterance
    assert group_regions([(0, 45.0), (46.0, 50.0)]) == [(0.0, 45.0), (46.0, 50.0)]
    assert group_regions([]) == []


def _seg(words, text=None):
    ws = [Word(s, e, w) for s, e, w in words]
    return Segment(ws[0].start, ws[-1].end, text or " ".join(w.word for w in ws), words=ws)


def test_split_at_pauses_cuts_at_word_gaps():
    seg = _seg([(0.0, 0.4, "Ja"), (0.5, 0.9, "prima."), (1.9, 2.3, "Dan"), (2.4, 2.9, "sturen"), (3.0, 3.4, "we"), (3.5, 3.9, "het.")])
    out = split_at_pauses([seg], min_gap=0.55)
    assert [s.text for s in out] == ["Ja prima.", "Dan sturen we het."]
    assert out[0].start == 0.0 and out[0].end == 0.9 and out[1].start == 1.9 and out[1].end == 3.9
    assert len(out[1].words) == 4


def test_split_at_pauses_forces_cut_on_long_segments_without_gaps():
    words = [(i * 1.0, i * 1.0 + 0.9, f"w{i}") for i in range(16)]   # 16 s, 0.1 s gaps only
    out = split_at_pauses([_seg(words)], min_gap=0.55, max_len=12.0)
    assert len(out) == 2 and sum(len(s.words) for s in out) == 16


def test_split_at_pauses_leaves_short_segments_alone():
    seg = _seg([(0.0, 0.4, "Ja"), (1.5, 1.9, "goed.")])
    assert split_at_pauses([seg]) == [seg]
