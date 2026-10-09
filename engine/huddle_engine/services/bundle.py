"""The result of processing a meeting as one JSON document — what a Huddle Server returns and the
desktop imports. Evidence references use segment positions (``idx``), never database ids, so the
bundle is valid in any database. Known-voice assignments stay out: voices live on the machine that
named them."""
from __future__ import annotations

import json
import time
from typing import Any

from ..db import Database
from . import meetings as ms

BUNDLE_VERSION = 1


def export_bundle(db: Database, meeting_id: str) -> dict[str, Any]:
    m = ms.get_meeting(db, meeting_id)
    if not m:
        raise KeyError(meeting_id)
    speakers = db.query("SELECT * FROM meeting_speakers WHERE meeting_id = ? ORDER BY color_index, id", (meeting_id,))
    label_of = {r["id"]: r["label"] for r in speakers}
    segs = db.query("SELECT * FROM transcript_segments WHERE meeting_id = ? ORDER BY start, idx", (meeting_id,))
    idx_of_id = {r["id"]: i for i, r in enumerate(segs)}
    words: dict[int, list[dict]] = {}
    for w in db.query("SELECT w.* FROM transcript_words w JOIN transcript_segments s ON s.id = w.segment_id"
                      " WHERE s.meeting_id = ? ORDER BY w.start", (meeting_id,)):
        words.setdefault(w["segment_id"], []).append({"start": w["start"], "end": w["end"], "word": w["word"],
                                                      "confidence": w["confidence"]})

    def ev(r) -> dict:
        return {"evidenceStart": r["evidence_start"], "evidenceEnd": r["evidence_end"],
                "segmentIdx": idx_of_id.get(r["segment_id"]) if r["segment_id"] is not None else None}

    notes = None
    srow = db.one("SELECT * FROM summaries WHERE meeting_id = ?", (meeting_id,))
    if srow:
        notes = {
            "summary": srow["summary"], "provider": srow["provider"], "model": srow["model"],
            "topics": [{"title": r["title"], "summary": r["summary"]}
                       for r in db.query("SELECT title, summary FROM topics WHERE meeting_id = ? ORDER BY position", (meeting_id,))],
            "questions": [{"question": r["question"], "answer": r["answer"], "askedBy": r["asked_by"], "answeredBy": r["answered_by"], **ev(r)}
                          for r in db.query("SELECT * FROM interview_questions WHERE meeting_id = ? ORDER BY position", (meeting_id,))],
            "decisions": [{"text": r["text"], **ev(r)}
                          for r in db.query("SELECT * FROM decisions WHERE meeting_id = ? ORDER BY position", (meeting_id,))],
            "actionItems": [{"text": r["text"], "owner": r["owner"], "dueDate": r["due_date"], "confidence": r["confidence"], **ev(r)}
                            for r in db.query("SELECT * FROM action_items WHERE meeting_id = ? AND source = 'auto' ORDER BY position", (meeting_id,))],
        }
    return {
        "version": BUNDLE_VERSION,
        "meeting": {"title": m.title, "titleIsDefault": ms.is_default_title(m.title), "language": m.language,
                    "durationSec": m.duration_sec, "mode": m.mode},
        "speakers": [{"label": r["label"], "displayName": r["display_name"], "nameSource": r["name_source"],
                      "embedding": json.loads(r["embedding"]) if r["embedding"] else None,
                      "embeddingModel": r["embedding_model"], "colorIndex": r["color_index"]} for r in speakers],
        "segments": [{"idx": i, "start": r["start"], "end": r["end"], "text": r["text"], "confidence": r["confidence"],
                      "language": r["language"], "speaker": label_of.get(r["meeting_speaker_id"]),
                      "words": words.get(r["id"], [])} for i, r in enumerate(segs)],
        "notes": notes,
    }


