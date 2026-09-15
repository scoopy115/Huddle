"""Projects: folders of meetings. A meeting belongs to at most one project.

Besides plain CRUD this module decides which project a *new* meeting probably belongs to
(`suggest`). The suggestion is written on the meeting (`suggested_project_*`) and shown in the
UI as "Looks like part of …" with Add / Not this one; nothing is ever assigned automatically.

How the suggestion is made: every project gets a text profile (its name, description and the
titles, summaries and topics of the meetings already in it) and the meeting gets one too
(title, summary, topics, decisions; the first part of the transcript when there are no notes
yet). A lexical score ranks the projects — how much of the project's *name* occurs in the
meeting is the strongest signal, cosine similarity of the two token bags the second. When a
local AI model is available the top candidates are handed to it with the meeting's notes and it
makes the final call (it can also say "none"); without a model the lexical score alone decides,
with a higher bar.
"""
from __future__ import annotations

import json
import logging
import math
import time
import uuid
from collections import Counter

from ..db import Database
from ..providers.base import ProviderError
from ..providers.llm import ExtractiveProvider, parse_json_object
from ..schemas import CreateProjectRequest, Meeting, Project, ProjectDetail
from ..text import tokenize

log = logging.getLogger(__name__)

MIN_CONFIDENCE = 0.45          # below this no suggestion is stored
MIN_CONFIDENCE_LEXICAL = 0.55  # bar without an AI model (the score is cruder)
MAX_CANDIDATES = 6             # how many projects the model gets to choose from


def _row(r) -> Project:
    keys = r.keys()
    return Project(id=r["id"], name=r["name"], description=r["description"], color_index=r["color_index"],
                   created_at=r["created_at"], updated_at=r["updated_at"],
                   meeting_count=r["meeting_count"] if "meeting_count" in keys else 0,
                   open_action_count=r["open_action_count"] if "open_action_count" in keys else 0,
                   last_meeting_at=r["last_meeting_at"] if "last_meeting_at" in keys else None,
                   suggestion_count=r["suggestion_count"] if "suggestion_count" in keys else 0)


_LIST_SQL = """
    SELECT p.*,
           (SELECT COUNT(*) FROM meetings m WHERE m.project_id = p.id) AS meeting_count,
           (SELECT COUNT(*) FROM action_items a JOIN meetings m ON m.id = a.meeting_id
             WHERE m.project_id = p.id AND a.done = 0) AS open_action_count,
           (SELECT MAX(m.started_at) FROM meetings m WHERE m.project_id = p.id) AS last_meeting_at,
           (SELECT COUNT(*) FROM meetings m WHERE m.suggested_project_id = p.id AND m.project_id IS NULL) AS suggestion_count
    FROM projects p
"""


def list_projects(db: Database) -> list[Project]:
    return [_row(r) for r in db.query(_LIST_SQL + " ORDER BY last_meeting_at DESC NULLS LAST, LOWER(p.name)")]


def get_project(db: Database, project_id: str) -> Project | None:
    r = db.one(_LIST_SQL + " WHERE p.id = ?", (project_id,))
    return _row(r) if r else None


def find_by_name(db: Database, name: str) -> Project | None:
    r = db.one(_LIST_SQL + " WHERE LOWER(p.name) = LOWER(?)", (name.strip(),))
    return _row(r) if r else None


def create(db: Database, req: CreateProjectRequest) -> Project:
    name = req.name.strip()
    if not name:
        raise ValueError("A project needs a name.")
    if find_by_name(db, name):
        raise ValueError(f"There is already a project called “{name}”.")
    pid = uuid.uuid4().hex[:12]
    now = time.time()
    n = db.one("SELECT COUNT(*) AS n FROM projects")["n"]
    with db.tx() as c:
        c.execute("INSERT INTO projects(id, name, description, color_index, created_at, updated_at) VALUES (?,?,?,?,?,?)",
                  (pid, name, (req.description or "").strip() or None, n % 8, now, now))
        for mid in req.meeting_ids:
            c.execute("UPDATE meetings SET project_id = ?, suggested_project_id = NULL, suggested_project_confidence = NULL,"
                      " suggested_project_reason = NULL WHERE id = ?", (pid, mid))
    return get_project(db, pid)


def update(db: Database, project_id: str, name: str | None = None, description: str | None = None) -> Project | None:
    sets, args = [], []
    if name is not None:
        name = name.strip()
        if not name:
            raise ValueError("A project needs a name.")
        other = find_by_name(db, name)
        if other and other.id != project_id:
            raise ValueError(f"There is already a project called “{name}”.")
        sets.append("name = ?")
        args.append(name)
    if description is not None:
        sets.append("description = ?")
        args.append(description.strip() or None)
    if sets:
        sets.append("updated_at = ?")
        args.append(time.time())
        db.execute(f"UPDATE projects SET {', '.join(sets)} WHERE id = ?", (*args, project_id))
    return get_project(db, project_id)


