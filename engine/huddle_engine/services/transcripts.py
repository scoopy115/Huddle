from __future__ import annotations

import json
import re
import time

from ..db import Database
from ..schemas import MeetingSpeaker, TranscriptSegment, TranscriptWord

_SEG_SQL = ("SELECT s.*, ms.label, ms.display_name, sp.name AS known_name"
            " FROM transcript_segments s"
            " LEFT JOIN meeting_speakers ms ON ms.id = s.meeting_speaker_id"
            " LEFT JOIN speakers sp ON sp.id = ms.speaker_id")


def _name(r) -> str | None:
    if r["meeting_speaker_id"] is None:
        return None
    return r["display_name"] or r["known_name"] or r["label"]


def segments(db: Database, meeting_id: str, with_words: bool = False) -> list[TranscriptSegment]:
    rows = db.query(_SEG_SQL + " WHERE s.meeting_id = ? ORDER BY s.start, s.idx", (meeting_id,))
    out = [TranscriptSegment(id=r["id"], meeting_id=r["meeting_id"], meeting_speaker_id=r["meeting_speaker_id"],
                             speaker_name=_name(r), idx=r["idx"], start=r["start"], end=r["end"], text=r["text"],
                             confidence=r["confidence"], language=r["language"]) for r in rows]
    if with_words and out:
        by_seg: dict[int, list[TranscriptWord]] = {}
        for w in db.query("SELECT w.* FROM transcript_words w JOIN transcript_segments s ON s.id = w.segment_id"
                          " WHERE s.meeting_id = ? ORDER BY w.start", (meeting_id,)):
            by_seg.setdefault(w["segment_id"], []).append(TranscriptWord(
                id=w["id"], segment_id=w["segment_id"], start=w["start"], end=w["end"], word=w["word"],
                confidence=w["confidence"]))
        for s in out:
            s.words = by_seg.get(s.id, [])
    return out


def segment_window(db: Database, segment_id: int, before: int = 3, after: int = 3) -> list[TranscriptSegment]:
    r = db.one("SELECT meeting_id, idx FROM transcript_segments WHERE id = ?", (segment_id,))
    if not r:
        return []
    rows = db.query(_SEG_SQL + " WHERE s.meeting_id = ? AND s.idx BETWEEN ? AND ? ORDER BY s.idx",
                    (r["meeting_id"], r["idx"] - before, r["idx"] + after))
    return [TranscriptSegment(id=x["id"], meeting_id=x["meeting_id"], meeting_speaker_id=x["meeting_speaker_id"],
                              speaker_name=_name(x), idx=x["idx"], start=x["start"], end=x["end"], text=x["text"],
                              confidence=x["confidence"]) for x in rows]


def speakers(db: Database, meeting_id: str) -> list[MeetingSpeaker]:
    rows = db.query(
        "SELECT ms.*, sp.name AS known_name, sg.name AS suggested_name,"
        " (SELECT COALESCE(SUM(s.\"end\" - s.start), 0) FROM transcript_segments s WHERE s.meeting_speaker_id = ms.id) AS talk"
        " FROM meeting_speakers ms LEFT JOIN speakers sp ON sp.id = ms.speaker_id"
        " LEFT JOIN speakers sg ON sg.id = ms.suggested_speaker_id"
        " WHERE ms.meeting_id = ? ORDER BY ms.color_index, ms.id", (meeting_id,))
    return [MeetingSpeaker(id=r["id"], meeting_id=r["meeting_id"], label=r["label"], display_name=r["display_name"],
                           speaker_id=r["speaker_id"], speaker_name=r["known_name"],
                           suggested_speaker_id=r["suggested_speaker_id"], suggested_speaker_name=r["suggested_name"],
                           suggested_confidence=r["suggested_confidence"], name_source=r["name_source"],
                           color_index=r["color_index"], talk_time_sec=float(r["talk"] or 0)) for r in rows]


