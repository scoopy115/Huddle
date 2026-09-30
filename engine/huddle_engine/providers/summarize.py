"""Structured meeting notes from a transcript, provider-agnostic.

Output schema (spec §17): summary, topics[], decisions[] with evidence, action
items with nullable owner/dueDate + confidence + evidence. Evidence is requested
as transcript **segment indices** (robust for small models) and mapped to
timestamps here. Long transcripts are summarised map-reduce style.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Annotated, Any

from pydantic import BaseModel, BeforeValidator, Field, ValidationError

from .base import ProviderError, Segment
from .llm import ExtractiveProvider, parse_json_object


def _as_text(v: Any) -> Any:
    """Small models sometimes return a list of strings (or a number) where a string was asked for.
    Join lists with newlines instead of failing the whole response."""
    if isinstance(v, list):
        return "\n".join(str(x) for x in v if x is not None)
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return str(v)
    return v


Text = Annotated[str, BeforeValidator(_as_text)]


log = logging.getLogger(__name__)

# ~ characters of transcript per LLM call. Qwen-class 8B models with 16k ctx handle
# ~40k chars of prose comfortably; stay conservative for smaller contexts.
CHUNK_CHARS = 28000


class TopicOut(BaseModel):
    title: Text
    summary: Text = ""


class DecisionOut(BaseModel):
    text: Text
    evidenceSegments: list[int] = Field(default_factory=list)


class ActionItemOut(BaseModel):
    text: Text
    owner: str | None = None
    dueDate: str | None = None
    confidence: float | None = None
    evidenceSegments: list[int] = Field(default_factory=list)


class NotesOut(BaseModel):
    title: Text = ""
    summary: Text = ""
    topics: list[TopicOut] = Field(default_factory=list)
    decisions: list[DecisionOut] = Field(default_factory=list)
    actionItems: list[ActionItemOut] = Field(default_factory=list)


class AnswerOut(BaseModel):
    by: str | None = None
    text: Text = ""


class QuestionOut(BaseModel):
    question: Text
    answers: list[AnswerOut] = Field(default_factory=list)
    answer: Text = ""                    # tolerated when a small model flattens the answers
    askedBy: str | None = None
    evidenceSegments: list[int] = Field(default_factory=list)


class InterviewOut(BaseModel):
    title: Text = ""
    about: Text = ""
    highlights: Text = ""
    summary: Text = ""                   # tolerated when a model collapses about + highlights
    questions: list[QuestionOut] = Field(default_factory=list)
    actionItems: list[ActionItemOut] = Field(default_factory=list)

    @property
    def summary_text(self) -> str:
        parts = [p.strip() for p in (self.about, self.highlights) if p and p.strip()]
        return "\n\n".join(parts) if parts else self.summary.strip()


@dataclass
class Evidence:
    start: float | None = None
    end: float | None = None
    segment_idx: int | None = None


@dataclass
class Decision:
    text: str
    evidence: Evidence = field(default_factory=Evidence)


@dataclass
class ActionItem:
    text: str
    owner: str | None
    due_date: str | None
    confidence: float | None
    evidence: Evidence = field(default_factory=Evidence)


@dataclass
class Topic:
    title: str
    summary: str


@dataclass
class Question:
    """An interview question with the complete answer gathered from the whole conversation."""
    question: str
    answer: str
    asked_by: str | None = None
    answered_by: str | None = None
    evidence: Evidence = field(default_factory=Evidence)


@dataclass
class MeetingNotes:
    summary: str
    topics: list[Topic]
    decisions: list[Decision]
    action_items: list[ActionItem]
    provider: str
    model: str
    raw: str | None = None
    title: str = ""          # short descriptive title proposed by the model ("" = none)
    questions: list[Question] = field(default_factory=list)   # interview mode only


# Languages the notes can be written in (same list as the UI's lib/languages.ts). Names are in
# English because they are only used inside the prompt.
LANG_NAMES = {
    "en": "English", "nl": "Dutch", "de": "German", "fr": "French", "es": "Spanish", "it": "Italian", "pt": "Portuguese",
    "pl": "Polish", "sv": "Swedish", "da": "Danish", "no": "Norwegian", "fi": "Finnish", "is": "Icelandic", "tr": "Turkish",
    "cs": "Czech", "sk": "Slovak", "sl": "Slovenian", "hr": "Croatian", "sr": "Serbian", "bs": "Bosnian", "bg": "Bulgarian",
    "mk": "Macedonian", "el": "Greek", "hu": "Hungarian", "ro": "Romanian", "uk": "Ukrainian", "ru": "Russian",
    "be": "Belarusian", "et": "Estonian", "lv": "Latvian", "lt": "Lithuanian", "ga": "Irish", "cy": "Welsh", "ca": "Catalan",
    "eu": "Basque", "gl": "Galician", "ar": "Arabic", "he": "Hebrew", "fa": "Persian", "hi": "Hindi", "bn": "Bengali",
    "ur": "Urdu", "ta": "Tamil", "te": "Telugu", "ml": "Malayalam", "kn": "Kannada", "id": "Indonesian", "ms": "Malay",
    "vi": "Vietnamese", "th": "Thai", "tl": "Filipino", "ja": "Japanese", "ko": "Korean", "zh": "Chinese", "sw": "Swahili",
    "af": "Afrikaans",
}

SYSTEM_PROMPT = """You are a meticulous meeting-notes assistant for in-person meetings.
The transcript may be in any language or a mix of languages. Write ALL notes in {notes_language}, regardless of the language spoken.
Each transcript line starts with a segment index in brackets, a timestamp and a speaker label.