def delete(db: Database, project_id: str) -> None:
    """Removes the folder only; its meetings stay (unassigned)."""
    with db.tx() as c:
        c.execute("UPDATE meetings SET project_id = NULL WHERE project_id = ?", (project_id,))
        c.execute("UPDATE meetings SET suggested_project_id = NULL, suggested_project_confidence = NULL,"
                  " suggested_project_reason = NULL WHERE suggested_project_id = ?", (project_id,))
        c.execute("DELETE FROM projects WHERE id = ?", (project_id,))


def assign(db: Database, meeting_id: str, project_id: str | None) -> None:
    """Put a meeting in a project (or take it out with None). Any pending suggestion is settled."""
    if project_id and not get_project(db, project_id):
        raise KeyError(project_id)
    db.execute("UPDATE meetings SET project_id = ?, suggested_project_id = NULL, suggested_project_confidence = NULL,"
               " suggested_project_reason = NULL WHERE id = ?", (project_id, meeting_id))
    if project_id:
        db.execute("UPDATE projects SET updated_at = ? WHERE id = ?", (time.time(), project_id))


def dismiss_suggestion(db: Database, meeting_id: str) -> None:
    db.execute("UPDATE meetings SET suggested_project_id = NULL, suggested_project_confidence = NULL,"
               " suggested_project_reason = NULL WHERE id = ?", (meeting_id,))


def detail(db: Database, project_id: str) -> ProjectDetail | None:
    from . import meetings as ms
    p = get_project(db, project_id)
    if not p:
        return None
    return ProjectDetail(project=p, meetings=ms.list_meetings(db, project_id=project_id),
                         suggested=ms.list_meetings(db, suggested_project_id=project_id))


# ---- suggestion ------------------------------------------------------------------------- #
def _meeting_text(db: Database, meeting_id: str, transcript_chars: int = 6000) -> tuple[str, str]:
    """(notes, transcript excerpt) for one meeting. Notes = title, summary, topics, decisions."""
    m = db.one("SELECT title FROM meetings WHERE id = ?", (meeting_id,))
    parts = [m["title"] if m else ""]
    s = db.one("SELECT summary FROM summaries WHERE meeting_id = ?", (meeting_id,))
    if s and s["summary"]:
        parts.append(s["summary"])
    for r in db.query("SELECT title, summary FROM topics WHERE meeting_id = ? ORDER BY position", (meeting_id,)):
        parts.append(f"{r['title']}. {r['summary']}")
    for r in db.query("SELECT text FROM decisions WHERE meeting_id = ? ORDER BY position", (meeting_id,)):
        parts.append(r["text"])
    words, n = [], 0
    for r in db.query("SELECT text FROM transcript_segments WHERE meeting_id = ? ORDER BY start", (meeting_id,)):
        words.append(r["text"])
        n += len(r["text"]) + 1
        if n >= transcript_chars:
            break
    return "\n".join(p for p in parts if p), " ".join(words)


def _project_text(db: Database, project: Project, per_meeting_chars: int = 500) -> str:
    parts = [project.name, project.description or ""]
    rows = db.query("SELECT m.id, m.title, s.summary FROM meetings m LEFT JOIN summaries s ON s.meeting_id = m.id"
                    " WHERE m.project_id = ? ORDER BY m.started_at DESC LIMIT 12", (project.id,))
    for r in rows:
        parts.append(r["title"])
        if r["summary"]:
            parts.append(r["summary"][:per_meeting_chars])
        for t in db.query("SELECT title FROM topics WHERE meeting_id = ? ORDER BY position LIMIT 8", (r["id"],)):
            parts.append(t["title"])
    return "\n".join(p for p in parts if p)


def _cosine(a: Counter, b: Counter) -> float:
    if not a or not b:
        return 0.0
    dot = sum(v * b[k] for k, v in a.items() if k in b)
    na = math.sqrt(sum(v * v for v in a.values()))
    nb = math.sqrt(sum(v * v for v in b.values()))
    return dot / (na * nb) if na and nb else 0.0


def lexical_scores(db: Database, meeting_id: str, projects: list[Project]) -> list[tuple[Project, float]]:
    """Projects ranked by how much the meeting looks like them (0–1). Pure text statistics:
    a project whose name is said in the meeting scores high; otherwise the overlap of content
    words between the meeting and what is already in the project."""
    notes, transcript = _meeting_text(db, meeting_id)
    m_tokens = Counter(tokenize(notes) * 3 + tokenize(transcript))   # notes weigh more than raw talk
    if not m_tokens:
        return [(p, 0.0) for p in projects]
    out = []
    for p in projects:
        name_tokens = set(tokenize(p.name))
        name_hit = sum(1 for t in name_tokens if t in m_tokens) / len(name_tokens) if name_tokens else 0.0
        sim = _cosine(m_tokens, Counter(tokenize(_project_text(db, p))))
        score = 0.6 * name_hit + 0.4 * min(1.0, sim / 0.35)
        out.append((p, round(score, 3)))
    out.sort(key=lambda x: -x[1])
    return out