def import_bundle(db: Database, meeting_id: str, bundle: dict[str, Any], notes_provider_prefix: str = "server:") -> dict[str, int]:
    """Replace the transcript, speakers and (when present) the notes of `meeting_id` with the
    bundle's. Speakers keep their server-side display names (inferred from the conversation);
    known-voice matching is left to the local ``identifying_speakers`` stage. Returns counts."""
    if int(bundle.get("version") or 0) != BUNDLE_VERSION:
        raise ValueError(f"Unsupported result version {bundle.get('version')!r}")
    meeting = bundle.get("meeting") or {}
    now = time.time()
    with db.tx() as c:
        c.execute("DELETE FROM transcript_segments WHERE meeting_id = ?", (meeting_id,))
        c.execute("DELETE FROM meeting_speakers WHERE meeting_id = ?", (meeting_id,))
        ids: dict[str, int] = {}
        for i, sp in enumerate(bundle.get("speakers") or []):
            emb = sp.get("embedding")
            cur = c.execute("INSERT INTO meeting_speakers(meeting_id, label, display_name, name_source, embedding, embedding_model, color_index)"
                            " VALUES (?,?,?,?,?,?,?)",
                            (meeting_id, sp["label"], sp.get("displayName"), sp.get("nameSource") if sp.get("displayName") else None,
                             json.dumps(emb) if emb else None, sp.get("embeddingModel") if emb else None,
                             int(sp.get("colorIndex") if sp.get("colorIndex") is not None else i)))
            ids[sp["label"]] = int(cur.lastrowid)
        seg_ids: list[int] = []
        for i, s in enumerate(bundle.get("segments") or []):
            cur = c.execute("INSERT INTO transcript_segments(meeting_id, meeting_speaker_id, idx, start, \"end\", text, confidence, language)"
                            " VALUES (?,?,?,?,?,?,?,?)",
                            (meeting_id, ids.get(s.get("speaker")), i, float(s["start"]), float(s["end"]), s["text"],
                             s.get("confidence"), s.get("language")))
            sid = int(cur.lastrowid)
            seg_ids.append(sid)
            if s.get("words"):
                c.executemany("INSERT INTO transcript_words(segment_id, start, \"end\", word, confidence) VALUES (?,?,?,?,?)",
                              [(sid, w["start"], w["end"], w["word"], w.get("confidence")) for w in s["words"]])
        c.execute("DELETE FROM live_segments WHERE recording_id = ?", (meeting_id,))
        if meeting.get("language"):
            c.execute("UPDATE meetings SET language = ? WHERE id = ?", (meeting["language"], meeting_id))
        if meeting.get("durationSec"):
            c.execute("UPDATE meetings SET duration_sec = COALESCE(duration_sec, ?) WHERE id = ?", (meeting["durationSec"], meeting_id))
        row = c.execute("SELECT title FROM meetings WHERE id = ?", (meeting_id,)).fetchone()
        if row and ms.is_default_title(row["title"]) and meeting.get("title") and not meeting.get("titleIsDefault"):
            c.execute("UPDATE meetings SET title = ? WHERE id = ?", (meeting["title"], meeting_id))

        def seg_id(item: dict) -> int | None:
            i = item.get("segmentIdx")
            return seg_ids[i] if isinstance(i, int) and 0 <= i < len(seg_ids) else None

        notes = bundle.get("notes")
        if notes:
            provider = f"{notes_provider_prefix}{notes.get('provider') or 'unknown'}"
            c.execute("INSERT INTO summaries(meeting_id, summary, provider, model, raw_json, created_at) VALUES (?,?,?,?,?,?)"
                      " ON CONFLICT(meeting_id) DO UPDATE SET summary=excluded.summary, provider=excluded.provider,"
                      " model=excluded.model, raw_json=excluded.raw_json, created_at=excluded.created_at",
                      (meeting_id, notes.get("summary") or "", provider, notes.get("model"), None, now))
            c.execute("DELETE FROM topics WHERE meeting_id = ?", (meeting_id,))
            c.executemany("INSERT INTO topics(meeting_id, position, title, summary) VALUES (?,?,?,?)",
                          [(meeting_id, i, t["title"], t.get("summary") or "") for i, t in enumerate(notes.get("topics") or [])])
            c.execute("DELETE FROM interview_questions WHERE meeting_id = ?", (meeting_id,))
            c.executemany("INSERT INTO interview_questions(meeting_id, position, question, answer, asked_by, answered_by,"
                          " evidence_start, evidence_end, segment_id) VALUES (?,?,?,?,?,?,?,?,?)",
                          [(meeting_id, i, q["question"], q.get("answer") or "", q.get("askedBy"), q.get("answeredBy"),
                            q.get("evidenceStart"), q.get("evidenceEnd"), seg_id(q)) for i, q in enumerate(notes.get("questions") or [])])
            c.execute("DELETE FROM decisions WHERE meeting_id = ?", (meeting_id,))
            c.executemany("INSERT INTO decisions(meeting_id, position, text, evidence_start, evidence_end, segment_id) VALUES (?,?,?,?,?,?)",
                          [(meeting_id, i, d["text"], d.get("evidenceStart"), d.get("evidenceEnd"), seg_id(d))
                           for i, d in enumerate(notes.get("decisions") or [])])
            prev_done = {(r["text"] or "").strip().lower(): r["done"]
                         for r in c.execute("SELECT text, done FROM action_items WHERE meeting_id = ?", (meeting_id,))}
            c.execute("DELETE FROM action_items WHERE meeting_id = ? AND source = 'auto'", (meeting_id,))
            c.executemany("INSERT INTO action_items(meeting_id, position, text, owner, due_date, confidence, evidence_start,"
                          " evidence_end, segment_id, done, source, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,'auto',?)",
                          [(meeting_id, i, a["text"], a.get("owner"), a.get("dueDate"), a.get("confidence"), a.get("evidenceStart"),
                            a.get("evidenceEnd"), seg_id(a), prev_done.get((a["text"] or "").strip().lower(), 0), now)
                           for i, a in enumerate(notes.get("actionItems") or [])])
    return {"speakers": len(ids), "segments": len(seg_ids), "notes": 1 if notes else 0}