Respond with ONE strict JSON object and nothing else, matching exactly:
{
  "title": "A short, specific title for this meeting in 3-7 words: the main subject(s), no date, no 'Meeting about'.",
  "summary": "One paragraph PER topic or project, each starting with the topic name and a colon, separated by a blank line. Each paragraph 3-5 sentences on what was discussed, agreed and left open for THAT topic. Never compress several projects into one paragraph.",
  "topics": [{"title": "project or subject name as used in the meeting", "summary": "3-5 sentences: the concrete points, agreements and open questions for this topic"}],
  "decisions": [{"text": "a concrete decision that was actually made", "evidenceSegments": [12, 13]}],
  "actionItems": [{"text": "concrete task", "owner": "name or null", "dueDate": "YYYY-MM-DD or null", "confidence": 0.0-1.0, "evidenceSegments": [31]}]
}

Rules:
- Meetings often cover several projects or clients in sequence. Detect each one and give it its own topic AND its own summary paragraph; keep them in meeting order.
- Only include decisions that were explicitly made, not open questions.
- Only include action items that someone committed to or was asked to do.
- owner: the person who committed to or was asked to do the task, ONLY if a real name appears in the transcript — either as that speaker's name or because they are addressed by name in the text ("Daan, kun jij…" → owner "Daan"). Labels like "Speaker 2" are not names. NEVER invent an owner; otherwise owner must be null.
- dueDate: ONLY a deadline stated for THIS task. Convert relative dates ("vrijdag", "next week", "morgen") to YYYY-MM-DD using the meeting date given. Dates mentioned for other things (a launch date, another task) do not count. NEVER invent a deadline; otherwise dueDate must be null.
- evidenceSegments must list the index numbers of the transcript lines that support the item.
- Keep texts short and concrete. Do not add commentary outside the JSON."""

INTERVIEW_PROMPT = """You are a meticulous assistant that turns the transcript of an interview into clean, structured notes.
One or more interviewers ask questions; one or more people answer. The transcript may be in any language or a mix of languages. Write ALL notes in {notes_language}, regardless of the language spoken.
Each transcript line starts with a segment index in brackets, a timestamp and a speaker label.