def speaker_names(db: Database, meeting_id: str) -> dict[str | None, str]:
    """label -> best display name (for prompts/exports)."""
    return {s.label: (s.display_name or s.speaker_name or s.label) for s in speakers(db, meeting_id)}


def _upsert_known_speaker(db: Database, name: str, embedding: list[float] | None, model: str | None = None) -> int:
    """Fold a cluster's voice embedding into the named person's profile. A profile only ever
    mixes vectors of one embedding model; a vector from another model starts the profile over."""
    from .voices import running_mean
    now = time.time()
    row = db.one("SELECT id, embedding, n_samples, embedding_model FROM speakers WHERE name = ?", (name,))
    if row:
        if embedding:
            old = json.loads(row["embedding"]) if row["embedding"] else []
            same_model = bool(old) and (row["embedding_model"] or None) == (model or None) and len(old) == len(embedding)
            merged = running_mean(old, row["n_samples"], embedding) if same_model else embedding
            db.execute("UPDATE speakers SET embedding = ?, n_samples = ?, embedding_model = ?, updated_at = ? WHERE id = ?",
                       (json.dumps(merged), (row["n_samples"] + 1) if same_model else 1, model, now, row["id"]))
        return row["id"]
    cur = db.execute("INSERT INTO speakers(name, embedding, n_samples, embedding_model, created_at, updated_at) VALUES (?,?,?,?,?,?)",
                     (name, json.dumps(embedding) if embedding else None, 1 if embedding else 0, model if embedding else None, now, now))
    return int(cur.lastrowid)


def _replace_name(text: str | None, old: str, new: str) -> str | None:
    if not text or not old:
        return text
    return re.sub(rf"(?<!\w){re.escape(old)}(?!\w)", new, text)


def propagate_name(db: Database, meeting_id: str, old: str, new: str) -> None:
    """A speaker got a (new) name: update every place the old name/label was used as
    text — action-item owners, summary, topics, decisions — so notes stay consistent."""
    if not old or old == new:
        return
    with db.tx() as c:
        c.execute("UPDATE action_items SET owner = ? WHERE meeting_id = ? AND owner = ?", (new, meeting_id, old))
        for r in c.execute("SELECT meeting_id, summary FROM summaries WHERE meeting_id = ?", (meeting_id,)).fetchall():
            c.execute("UPDATE summaries SET summary = ? WHERE meeting_id = ?", (_replace_name(r["summary"], old, new), meeting_id))
        for r in c.execute("SELECT id, title, summary FROM topics WHERE meeting_id = ?", (meeting_id,)).fetchall():
            c.execute("UPDATE topics SET title = ?, summary = ? WHERE id = ?",
                      (_replace_name(r["title"], old, new), _replace_name(r["summary"], old, new), r["id"]))
        for r in c.execute("SELECT id, text FROM decisions WHERE meeting_id = ?", (meeting_id,)).fetchall():
            c.execute("UPDATE decisions SET text = ? WHERE id = ?", (_replace_name(r["text"], old, new), r["id"]))
        for r in c.execute("SELECT id, text FROM action_items WHERE meeting_id = ?", (meeting_id,)).fetchall():
            c.execute("UPDATE action_items SET text = ? WHERE id = ?", (_replace_name(r["text"], old, new), r["id"]))


