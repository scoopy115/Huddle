"""Structured LLM output parsing: nullable owner/dueDate, evidence mapping, fallbacks, chunking."""
import pytest

from huddle_engine.providers.base import ProviderError, Segment
from huddle_engine.providers.llm import ExtractiveProvider, parse_json_object
from huddle_engine.providers.summarize import CHUNK_CHARS, extractive_notes, summarize


class FakeLLM:
    id = "fake"
    model = "fake-1"

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def complete_json(self, system, user, max_tokens=2048):
        self.calls.append(user)
        return self.responses.pop(0)

    def complete(self, system, user, max_tokens=1024):
        return "answer"


SEGS = [Segment(0.0, 4.0, "We gaan naast blauw een warme kleur gebruiken.", speaker_label="Speaker 1"),
        Segment(4.5, 8.0, "Daan, kun jij de kleuren aanpassen? Ik doe dat vrijdag.", speaker_label="Speaker 2"),
        Segment(8.5, 12.0, "De analytics moeten nog gecontroleerd worden.", speaker_label="Speaker 1")]
NAMES = {"Speaker 1": "Alex", "Speaker 2": "Speaker 2"}


def test_parse_json_tolerates_fences_and_prose():
    assert parse_json_object('Sure!\n```json\n{"a": 1}\n```')["a"] == 1
    assert parse_json_object('{"a": {"b": [1,2]}} trailing')["a"]["b"] == [1, 2]
    with pytest.raises(ValueError):
        parse_json_object("no json here")


def test_structured_output_with_evidence_and_nulls():
    llm = FakeLLM(['''{"summary": "Korte samenvatting.",
        "topics": [{"title": "Branding", "summary": "Kleuren."}],
        "decisions": [{"text": "Warme accentkleur naast blauw.", "evidenceSegments": [0]}],
        "actionItems": [
          {"text": "Kleuren aanpassen in Figma", "owner": "Daan", "dueDate": "2026-09-04", "confidence": 0.9, "evidenceSegments": [1]},
          {"text": "Analytics controleren", "owner": "Speaker 1", "dueDate": "volgende week", "confidence": 0.5, "evidenceSegments": [2, 99]},
          {"text": "Iets zonder bewijs", "owner": null, "dueDate": null, "confidence": 1.4, "evidenceSegments": []}
        ]}'''])
    notes = summarize(llm, SEGS, NAMES, meeting_date="2026-09-03 (Thursday)")
    assert notes.summary == "Korte samenvatting." and notes.provider == "fake" and notes.model == "fake-1"
    assert notes.topics[0].title == "Branding"
    d = notes.decisions[0]
    assert (d.evidence.start, d.evidence.end, d.evidence.segment_idx) == (0.0, 4.0, 0)
    a1, a2, a3 = notes.action_items
    assert a1.owner == "Daan" and a1.due_date == "2026-09-04" and a1.evidence.start == 4.5
    # label-like owners and vague dates are normalised to null, invalid indices ignored
    assert a2.owner is None and a2.due_date is None and a2.evidence.start == 8.5 and a2.evidence.end == 12.0
    assert a3.owner is None and a3.due_date is None and a3.confidence == 1.0 and a3.evidence.start is None
    # prompt carried the meeting date and indexed transcript lines
    assert "2026-09-03" in llm.calls[0] and "[1] 00:04 Speaker 2:" in llm.calls[0] and "[0] 00:00 Alex:" in llm.calls[0]


def test_unusable_response_raises_provider_error_with_detail():
    llm = FakeLLM(["I cannot do that."])
    with pytest.raises(ProviderError) as ei:
        summarize(llm, SEGS, NAMES, meeting_date="2026-09-03")
    assert "unusable" in str(ei.value) and "I cannot do that." in ei.value.detail


def test_long_transcript_uses_map_reduce():
    many = [Segment(i * 5.0, i * 5.0 + 4, f"Dit is een lange zin over de homepage en de kleuren van Acme nummer {i}.",
                    speaker_label="Speaker 1") for i in range(1200)]
    part = '{"summary": "deel", "topics": [], "decisions": [], "actionItems": []}'
    merged = '{"summary": "geheel", "topics": [{"title": "Homepage", "summary": ""}], "decisions": [], "actionItems": []}'

    class MergeAware(FakeLLM):
        def complete_json(self, system, user, max_tokens=2048):
            self.calls.append(user)
            return merged if "Partial notes:" in user else part

    llm = MergeAware([])
    notes = summarize(llm, many, {}, meeting_date="2026-09-03")
    assert notes.summary == "geheel" and notes.topics[0].title == "Homepage" and len(llm.calls) >= 3
    assert all(len(c) < CHUNK_CHARS + 2000 for c in llm.calls[:-1])
    assert "part 1 of" in llm.calls[0]


def test_extractive_fallback_never_fabricates_owner_or_date():
    segs = [Segment(0, 4, "We decided to go with the warm accent color.", speaker_label="Speaker 1"),
            Segment(4, 8, "I'll send the copy on Thursday so Alex can finish the page.", speaker_label="Speaker 2")]
    notes = summarize(ExtractiveProvider(), segs, {"Speaker 1": "Speaker 1", "Speaker 2": "Speaker 2"}, meeting_date="2026-09-03")
    assert notes.provider == "extractive"
    assert notes.decisions and notes.decisions[0].evidence.start == 0
    assert notes.action_items and all(a.owner is None and a.due_date is None for a in notes.action_items)
    assert notes.action_items[0].evidence.start == 4
    assert extractive_notes([], {}).summary == ""


def test_clean_title_and_default_title_detection():
    from huddle_engine.providers.summarize import clean_title
    from huddle_engine.services.meetings import default_title, is_default_title
    assert clean_title(' "Acme website & sprint werkwijze." ') == "Acme website & sprint werkwijze"
    assert clean_title("x") == ""
    assert len(clean_title("w" * 200)) == 80
    assert is_default_title(default_title(1_756_900_000.0))
    assert is_default_title("Recovered recording") and is_default_title("") and is_default_title(None)
    assert not is_default_title("sprint-meeting-long") and not is_default_title("Weekly design sync")
