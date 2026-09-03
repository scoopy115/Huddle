"""Ask this meeting / ask all meetings: retrieval → local LLM with citations.

Retrieval combines FTS hits over transcripts with matching decisions and action items
(including open ones when the question is about tasks), so questions like "which
deadlines were mentioned this week?" or "what is still open for Daan?" work without
pasting whole transcripts into the prompt."""
from __future__ import annotations

import re
from datetime import datetime

from ..db import Database
from ..providers.base import ProviderError
from ..providers.llm import ExtractiveProvider
from ..schemas import SearchHit
from . import action_items as ai_svc
from . import search as search_svc
from . import transcripts

ASK_SYSTEM = ("You answer questions about the user's meetings using ONLY the material provided: transcript excerpts, "
              "decisions and action items. Each item starts with a bracketed source, e.g. [Branding meeting · 2026-09-03 · "
              "Speaker · 12:41] or [Branding meeting · action item · owner Daan · due 2026-09-05]. Cite the sources you rely "
              "on in parentheses after the relevant sentence, e.g. (Branding meeting · 12:41). If the material does not "
              "contain the answer, say so plainly. Answer in {language}. Be concise; use a short list when listing several items.")

_TASK_WORDS = re.compile(r"\b(action|actions|actie|acties|taken|taak|todo|to-do|open|deadline|deadlines|due|owner|"
                         r"verantwoordelijk|opvolg|follow.?up|assigned|toegewezen)\b", re.IGNORECASE)
_DECISION_WORDS = re.compile(r"\b(decid|decision|besl[ou]t|besliss|afgesproken|agreed|agree)\w*", re.IGNORECASE)


def _fmt(sec: float) -> str:
    m, s = divmod(int(sec), 60)
    return f"{m:02d}:{s:02d}"


def _date(ts: float | None) -> str:
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d") if ts else ""


def _terms(question: str) -> list[str]:
    from ..text import tokenize
    return [t for t in tokenize(question) if len(t) > 3][:8]


def _hits(db: Database, question: str, meeting_id: str | None, limit: int) -> list[SearchHit]:
    hits = search_svc.search(db, question, limit=limit, meeting_id=meeting_id)
    if not hits:
        terms = _terms(question)
        if terms:
            hits = search_svc.search(db, " ".join(terms), limit=limit, meeting_id=meeting_id)
    return hits


def _notes_context(db: Database, question: str, meeting_id: str | None) -> list[str]:
    """Decisions and action items relevant to the question (LIKE match on salient terms;
    all open items when the question is about tasks)."""
    terms = _terms(question)
    out: list[str] = []
    want_tasks = bool(_TASK_WORDS.search(question))
    want_decisions = bool(_DECISION_WORDS.search(question))
    scope = "AND a.meeting_id = ?" if meeting_id else ""
    args_scope = [meeting_id] if meeting_id else []

    conds = " OR ".join(["LOWER(a.text) LIKE ?", "LOWER(a.owner) LIKE ?"] * max(1, len(terms))) if terms else "0"
    like_args = [f"%{t}%" for t in terms for _ in (0, 1)]
    rows = db.query(f"SELECT a.*, m.title, m.started_at FROM action_items a JOIN meetings m ON m.id = a.meeting_id"
                    f" WHERE (({conds}) {'OR a.done = 0' if want_tasks else ''}) {scope} ORDER BY m.started_at DESC LIMIT 40",
                    like_args + args_scope)
    for r in rows:
        meta = f"owner {r['owner']}" if r["owner"] else "no owner"
        meta += f" · due {r['due_date']}" if r["due_date"] else ""
        meta += " · done" if r["done"] else " · open"
        out.append(f"[{r['title']} · {_date(r['started_at'])} · action item · {meta}] {r['text']}")

    dconds = " OR ".join(["LOWER(d.text) LIKE ?"] * max(1, len(terms))) if terms else "0"
    drows = db.query(f"SELECT d.*, m.title, m.started_at FROM decisions d JOIN meetings m ON m.id = d.meeting_id"
                     f" WHERE ({dconds} {'OR 1' if want_decisions and meeting_id else ''}) {scope.replace('a.', 'd.')}"
                     f" ORDER BY m.started_at DESC LIMIT 30", [f"%{t}%" for t in terms] + args_scope)
    for r in drows:
        out.append(f"[{r['title']} · {_date(r['started_at'])} · decision · {_fmt(r['evidence_start'] or 0)}] {r['text']}")
    return out


def ask(db: Database, provider, question: str, meeting_id: str | None = None, limit: int = 24, language: str = "English") -> dict:
    hits = _hits(db, question, meeting_id, limit)
    notes = _notes_context(db, question, meeting_id)
    sources = [h.model_dump(by_alias=True) for h in hits[:12]]
    if not hits and not notes:
        return {"answer": "I couldn't find anything about that in " + ("this meeting." if meeting_id else "your meetings."),
                "sources": []}
    if isinstance(provider, ExtractiveProvider):
        lines = [f"[{h.meeting_title} · {h.speaker_name or 'Speaker'} · {_fmt(h.start)}] {h.text}" for h in hits[:8]] + notes[:8]
        return {"answer": "No AI model is available in Ollama, so here is the most relevant material:\n\n" + "\n".join(lines),
                "sources": sources}
    excerpts = []
    for h in hits:
        window = transcripts.segment_window(db, h.segment_id, before=1, after=1)
        text = " ".join(w.text for w in window) if window else h.text
        excerpts.append(f"[{h.meeting_title} · {_date(h.meeting_started_at)} · {h.speaker_name or 'Speaker'} · {_fmt(h.start)}] {text}")
    today = datetime.now().strftime("%Y-%m-%d (%A)")
    user = (f"Today is {today}.\nQuestion: {question}\n\nTranscript excerpts:\n" + ("\n\n".join(excerpts) or "(none)")
            + "\n\nDecisions and action items:\n" + ("\n".join(notes) or "(none)"))
    try:
        answer = provider.complete(ASK_SYSTEM.format(language=language), user, max_tokens=900).strip()
    except ProviderError as e:
        return {"answer": f"The AI model could not answer: {e}", "sources": sources, "error": str(e)}
    return {"answer": answer, "sources": sources}


__all__ = ["ai_svc", "ask"]
