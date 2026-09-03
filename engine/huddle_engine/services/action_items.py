from __future__ import annotations

import time

from ..db import Database
from ..schemas import ActionItem


def _row(r) -> ActionItem:
    return ActionItem(id=r["id"], meeting_id=r["meeting_id"], position=r["position"], text=r["text"], owner=r["owner"],
                      due_date=r["due_date"], confidence=r["confidence"], evidence_start=r["evidence_start"],
                      evidence_end=r["evidence_end"], segment_id=r["segment_id"], done=bool(r["done"]), source=r["source"],
                      meeting_title=r["meeting_title"] if "meeting_title" in r.keys() else None,  # noqa: SIM118 (sqlite3.Row)
                      meeting_started_at=r["meeting_started_at"] if "meeting_started_at" in r.keys() else None)  # noqa: SIM118 (sqlite3.Row)


def list_all(db: Database, open_only: bool = False, owner: str | None = None, limit: int = 500) -> list[ActionItem]:
    q = ("SELECT a.*, m.title AS meeting_title, m.started_at AS meeting_started_at FROM action_items a"
         " JOIN meetings m ON m.id = a.meeting_id")
    conds, args = [], []
    if open_only:
        conds.append("a.done = 0")
    if owner:
        conds.append("LOWER(a.owner) = LOWER(?)")
        args.append(owner)
    if conds:
        q += " WHERE " + " AND ".join(conds)
    q += " ORDER BY a.done ASC, m.started_at DESC, a.position LIMIT ?"
    args.append(limit)
    return [_row(r) for r in db.query(q, args)]


def get(db: Database, item_id: int) -> ActionItem | None:
    r = db.one("SELECT a.*, m.title AS meeting_title, m.started_at AS meeting_started_at FROM action_items a"
               " JOIN meetings m ON m.id = a.meeting_id WHERE a.id = ?", (item_id,))
    return _row(r) if r else None


def update(db: Database, item_id: int, text: str | None = None, owner: str | None = None,
           due_date: str | None = None, done: bool | None = None, clear_owner: bool = False,
           clear_due: bool = False) -> ActionItem | None:
    sets, args = [], []
    if text is not None:
        sets.append("text = ?")
        args.append(text.strip())
    if owner is not None or clear_owner:
        sets.append("owner = ?")
        args.append((owner or "").strip() or None)
    if due_date is not None or clear_due:
        sets.append("due_date = ?")
        args.append((due_date or "").strip() or None)
    if done is not None:
        sets.append("done = ?")
        args.append(int(done))
    if sets:
        db.execute(f"UPDATE action_items SET {', '.join(sets)} WHERE id = ?", (*args, item_id))
    return get(db, item_id)


def create(db: Database, meeting_id: str, text: str, owner: str | None, due_date: str | None) -> ActionItem:
    pos = db.one("SELECT COALESCE(MAX(position), -1) + 1 AS p FROM action_items WHERE meeting_id = ?", (meeting_id,))["p"]
    cur = db.execute("INSERT INTO action_items(meeting_id, position, text, owner, due_date, confidence, done, source, created_at)"
                     " VALUES (?,?,?,?,?,?,?,?,?)",
                     (meeting_id, pos, text.strip(), (owner or "").strip() or None, (due_date or "").strip() or None,
                      1.0, 0, "manual", time.time()))
    return get(db, int(cur.lastrowid))


def delete(db: Database, item_id: int) -> None:
    db.execute("DELETE FROM action_items WHERE id = ?", (item_id,))