Respond with ONE strict JSON object and nothing else, matching exactly:
{
  "title": "A short, specific title for this interview in 3-7 words: the person or subject, no date, no 'Interview with'.",
  "about": "One paragraph: what the interview was about and who was interviewed, as far as the transcript shows.",
  "highlights": "One or two paragraphs: the most important answers and standpoints from the WHOLE interview, with the concrete details (names, numbers, examples, reasons) that were given.",
  "questions": [{"question": "the question as it was asked, cleaned up into one clear sentence", "askedBy": "name or null", "answers": [{"by": "name or null", "text": "this person's complete answer, see the rules"}], "evidenceSegments": [12, 13, 40]}],
  "actionItems": [{"text": "concrete task", "owner": "name or null", "dueDate": "YYYY-MM-DD or null", "confidence": 0.0-1.0, "evidenceSegments": [31]}]
}

Rules:
- List EVERY real question the interviewer(s) asked, in the order they were first asked. Skip backchannel ("right?", "you know?"), small talk and rhetorical remarks. A rephrasing, or a follow-up that only digs deeper into the same subject, belongs to the original question and is not a new one.
- The answer is EVERYTHING the interviewee said in reply to that question, wherever in the interview it was said. When a later answer comes back to an earlier question, refers to it or adds to it, put that part under the earlier question. Never lose a detail because it was said later.
- Write each answer as a clean, faithful account in the first person, as the interviewee would put it in writing: without fillers ("uh", "um", "you know"), false starts, repetitions and the interviewer's interjections. Condense, but keep every concrete detail: names, numbers, dates, places, examples, reasons and nuances, copied exactly as they were said. A rich answer may be several sentences or a short paragraph; a one-word answer stays one word.
- answers holds one entry PER PERSON who answered, in speaking order: one person → one entry; two people → two entries, each with only that person's own words. Never blend two people's words into one "I".
- askedBy / by: ONLY real names that appear in the transcript (as a speaker's name or because someone is addressed by name). Labels like "Speaker 2" are not names; use null instead.
- evidenceSegments: the index numbers of the transcript lines with the question and its answer(s).
- actionItems: only concrete tasks someone committed to or was asked to do during the interview itself (send a document, follow up, arrange a visit). The interviewee's plans, intentions and future business steps are answers, NOT action items. owner and dueDate follow the same never-invent rules: a real name from the transcript or null; a date stated for THIS task converted to YYYY-MM-DD with the meeting date, or null.
- Do not add commentary outside the JSON."""

INTERVIEW_MERGE_PROMPT = """You are merging partial interview notes (JSON objects, in order) from consecutive parts of one interview into a single notes object with the same schema.
Keep the questions in the order they were first asked. When two parts contain the same question (or a rephrasing of it), keep it once and combine the answers per person (one answers entry per person) without losing any detail. When a later part contains an answer that belongs to a question from an earlier part, move it under that question. Keep evidenceSegments from the parts; keep names null when they were null. Deduplicate action items.
Write in {notes_language}. Respond with ONE strict JSON object with keys title, about, highlights, questions, actionItems and nothing else."""

MERGE_PROMPT = """You are merging partial meeting notes (JSON objects, in order) from consecutive parts of one meeting into a single notes object with the same schema.
Deduplicate topics, decisions and action items; keep evidenceSegments from the parts; keep owner/dueDate null when they were null.
Write in {notes_language}. Respond with ONE strict JSON object with keys title, summary, topics, decisions, actionItems and nothing else."""

ACTIONS_PROMPT = """You extract action items from a meeting transcript. Each line starts with a segment index in brackets, a timestamp and a speaker label.
Write the items in {notes_language}. Respond with ONE strict JSON object only:
{{"actionItems": [{{"text": "concrete task", "owner": "name or null", "dueDate": "YYYY-MM-DD or null", "confidence": 0.0-1.0, "evidenceSegments": [31]}}]}}
Rules: only tasks someone committed to or was asked to do; owner only if a real name appears in the transcript (labels like "Speaker 2" are not names), otherwise null; dueDate only if a deadline for THIS task was stated (convert relative dates with the meeting date), otherwise null. Never invent."""


def clean_title(raw: str | None) -> str:
    """Normalise a model-proposed meeting title: one line, no wrapping quotes or final period,
    at most 80 characters. Empty when the model gave nothing usable."""
    t = " ".join((raw or "").split()).strip().strip('"\u201c\u201d\'').rstrip(".").strip()
    if len(t) < 3:
        return ""
    return t[:80].rstrip(" ,;:-")


def _calendar_hint(meeting_date: str) -> str:
    """Small models get weekday arithmetic wrong ("vrijdag" → off by one). Spell out
    the next 14 days so relative dates can be looked up instead of computed."""
    import datetime as _dt
    m = re.search(r"\d{4}-\d{2}-\d{2}", meeting_date)
    if not m:
        return ""
    try:
        d0 = _dt.date.fromisoformat(m.group(0))
    except ValueError:
        return ""
    days = ", ".join(f"{(d0 + _dt.timedelta(days=i)).strftime('%A')}={(d0 + _dt.timedelta(days=i)).isoformat()}"
                     for i in range(1, 15))
    return f" Upcoming days (use for relative dates like 'vrijdag'/'next Tuesday'): {days}."


def _fmt_ts(sec: float) -> str:
    m, s = divmod(int(sec), 60)
    h, m = divmod(m, 60)
    return f"{h:d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def render_transcript(segments: list[Segment], names: dict[str | None, str]) -> list[str]:
    lines = []
    for i, s in enumerate(segments):
        who = names.get(s.speaker_label, s.speaker_label or "Speaker")
        lines.append(f"[{i}] {_fmt_ts(s.start)} {who}: {s.text.strip()}")
    return lines


def _chunks(lines: list[str], limit: int) -> list[list[str]]:
    out, cur, size = [], [], 0
    for ln in lines:
        if cur and size + len(ln) + 1 > limit:
            out.append(cur)
            cur, size = [], 0
        cur.append(ln)
        size += len(ln) + 1
    if cur:
        out.append(cur)
    return out


def _evidence(idxs: list[int], segments: list[Segment]) -> Evidence:
    valid = [i for i in idxs if isinstance(i, int) and 0 <= i < len(segments)]
    if not valid:
        return Evidence()
    return Evidence(start=min(segments[i].start for i in valid),
                    end=max(segments[i].end for i in valid), segment_idx=min(valid))


def _clean_owner(owner: str | None) -> str | None:
    if not owner:
        return None
    o = owner.strip()
    if not o or o.lower() in {"null", "none", "n/a", "unknown", "onbekend", "niemand", "nobody",
                              "unassigned", "tbd", "?"}:
        return None
    if re.fullmatch(r"(speaker|spreker)\s*\d+", o, re.IGNORECASE):
        return None
    return o


def _clean_date(d: str | None) -> str | None:
    if not d:
        return None
    d = d.strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", d):
        return d
    return None            # anything vaguer than a date is not a deadline


def _call(provider, system: str, user: str, model=NotesOut, max_tokens: int = 4000):
    raw = provider.complete_json(system, user, max_tokens=max_tokens)
    try:
        return model.model_validate(parse_json_object(raw)), raw
    except (ValueError, ValidationError) as e:
        raise ProviderError("The AI model returned an unusable response.", detail=f"{e}\n---\n{raw[:4000]}") from e


def _action_items(items: list[ActionItemOut], segments: list[Segment]) -> list[ActionItem]:
    return [ActionItem(a.text.strip(), _clean_owner(a.owner), _clean_date(a.dueDate),
                       (max(0.0, min(1.0, float(a.confidence))) if a.confidence is not None else None),
                       _evidence(a.evidenceSegments, segments))
            for a in items if a.text.strip()]


def _to_notes(out: NotesOut, segments: list[Segment], provider_id: str, model: str, raw: str | None) -> MeetingNotes:
    return MeetingNotes(
        title=clean_title(out.title),
        summary=out.summary.strip(),
        topics=[Topic(t.title.strip(), t.summary.strip()) for t in out.topics if t.title.strip()],
        decisions=[Decision(d.text.strip(), _evidence(d.evidenceSegments, segments))
                   for d in out.decisions if d.text.strip()],
        action_items=_action_items(out.actionItems, segments),
        provider=provider_id, model=model, raw=raw)


def _interview_to_notes(out: InterviewOut, segments: list[Segment], provider_id: str, model: str,
                        raw: str | None) -> MeetingNotes:
    return MeetingNotes(
        title=clean_title(out.title),
        summary=out.summary_text,
        topics=[], decisions=[],
        questions=[_question(q, segments) for q in out.questions if q.question.strip()],
        action_items=_action_items(out.actionItems, segments),
        provider=provider_id, model=model, raw=raw)


def _one_line(text: str) -> str:
    return " ".join(text.split()).strip()


def _question(q: QuestionOut, segments: list[Segment]) -> Question:
    """One answers entry per person. A single answer is stored as plain text; several are
    joined as "Name: …" paragraphs so the reader sees who said what."""
    raw = [(_clean_owner(a.by), _strip_fillers(a.text)) for a in q.answers if a.text.strip()]
    if not raw and q.answer.strip():
        raw = [(None, _strip_fillers(q.answer))]
    # everything one person said about this question becomes one paragraph, in speaking order
    answers: list[tuple[str | None, str]] = []
    for by, t in raw:
        if not t:
            continue
        for i, (b, prev) in enumerate(answers):
            if b == by:
                answers[i] = (b, f"{prev} {t}")
                break
        else:
            answers.append((by, t))
    names = [by for by, _ in answers if by]
    if len(answers) <= 1:
        text = answers[0][1] if answers else ""
    else:
        text = "\n\n".join(f"{by}: {t}" if by else t for by, t in answers)
    return Question(_one_line(q.question), text, _clean_owner(q.askedBy), ", ".join(names) or None,
                    _evidence(q.evidenceSegments, segments))


def _context_block(user_context: str | None) -> str:
    if not user_context or not user_context.strip():
        return ""
    return ("\n\nContext and corrections from the user (authoritative — follow them even where the transcript "
            "seems to say otherwise):\n" + user_context.strip())


def summarize(provider, segments: list[Segment], speaker_names: dict[str | None, str],
              meeting_date: str, language_hint: str | None = None, notes_language: str = "en",
              include_actions: bool = True, user_context: str | None = None, mode: str = "meeting") -> MeetingNotes:
    """Produce structured notes with the given LLM provider, or the extractive fallback.
    Notes are written in `notes_language` (the app's UI language), never in the spoken one.
    `mode` "interview" yields a summary plus every question with its complete answer instead
    of topics and decisions."""
    if not segments:
        return MeetingNotes("", [], [], [], provider="none", model="none")
    interview = mode == "interview"
    if isinstance(provider, ExtractiveProvider):
        notes = extractive_interview(segments, speaker_names) if interview else extractive_notes(segments, speaker_names)
        if not include_actions:
            notes.action_items = []
        return notes

    lang_name = LANG_NAMES.get(notes_language, notes_language)
    system = (INTERVIEW_PROMPT if interview else SYSTEM_PROMPT).replace("{notes_language}", lang_name)
    if not include_actions:
        system += "\n- Return an empty actionItems list; action items are extracted separately."
    out_model = InterviewOut if interview else NotesOut
    to_notes = _interview_to_notes if interview else _to_notes
    max_tokens = 6000 if interview else 4000      # answers are quoted at length
    lines = render_transcript(segments, speaker_names)
    header = f"{'Interview' if interview else 'Meeting'} date: {meeting_date}.{_calendar_hint(meeting_date)}"
    if language_hint and language_hint != "auto":
        header += f" Spoken language(s): {language_hint}."
    header += _context_block(user_context)
    chunks = _chunks(lines, CHUNK_CHARS)

    if len(chunks) == 1:
        out, raw = _call(provider, system, f"{header}\n\nTranscript:\n" + "\n".join(lines) + "\n\nReturn the JSON now.",
                         out_model, max_tokens)
        notes = to_notes(out, segments, provider.id, provider.model, raw)
    else:
        partials = []
        for n, chunk in enumerate(chunks, 1):
            log.info("summarize: chunk %d/%d (%d lines)", n, len(chunks), len(chunk))
            out, _ = _call(provider, system,
                           f"{header} This is part {n} of {len(chunks)} of the {'interview' if interview else 'meeting'}.\n\nTranscript:\n"
                           + "\n".join(chunk) + "\n\nReturn the JSON now.", out_model, max_tokens)
            partials.append(out)
        merged_input = "\n\n".join(p.model_dump_json() for p in partials)
        out, raw = _call(provider, (INTERVIEW_MERGE_PROMPT if interview else MERGE_PROMPT).replace("{notes_language}", lang_name),
                         f"{header}\n\nPartial notes:\n{merged_input}\n\nReturn the merged JSON now.", out_model,
                         8000 if interview else 4000)
        notes = to_notes(out, segments, provider.id, provider.model, raw)
    if not include_actions:
        notes.action_items = []
    return notes


class ActionsOut(BaseModel):
    actionItems: list[ActionItemOut] = Field(default_factory=list)


ACTION_CHUNK_CHARS = 9000   # ~2.5k tokens: small chunks make the first items appear within seconds


def iter_action_items(provider, segments: list[Segment], speaker_names: dict[str | None, str],
                      meeting_date: str, notes_language: str = "en", user_context: str | None = None):
    """Yield (chunk_index, chunk_count, items) as the model reads the transcript chunk by chunk,
    so the caller can store items and report progress while extraction is still running."""
    if not segments:
        return
    if isinstance(provider, ExtractiveProvider):
        yield 0, 1, extractive_notes(segments, speaker_names).action_items
        return
    lines = render_transcript(segments, speaker_names)
    header = f"Meeting date: {meeting_date}.{_calendar_hint(meeting_date)}" + _context_block(user_context)
    system = ACTIONS_PROMPT.replace("{{", "{").replace("}}", "}").replace("{notes_language}", LANG_NAMES.get(notes_language, notes_language))
    chunks = _chunks(lines, ACTION_CHUNK_CHARS)
    for i, chunk in enumerate(chunks):
        raw = provider.complete_json(system, f"{header}\n\nTranscript:\n" + "\n".join(chunk) + "\n\nReturn the JSON now.", max_tokens=1200)
        try:
            out = ActionsOut.model_validate(parse_json_object(raw)).actionItems
        except (ValueError, ValidationError) as e:
            raise ProviderError("The AI model returned an unusable response.", detail=f"{e}\n---\n{raw[:4000]}") from e
        yield i, len(chunks), [ActionItem(a.text.strip(), _clean_owner(a.owner), _clean_date(a.dueDate),
                                          (max(0.0, min(1.0, float(a.confidence))) if a.confidence is not None else None),
                                          _evidence(a.evidenceSegments, segments)) for a in out if a.text.strip()]





# --------------------------------------------------------------------------- #
# Speaker name inference: "Daan, kun jij de kleuren aanpassen?" → next speaker = Daan
# --------------------------------------------------------------------------- #
SPEAKER_PROMPT = """You read a meeting transcript where speakers are only labelled "Speaker 1", "Speaker 2", ….
Work out the real first names of speakers ONLY from explicit evidence in the transcript:
- someone addresses a person by name and that person answers next ("Daan, kun jij…?" → the next speaker is Daan),
- someone introduces themselves ("Hi, I'm Karen"),
- someone thanks or refers to the previous speaker by name.
Never guess. If there is no clear evidence for a speaker, leave them out.

Respond with ONE strict JSON object only:
{"speakers": [{"label": "Speaker 3", "name": "Daan", "confidence": 0.0-1.0, "evidenceSegments": [12, 13]}]}"""

_NAME_STOP = {"speaker", "spreker", "ok", "oké", "okay", "yes", "ja", "nee", "no", "goedemorgen", "goedemiddag",
              "hallo", "hi", "hello", "morning", "thanks", "dank", "bedankt", "prima", "top", "goed", "agreed",
              "akkoord", "sure", "right", "well", "so", "dus", "nou", "and", "en", "but", "maar", "the", "de", "het",
              "een", "we", "wij", "ik", "i", "you", "jij", "u", "let", "laten", "dan", "then", "good", "great", "nog",
              "welkom", "welcome", "fijn", "mooi", "figma", "notion", "ollama", "whisper", "huddle",
              "monday", "tuesday", "wednesday", "thursday", "friday", "maandag", "dinsdag", "woensdag", "donderdag",
              "vrijdag", "september", "oktober", "october", "august", "augustus", "q4", "api", "dns", "csv", "pdf"}
# "Daan, kun jij…" / "…, Karen?" / "Thanks Xander." / "Bedankt Ellen"
_VOCATIVE = re.compile(
    r"(?:^|[.!?]\s+)([A-Z][a-zà-ÿ]{2,})\s*,\s+(?:kun|kan|wil|zou|heb|ben|can|could|would|do|did|are|will|what|how|hoe|wat)|"
    r",\s*([A-Z][a-zà-ÿ]{2,})\s*[?.!]\s*$|"
    r"\b(?i:thanks|thank you|bedankt|dank je|dank u)\s+([A-Z][a-zà-ÿ]{2,})\b", re.UNICODE)


def heuristic_speaker_names(segments: list[Segment]) -> dict[str, tuple[str, float]]:
    """Dependency-free inference: a vocative name in segment i assigns the name to the
    speaker of the next segment by a different speaker (address → answer), or to the
    previous speaker for "thanks X" patterns."""
    out: dict[str, tuple[str, float]] = {}
    taken: set[str] = set()
    for i, s in enumerate(segments):
        for m in _VOCATIVE.finditer(s.text):
            addr = m.group(1) or m.group(2)
            thanked = m.group(3)
            name = addr or thanked
            if not name or name.lower() in _NAME_STOP:
                continue
            if addr:
                nxt = next((t for t in segments[i + 1:i + 3] if t.speaker_label and t.speaker_label != s.speaker_label), None)
                target = nxt.speaker_label if nxt else None
            else:
                prv = next((t for t in reversed(segments[max(0, i - 2):i]) if t.speaker_label and t.speaker_label != s.speaker_label), None)
                target = prv.speaker_label if prv else None
            if not target or target in out or name in taken:
                continue
            out[target] = (name, 0.7)
            taken.add(name)
    return out


class SpeakerOut(BaseModel):
    label: Text
    name: Text
    confidence: float = 0.0
    evidenceSegments: list[int] = Field(default_factory=list)


class SpeakersOut(BaseModel):
    speakers: list[SpeakerOut] = Field(default_factory=list)


def infer_speaker_names(provider, segments: list[Segment], labels: dict[str | None, str],
                        min_confidence: float = 0.75) -> dict[str, tuple[str, float]]:
    """Combine the heuristic with an LLM pass (when a model is available). Returns
    {label: (name, confidence)} for labels that still carry a generic name."""
    generic = {lab for lab, name in labels.items() if lab and name == lab}
    result = {k: v for k, v in heuristic_speaker_names(segments).items() if k in generic}
    if isinstance(provider, ExtractiveProvider) or not generic:
        return result
    lines = render_transcript(segments, labels)
    if len("\n".join(lines)) > CHUNK_CHARS:
        lines = lines[: max(40, int(len(lines) * CHUNK_CHARS / len("\n".join(lines))))]
    try:
        raw = provider.complete_json(SPEAKER_PROMPT, "Transcript:\n" + "\n".join(lines) + "\n\nReturn the JSON now.",
                                     max_tokens=600)
        out = SpeakersOut.model_validate(parse_json_object(raw))
    except (ProviderError, ValueError, ValidationError) as e:
        log.warning("speaker inference skipped: %s", e)
        return result
    used = {n for n, _ in result.values()}
    for sp in out.speakers:
        name = _clean_owner(sp.name)
        if not name or sp.label not in generic or sp.confidence < min_confidence:
            continue
        same = sp.label in result and result[sp.label][0] == name
        if name in used and not same:
            continue
        if sp.label not in result or result[sp.label][1] < sp.confidence:
            result[sp.label] = (name, float(sp.confidence))
            used.add(name)
    return result


def extractive_notes(segments: list[Segment], speaker_names: dict[str | None, str]) -> MeetingNotes:
    """Dependency-free fallback when no local AI model is available (huddle_engine.text):
    an overview from the most content-dense sentences, decision-sounding sentences, likely
    commitments, and keywords as topics. Owners and dates are deliberately left null — the
    heuristics cannot attribute them, and Huddle never fabricates."""
    from ..text import action_sentences, extractive_summary, keywords

    texts = [s.text.strip() for s in segments if s.text.strip()]
    summ = extractive_summary(" ".join(texts))

    def find_evidence(text: str) -> Evidence:
        needle = text.rstrip(".").lower()[:40]
        for i, s in enumerate(segments):
            if needle and needle in s.text.lower():
                return Evidence(start=s.start, end=s.end, segment_idx=i)
        return Evidence()

    return MeetingNotes(
        summary=summ.overview,
        topics=[Topic(k, "") for k in keywords(texts, top_n=6)],
        decisions=[Decision(d, find_evidence(d)) for d in summ.decisions],
        action_items=[ActionItem(a, None, None, 0.4, find_evidence(a)) for a in action_sentences(texts)],
        provider="extractive", model="extractive")


_FILLERS = re.compile(r"\b(?:uh+m*|u+m+|eh+m*|ehm|hmm+|erm|mm+|nou ja|zeg maar|you know|i mean|like,)\b[,.]?\s*", re.IGNORECASE)
_QUESTION_SENTENCE = re.compile(r"[^.!?]*\?")


def _strip_fillers(text: str) -> str:
    t = _FILLERS.sub("", text)
    t = re.sub(r"\s+([,.!?])", r"\1", t)
    t = re.sub(r"\s{2,}", " ", t).strip()
    return t[:1].upper() + t[1:] if t else t


def extractive_interview(segments: list[Segment], speaker_names: dict[str | None, str]) -> MeetingNotes:
    """Dependency-free interview fallback: every line that asks something becomes a question;
    what other speakers say until the next question is its answer, with fillers removed.
    Answers given later that refer back to an earlier question cannot be detected without a
    model, so they stay with the question they followed."""
    from ..text import extractive_summary

    texts = [s.text.strip() for s in segments if s.text.strip()]
    summ = extractive_summary(" ".join(texts))
    name = lambda s: speaker_names.get(s.speaker_label, s.speaker_label)  # noqa: E731
    questions: list[Question] = []
    i = 0
    while i < len(segments):
        s = segments[i]
        asks = _QUESTION_SENTENCE.findall(s.text)
        if not asks:
            i += 1
            continue
        q_text = _strip_fillers(asks[-1].strip())
        if len(q_text) < 8:
            i += 1
            continue
        parts, j = [], i + 1
        who: list[str] = []
        while j < len(segments) and not (segments[j].speaker_label != s.speaker_label and "?" in segments[j].text) \
                and not (segments[j].speaker_label == s.speaker_label and "?" in segments[j].text):
            t = segments[j]
            if t.speaker_label != s.speaker_label and t.text.strip():
                parts.append(_strip_fillers(t.text.strip()))
                n = name(t)
                if n and n not in who:
                    who.append(n)
            j += 1
        answer = " ".join(parts).strip()
        if answer:
            asked = name(s)
            questions.append(Question(q_text, answer,
                                      asked if asked and not re.fullmatch(r"(speaker|spreker)\s*\d+", asked, re.I) else None,
                                      ", ".join(w for w in who if not re.fullmatch(r"(speaker|spreker)\s*\d+", w, re.I)) or None,
                                      Evidence(start=s.start, end=segments[j - 1].end if j - 1 > i else s.end, segment_idx=i)))
        i = max(j, i + 1)
    return MeetingNotes(summary=summ.overview, topics=[], decisions=[], action_items=[], questions=questions,
                        provider="extractive", model="extractive")
