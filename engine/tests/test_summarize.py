"""Structured LLM output parsing: nullable owner/dueDate, evidence mapping, fallbacks, chunking."""
import pytest

from huddle_engine.providers.base import ProviderError, Segment
from huddle_engine.providers.llm import ExtractiveProvider, parse_json_object
from huddle_engine.providers.summarize import CHUNK_CHARS, extractive_interview, extractive_notes, summarize


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


INTERVIEW = [Segment(0.0, 3.0, "Hoe ben je, uh, bij dit bedrijf terechtgekomen?", speaker_label="Speaker 1"),
             Segment(3.5, 9.0, "Ehm, via een stage in 2019. Daarna ben ik gebleven.", speaker_label="Speaker 2"),
             Segment(9.5, 12.0, "En wat doe je nu precies?", speaker_label="Speaker 1"),
             Segment(12.5, 18.0, "Ik leid het designteam, zes mensen. En die stage was trouwens bij de vestiging in Utrecht.",
                     speaker_label="Speaker 2")]
INAMES = {"Speaker 1": "Speaker 1", "Speaker 2": "Fatima"}


def test_interview_mode_uses_interview_prompt_and_maps_questions():
    llm = FakeLLM(['''{"title": "Fatima over haar loopbaan", "about": "Fatima vertelt over haar start.", "highlights": "Ze leidt een team van zes.",
        "questions": [
          {"question": "Hoe ben je bij dit bedrijf terechtgekomen?", "askedBy": "Speaker 1",
           "answers": [{"by": "Fatima", "text": "Via een stage in 2019, bij de vestiging in Utrecht. Daarna ben ik gebleven."}], "evidenceSegments": [0, 1, 3]},
          {"question": "Wat doe je nu?", "answer": "Uh, ik leid het designteam van zes mensen.", "askedBy": null, "evidenceSegments": [2, 3]},
          {"question": "Wie werkt er mee?", "answers": [{"by": "Fatima", "text": "Ik."}, {"by": "Speaker 3", "text": "En ik."}, {"by": "Fatima", "text": "Samen dus."}], "evidenceSegments": []}
        ],
        "actionItems": []}'''])
    notes = summarize(llm, INTERVIEW, INAMES, meeting_date="2026-09-22 (Tuesday)", notes_language="nl", mode="interview")
    assert "interview" in llm.calls[0].lower() and "[1] 00:03 Fatima:" in llm.calls[0]
    assert notes.title == "Fatima over haar loopbaan" and notes.topics == [] and notes.decisions == []
    assert notes.summary == "Fatima vertelt over haar start.\n\nZe leidt een team van zes."
    q1, q2, q3 = notes.questions
    assert q1.question.startswith("Hoe ben je") and "Utrecht" in q1.answer
    assert q1.asked_by is None and q1.answered_by == "Fatima"           # labels are not names
    assert (q1.evidence.start, q1.evidence.end, q1.evidence.segment_idx) == (0.0, 18.0, 0)
    # flat "answer" is tolerated; fillers the model left in are stripped
    assert q2.answer == "Ik leid het designteam van zes mensen." and q2.answered_by is None and q2.evidence.segment_idx == 2
    # several people: one paragraph per person (a later addition joins that person's paragraph), name-prefixed
    assert q3.answer == "Fatima: Ik. Samen dus.\n\nEn ik." and q3.answered_by == "Fatima"


def test_interview_long_transcript_merges_partials():
    many = [Segment(i * 5.0, i * 5.0 + 4, f"Vraag {i}, wat vind je van onderwerp nummer {i}?" if i % 2 == 0
                    else f"Ik vind onderwerp {i} heel belangrijk omdat het veel details heeft, nummer {i}.",
                    speaker_label="Speaker 1" if i % 2 == 0 else "Speaker 2") for i in range(1400)]
    part = '{"summary": "deel", "questions": [{"question": "Q?", "answers": [{"by": null, "text": "A"}], "evidenceSegments": [0]}], "actionItems": []}'
    merged = '{"about": "geheel", "questions": [{"question": "Q?", "answers": [{"by": null, "text": "A en B"}], "evidenceSegments": [0, 1]}], "actionItems": []}'

    class MergeAware(FakeLLM):
        def complete_json(self, system, user, max_tokens=2048):
            self.calls.append((system, user))
            return merged if "Partial notes:" in user else part

    llm = MergeAware([])
    notes = summarize(llm, many, {}, meeting_date="2026-09-22", mode="interview")
    assert len(llm.calls) > 2 and "merging partial interview notes" in llm.calls[-1][0]
    assert notes.questions[0].answer == "A en B" and notes.summary == "geheel"


def test_extractive_interview_pairs_questions_with_following_answers():
    notes = extractive_interview(INTERVIEW, INAMES)
    assert notes.provider == "extractive" and len(notes.questions) == 2
    q1, q2 = notes.questions
    assert q1.question == "Hoe ben je, bij dit bedrijf terechtgekomen?" or q1.question.startswith("Hoe ben je")
    assert "Ehm" not in q1.answer and q1.answer.startswith("Via een stage in 2019")
    assert q1.answered_by == "Fatima" and q1.asked_by is None and q1.evidence.segment_idx == 0
    assert q2.question == "En wat doe je nu precies?" and "designteam" in q2.answer
    # interview mode without a model goes through the same path
    assert summarize(ExtractiveProvider(), INTERVIEW, INAMES, meeting_date="2026-09-22", mode="interview").questions