_SYSTEM = ("You file meeting notes into projects. You get one meeting (title, summary, topics, decisions, a transcript "
           "excerpt) and a numbered list of candidate projects (name, description, what earlier meetings in the "
           "project were about). Decide whether the meeting clearly belongs to one of the projects. Only pick a "
           "project when the subject matter, product, client or team really matches; a shared person or a generic "
           "topic (planning, budget) is not enough. If no project fits, answer with projectId null. Respond with JSON "
           "only: {\"projectId\": <id or null>, \"confidence\": <0..1>, \"reason\": <one short sentence in {language}>}.")


def _llm_pick(provider, db: Database, meeting_id: str, candidates: list[tuple[Project, float]],
              language: str) -> tuple[Project | None, float, str] | None:
    notes, transcript = _meeting_text(db, meeting_id, transcript_chars=3000)
    lines = []
    for p, _score in candidates:
        lines.append(f"- projectId \"{p.id}\": {p.name}" + (f" — {p.description}" if p.description else ""))
        rows = db.query("SELECT m.title, s.summary FROM meetings m LEFT JOIN summaries s ON s.meeting_id = m.id"
                        " WHERE m.project_id = ? ORDER BY m.started_at DESC LIMIT 4", (p.id,))
        for r in rows:
            lines.append(f"    · earlier meeting: {r['title']}" + (f": {r['summary'][:220]}" if r["summary"] else ""))
    user = (f"MEETING\n{notes}\n\nTranscript excerpt:\n{transcript[:3000]}\n\nCANDIDATE PROJECTS\n" + "\n".join(lines)
            + "\n\nWhich project does this meeting belong to, if any?")
    raw = provider.complete_json(_SYSTEM.replace("{language}", language), user, max_tokens=300)
    data = parse_json_object(raw)
    pid = data.get("projectId")
    conf = float(data.get("confidence") or 0.0)
    reason = str(data.get("reason") or "").strip()
    if pid in (None, "", "null"):
        return (None, conf, reason)
    chosen = next((p for p, _ in candidates if p.id == str(pid)), None)
    if not chosen:
        # tolerate the model answering with the name instead of the id
        chosen = next((p for p, _ in candidates if p.name.lower() == str(pid).strip().lower()), None)
    if not chosen:
        return None
    return (chosen, max(0.0, min(1.0, conf)), reason)


def suggest(db: Database, meeting_id: str, provider=None, language: str = "English") -> Project | None:
    """Work out which project the meeting probably belongs to and store it as a suggestion.
    Skips meetings that already have a project. Returns the suggested project, if any."""
    m = db.one("SELECT project_id FROM meetings WHERE id = ?", (meeting_id,))
    if not m or m["project_id"]:
        return None
    projects = list_projects(db)
    if not projects:
        dismiss_suggestion(db, meeting_id)
        return None
    ranked = lexical_scores(db, meeting_id, projects)
    best: tuple[Project | None, float, str] = (None, 0.0, "")
    use_llm = provider is not None and not isinstance(provider, ExtractiveProvider)
    if use_llm:
        # Few projects: the model sees them all. Many: only the ones the text match finds plausible.
        candidates = ranked if len(ranked) <= MAX_CANDIDATES else ([x for x in ranked if x[1] > 0.05][:MAX_CANDIDATES] or ranked[:MAX_CANDIDATES])
        try:
            picked = _llm_pick(provider, db, meeting_id, candidates, language)
            if picked is not None:
                best = picked
        except (ProviderError, ValueError, json.JSONDecodeError, KeyError, TypeError) as e:
            log.warning("project suggestion: model failed (%s); using text match", e)
            use_llm = False
    if not use_llm and ranked:
        p, score = ranked[0]
        runner_up = ranked[1][1] if len(ranked) > 1 else 0.0
        if score >= MIN_CONFIDENCE_LEXICAL and score - runner_up >= 0.1:
            best = (p, score, f"The meeting mentions what “{p.name}” is about.")
    project, conf, reason = best
    if not project or conf < MIN_CONFIDENCE:
        dismiss_suggestion(db, meeting_id)
        return None
    db.execute("UPDATE meetings SET suggested_project_id = ?, suggested_project_confidence = ?, suggested_project_reason = ?"
               " WHERE id = ?", (project.id, conf, reason or None, meeting_id))
    return project


def meetings_in(db: Database, project_id: str) -> list[Meeting]:
    from . import meetings as ms
    return ms.list_meetings(db, project_id=project_id)
