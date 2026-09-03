"""Full-text search over transcripts (SQLite FTS5). Semantic search will sit next
to this behind the same `SearchHit` shape once a local embedding provider lands."""
from __future__ import annotations

import re
import sqlite3

from ..db import Database
from ..schemas import SearchHit

_WORD = re.compile(r"\w+", re.UNICODE)


def fts_query(text: str, prefix: bool = True, op: str = "AND") -> str:
    """Safe FTS5 MATCH string: each word quoted (neutralises operators), optional
    prefix match so 'kleur' also finds 'kleuren' in Dutch and English alike."""
    terms = [t for t in _WORD.findall(text) if t]
    if not terms:
        return ""
    parts = [f'"{t}"' + ("*" if prefix else "") for t in terms]
    return f" {op} ".join(parts)


_SQL = """
    SELECT s.id AS segment_id, s.meeting_id, s.start, s."end", s.text,
           m.title AS meeting_title, m.started_at,
           COALESCE(ms.display_name, sp.name, ms.label) AS speaker_name,
           snippet(segments_fts, 0, '[', ']', '…', 14) AS snippet
    FROM segments_fts
    JOIN transcript_segments s ON s.id = segments_fts.rowid
    JOIN meetings m ON m.id = s.meeting_id
    LEFT JOIN meeting_speakers ms ON ms.id = s.meeting_speaker_id
    LEFT JOIN speakers sp ON sp.id = ms.speaker_id
    WHERE segments_fts MATCH ? {extra}
    ORDER BY bm25(segments_fts)
    LIMIT ?
"""


def search(db: Database, query: str, limit: int = 50, meeting_id: str | None = None) -> list[SearchHit]:
    q = query.strip()
    if not q:
        return []
    extra = "AND s.meeting_id = ?" if meeting_id else ""
    sql = _SQL.format(extra=extra)

    def run(match: str) -> list[sqlite3.Row]:
        args: list = [match]
        if meeting_id:
            args.append(meeting_id)
        args.append(limit)
        try:
            return db.query(sql, args)
        except sqlite3.OperationalError:
            return []

    rows = run(fts_query(q, prefix=True, op="AND"))
    if not rows:
        rows = run(fts_query(q, prefix=True, op="OR"))
    return [SearchHit(meeting_id=r["meeting_id"], meeting_title=r["meeting_title"], meeting_started_at=r["started_at"],
                      segment_id=r["segment_id"], speaker_name=r["speaker_name"], start=r["start"], end=r["end"],
                      snippet=r["snippet"], text=r["text"]) for r in rows]


def search_meetings(db: Database, query: str, limit: int = 20) -> list[dict]:
    """Meetings ranked by number of matching transcript segments (+ title matches)."""
    hits = search(db, query, limit=500)
    counts: dict[str, dict] = {}
    for h in hits:
        c = counts.setdefault(h.meeting_id, {"meetingId": h.meeting_id, "title": h.meeting_title,
                                             "startedAt": h.meeting_started_at, "matches": 0, "firstHit": h})
        c["matches"] += 1
    for r in db.query("SELECT id, title, started_at FROM meetings WHERE title LIKE ? LIMIT ?", (f"%{query}%", limit)):
        c = counts.setdefault(r["id"], {"meetingId": r["id"], "title": r["title"], "startedAt": r["started_at"],
                                        "matches": 0, "firstHit": None})
        c["matches"] += 3
    out = sorted(counts.values(), key=lambda c: (-c["matches"], -c["startedAt"]))[:limit]
    for c in out:
        c["firstHit"] = c["firstHit"].model_dump(by_alias=True) if c["firstHit"] else None
    return out