def rename_speaker(db: Database, meeting_speaker_id: int, name: str, enroll: bool = True) -> MeetingSpeaker | None:
    """Give a diarized cluster a name. Segment IDs are untouched (the mapping lives on
    meeting_speakers), so timestamps/evidence stay valid. With `enroll`, the cluster's
    embedding is folded into the known-speaker profile so future meetings can suggest it.
    The previous name (or label) is replaced wherever it appears in the notes."""
    row = db.one("SELECT ms.*, sp.name AS known_name FROM meeting_speakers ms LEFT JOIN speakers sp ON sp.id = ms.speaker_id"
                 " WHERE ms.id = ?", (meeting_speaker_id,))
    if not row:
        return None
    old = row["display_name"] or row["known_name"] or row["label"]
    name = name.strip()
    if not name:
        db.execute("UPDATE meeting_speakers SET display_name = NULL, speaker_id = NULL, name_source = NULL WHERE id = ?",
                   (meeting_speaker_id,))
        propagate_name(db, row["meeting_id"], old, row["label"])
    else:
        speaker_id = None
        if enroll:
            emb = json.loads(row["embedding"]) if row["embedding"] else None
            speaker_id = _upsert_known_speaker(db, name, emb, row["embedding_model"] if "embedding_model" in row.keys() else None)  # noqa: SIM118 (sqlite3.Row)
        db.execute("UPDATE meeting_speakers SET display_name = ?, speaker_id = ?, suggested_speaker_id = NULL,"
                   " suggested_confidence = NULL, name_source = 'user' WHERE id = ?", (name, speaker_id, meeting_speaker_id))
        propagate_name(db, row["meeting_id"], old, name)
    return next((s for s in speakers(db, row["meeting_id"]) if s.id == meeting_speaker_id), None)


def confirm_suggestion(db: Database, meeting_speaker_id: int) -> MeetingSpeaker | None:
    row = db.one("SELECT ms.*, sp.name FROM meeting_speakers ms JOIN speakers sp ON sp.id = ms.suggested_speaker_id"
                 " WHERE ms.id = ?", (meeting_speaker_id,))
    if not row:
        return None
    out = rename_speaker(db, meeting_speaker_id, row["name"], enroll=True)
    db.execute("UPDATE meeting_speakers SET name_source = 'recognized' WHERE id = ?", (meeting_speaker_id,))
    return out


def merge_speakers(db: Database, source_id: int, target_id: int) -> None:
    """Fold cluster `source` into `target` (diarization over-split)."""
    db.execute("UPDATE transcript_segments SET meeting_speaker_id = ? WHERE meeting_speaker_id = ?", (target_id, source_id))
    db.execute("DELETE FROM meeting_speakers WHERE id = ?", (source_id,))


def update_segment(db: Database, segment_id: int, text: str | None, meeting_speaker_id: int | None) -> TranscriptSegment | None:
    sets, args = [], []
    if text is not None:
        sets.append("text = ?")
        args.append(text.strip())
    if meeting_speaker_id is not None:
        sets.append("meeting_speaker_id = ?")
        args.append(meeting_speaker_id)
    if sets:
        db.execute(f"UPDATE transcript_segments SET {', '.join(sets)} WHERE id = ?", (*args, segment_id))
    r = db.one(_SEG_SQL + " WHERE s.id = ?", (segment_id,))
    if not r:
        return None
    return TranscriptSegment(id=r["id"], meeting_id=r["meeting_id"], meeting_speaker_id=r["meeting_speaker_id"],
                             speaker_name=_name(r), idx=r["idx"], start=r["start"], end=r["end"], text=r["text"],
                             confidence=r["confidence"])


def known_speakers(db: Database) -> list[dict]:
    return [{"id": r["id"], "name": r["name"], "nSamples": r["n_samples"], "hasEmbedding": bool(r["embedding"]),
             "meetingCount": r["mc"], "updatedAt": r["updated_at"]}
            for r in db.query("SELECT sp.*, (SELECT COUNT(*) FROM meeting_speakers ms WHERE ms.speaker_id = sp.id) AS mc"
                              " FROM speakers sp ORDER BY sp.name")]


def delete_known_speaker(db: Database, speaker_id: int) -> None:
    db.execute("DELETE FROM speakers WHERE id = ?", (speaker_id,))


def delete_all_embeddings(db: Database) -> None:
    db.execute("UPDATE speakers SET embedding = NULL, n_samples = 0")
    db.execute("UPDATE meeting_speakers SET embedding = NULL, suggested_speaker_id = NULL, suggested_confidence = NULL")
