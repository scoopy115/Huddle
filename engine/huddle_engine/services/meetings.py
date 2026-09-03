from __future__ import annotations

import json
import re
import shutil
import time
import uuid
from datetime import datetime
from pathlib import Path

from ..audio import SUPPORTED_EXT
from ..db import Database
from ..schemas import (
    ActionItem,
    CreateFromRecordingRequest,
    Decision,
    Meeting,
    MeetingDetail,
    ProcessingJob,
    Recording,
    StageState,
    Summary,
    Topic,
)
from ..settings import EngineConfig
from . import transcripts


def _ts(value: str | float) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return time.time()


def default_title(started_at: float) -> str:
    return "Meeting " + datetime.fromtimestamp(started_at).strftime("%-d %b %Y, %H:%M")


_DEFAULT_TITLE = re.compile(r"^(Meeting \d{1,2} \w{3} \d{4}, \d{2}:\d{2}|Recovered recording|Untitled meeting)$")


def is_default_title(title: str | None) -> bool:
    """True for titles Huddle generated itself (date/time, recovery, empty) — the only ones the
    summary step may replace. Anything a person typed or an imported file name stays."""
    return not title or bool(_DEFAULT_TITLE.match(title.strip()))


def _row_meeting(r, extras: dict | None = None) -> Meeting:
    e = extras or {}
    keys = r.keys()
    return Meeting(id=r["id"], title=r["title"], created_at=r["created_at"], started_at=r["started_at"],
                   ended_at=r["ended_at"], duration_sec=r["duration_sec"], language=r["language"],
                   language_override=r["language_override"] if "language_override" in keys else None,
                   speaker_count_hint=r["speaker_count_hint"] if "speaker_count_hint" in keys else None,
                   context_html=r["context_html"] if "context_html" in keys else None,
                   status=r["status"], source=r["source"], notes=r["notes"],
                   job_state=e.get("job_state"), job_stage=e.get("job_stage"), job_progress=e.get("job_progress"),
                   job_error=e.get("job_error"),
                   speaker_count=e.get("speakers", 0), segment_count=e.get("segments", 0),
                   open_action_count=e.get("open_actions", 0), summary_preview=e.get("summary"),
                   participants=e.get("participants", []))


def _row_recording(r) -> Recording:
    return Recording(id=r["id"], meeting_id=r["meeting_id"], file_path=r["file_path"],
                     processed_path=r["processed_path"], system_file_path=r["system_file_path"], format=r["format"], sample_rate=r["sample_rate"],
                     channels=r["channels"], duration_sec=r["duration_sec"], size_bytes=r["size_bytes"],
                     input_device=r["input_device"], started_at=r["started_at"], status=r["status"])


def create_from_recording(db: Database, cfg: EngineConfig, req: CreateFromRecordingRequest) -> Meeting:
    started = _ts(req.started_at)
    now = time.time()
    path = Path(req.file_path)
    size = path.stat().st_size if path.exists() else None
    with db.tx() as c:
        c.execute("INSERT INTO meetings(id,title,created_at,started_at,ended_at,duration_sec,status,source,language_override,"
                  "speaker_count_hint) VALUES (?,?,?,?,?,?,?,?,?,?)",
                  (req.id, req.title or default_title(started), now, started, started + req.duration_sec,
                   req.duration_sec, "saved", req.source, (req.language or None) if req.language != "auto" else None,
                   req.speaker_count or None))
        c.execute("INSERT INTO recordings(id,meeting_id,file_path,system_file_path,format,sample_rate,channels,duration_sec,"
                  "size_bytes,input_device,started_at,status) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                  (f"rec-{req.id}", req.id, str(path), req.system_file_path, req.format, req.sample_rate, req.channels,
                   req.duration_sec, size, req.input_device, started, "saved"))
    return get_meeting(db, req.id)


def import_file(db: Database, cfg: EngineConfig, src: str, title: str | None) -> Meeting:
    p = Path(src).expanduser()
    if not p.exists():
        raise FileNotFoundError(f"File not found: {src}")
    if p.suffix.lower() not in SUPPORTED_EXT:
        raise ValueError(f"Unsupported audio format '{p.suffix}'. Supported: WAV, MP3, M4A, MP4, WebM, FLAC, OGG.")
    mid = datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
    started = p.stat().st_mtime
    (cfg.recordings_dir / mid).mkdir(parents=True, exist_ok=True)
    req = CreateFromRecordingRequest(id=mid, file_path=str(p), started_at=started, duration_sec=0.0,
                                     format=p.suffix.lstrip(".").lower(), title=title or p.stem, source="imported")
    return create_from_recording(db, cfg, req)


def _extras(db: Database, ids: list[str]) -> dict[str, dict]:
    if not ids:
        return {}
    q = ",".join("?" * len(ids))
    out = {i: {} for i in ids}
    for r in db.query(f"SELECT meeting_id, COUNT(*) n FROM transcript_segments WHERE meeting_id IN ({q}) GROUP BY meeting_id", ids):
        out[r["meeting_id"]]["segments"] = r["n"]
    for r in db.query(f"SELECT meeting_id, COUNT(*) n FROM meeting_speakers WHERE meeting_id IN ({q}) GROUP BY meeting_id", ids):
        out[r["meeting_id"]]["speakers"] = r["n"]
    for r in db.query(f"SELECT meeting_id, COUNT(*) n FROM action_items WHERE done = 0 AND meeting_id IN ({q}) GROUP BY meeting_id", ids):
        out[r["meeting_id"]]["open_actions"] = r["n"]
    for r in db.query(f"SELECT meeting_id, summary FROM summaries WHERE meeting_id IN ({q})", ids):
        out[r["meeting_id"]]["summary"] = (r["summary"] or "")[:200]
    for r in db.query(f"SELECT meeting_id, state, current_stage, stages_json FROM processing_jobs WHERE meeting_id IN ({q})", ids):
        stages = json.loads(r["stages_json"])
        e = out[r["meeting_id"]]
        e["job_state"] = r["state"]
        e["job_stage"] = r["current_stage"]
        if r["current_stage"]:
            e["job_progress"] = stages.get(r["current_stage"], {}).get("progress")
        failed = [n for n, st in stages.items() if st.get("status") == "failed"]
        if failed:
            e["job_stage"] = e["job_stage"] or failed[0]
            e["job_error"] = failed[0]
    # NB: the alias cannot be used in WHERE — SQLite would resolve `name` to speakers.name.
    for r in db.query(f"SELECT ms.meeting_id, COALESCE(ms.display_name, sp.name) AS name FROM meeting_speakers ms"
                      f" LEFT JOIN speakers sp ON sp.id = ms.speaker_id WHERE ms.meeting_id IN ({q})"
                      f" AND COALESCE(ms.display_name, sp.name) IS NOT NULL ORDER BY ms.id", ids):
        out[r["meeting_id"]].setdefault("participants", []).append(r["name"])
    return out


def list_meetings(db: Database, limit: int = 500, query: str | None = None) -> list[Meeting]:
    if query:
        rows = db.query("SELECT * FROM meetings WHERE title LIKE ? ORDER BY started_at DESC LIMIT ?",
                        (f"%{query}%", limit))
    else:
        rows = db.query("SELECT * FROM meetings ORDER BY started_at DESC LIMIT ?", (limit,))
    ex = _extras(db, [r["id"] for r in rows])
    return [_row_meeting(r, ex.get(r["id"])) for r in rows]


def get_meeting(db: Database, meeting_id: str) -> Meeting | None:
    r = db.one("SELECT * FROM meetings WHERE id = ?", (meeting_id,))
    if not r:
        return None
    return _row_meeting(r, _extras(db, [meeting_id]).get(meeting_id))


def get_recording(db: Database, meeting_id: str) -> Recording | None:
    r = db.one("SELECT * FROM recordings WHERE meeting_id = ? ORDER BY rowid LIMIT 1", (meeting_id,))
    return _row_recording(r) if r else None


def get_job(db: Database, meeting_id: str) -> ProcessingJob | None:
    r = db.one("SELECT * FROM processing_jobs WHERE meeting_id = ?", (meeting_id,))
    if not r:
        return None
    stages = {k: StageState(**v) for k, v in json.loads(r["stages_json"]).items()}
    return ProcessingJob(meeting_id=r["meeting_id"], state=r["state"], current_stage=r["current_stage"],
                         stages=stages, error=r["error"], error_detail=r["error_detail"],
                         created_at=r["created_at"], updated_at=r["updated_at"])


def get_summary(db: Database, meeting_id: str) -> Summary | None:
    r = db.one("SELECT * FROM summaries WHERE meeting_id = ?", (meeting_id,))
    return Summary(meeting_id=r["meeting_id"], summary=r["summary"], provider=r["provider"], model=r["model"],
                   created_at=r["created_at"]) if r else None


def get_topics(db: Database, meeting_id: str) -> list[Topic]:
    return [Topic(id=r["id"], meeting_id=r["meeting_id"], position=r["position"], title=r["title"], summary=r["summary"])
            for r in db.query("SELECT * FROM topics WHERE meeting_id = ? ORDER BY position", (meeting_id,))]


def get_decisions(db: Database, meeting_id: str) -> list[Decision]:
    return [Decision(id=r["id"], meeting_id=r["meeting_id"], position=r["position"], text=r["text"],
                     evidence_start=r["evidence_start"], evidence_end=r["evidence_end"], segment_id=r["segment_id"])
            for r in db.query("SELECT * FROM decisions WHERE meeting_id = ? ORDER BY position", (meeting_id,))]


def get_action_items(db: Database, meeting_id: str) -> list[ActionItem]:
    return [ActionItem(id=r["id"], meeting_id=r["meeting_id"], position=r["position"], text=r["text"], owner=r["owner"],
                       due_date=r["due_date"], confidence=r["confidence"], evidence_start=r["evidence_start"],
                       evidence_end=r["evidence_end"], segment_id=r["segment_id"], done=bool(r["done"]), source=r["source"])
            for r in db.query("SELECT * FROM action_items WHERE meeting_id = ? ORDER BY done, position, id", (meeting_id,))]


def get_detail(db: Database, meeting_id: str) -> MeetingDetail | None:
    m = get_meeting(db, meeting_id)
    if not m:
        return None
    return MeetingDetail(meeting=m, recording=get_recording(db, meeting_id), speakers=transcripts.speakers(db, meeting_id),
                         segments=transcripts.segments(db, meeting_id), summary=get_summary(db, meeting_id),
                         topics=get_topics(db, meeting_id), decisions=get_decisions(db, meeting_id),
                         action_items=get_action_items(db, meeting_id), job=get_job(db, meeting_id))


def update_meeting(db: Database, meeting_id: str, title: str | None = None, notes: str | None = None,
                   language_override: str | None = None, speaker_count_hint: int | None = None,
                   context_html: str | None = None) -> Meeting | None:
    sets, args = [], []
    if context_html is not None:
        sets.append("context_html = ?")
        args.append(context_html.strip() or None)
    if speaker_count_hint is not None:
        sets.append("speaker_count_hint = ?")
        args.append(int(speaker_count_hint) if 0 < int(speaker_count_hint) <= 20 else None)
    if title is not None:
        sets.append("title = ?")
        args.append(title.strip() or "Untitled meeting")
    if notes is not None:
        sets.append("notes = ?")
        args.append(notes)
    if language_override is not None:
        sets.append("language_override = ?")
        args.append(language_override.strip() or None)
    if sets:
        db.execute(f"UPDATE meetings SET {', '.join(sets)} WHERE id = ?", (*args, meeting_id))
    return get_meeting(db, meeting_id)


def delete_meeting(db: Database, cfg: EngineConfig, meeting_id: str) -> None:
    rec = get_recording(db, meeting_id)
    db.execute("DELETE FROM meetings WHERE id = ?", (meeting_id,))
    d = cfg.recordings_dir / meeting_id
    if d.exists() and d.resolve().is_relative_to(cfg.recordings_dir.resolve()):
        shutil.rmtree(d, ignore_errors=True)
    # Imported originals outside our data dir are never touched.
    _ = rec


def delete_audio(db: Database, cfg: EngineConfig, meeting_id: str) -> int:
    """Remove audio files but keep transcript and notes. Returns bytes freed."""
    freed = 0
    d = cfg.recordings_dir / meeting_id
    if d.exists():
        for p in d.glob("*.wav"):
            freed += p.stat().st_size
            p.unlink()
    db.execute("UPDATE recordings SET status = 'audio_deleted', processed_path = NULL WHERE meeting_id = ?", (meeting_id,))
    return freed


def recordings_bytes(cfg: EngineConfig) -> int:
    total = 0
    if cfg.recordings_dir.exists():
        for p in cfg.recordings_dir.rglob("*"):
            try:
                if p.is_file():
                    total += p.stat().st_size
            except OSError:
                pass
    return total


def enforce_storage_limit(db: Database, cfg: EngineConfig, max_bytes: int) -> list[str]:
    """Keep recordings under the quota by deleting the audio of the OLDEST processed
    meetings first. Transcripts, notes and action items are never touched."""
    if max_bytes <= 0:
        return []
    removed: list[str] = []
    total = recordings_bytes(cfg)
    if total <= max_bytes:
        return removed
    rows = db.query("SELECT m.id FROM meetings m JOIN recordings r ON r.meeting_id = m.id"
                    " WHERE r.status != 'audio_deleted' AND m.status IN ('ready','failed') ORDER BY m.started_at ASC")
    for r in rows:
        if total <= max_bytes:
            break
        freed = delete_audio(db, cfg, r["id"])
        total -= freed
        removed.append(r["id"])
    return removed


def apply_retention(db: Database, cfg: EngineConfig, days: int) -> int:
    """Delete audio older than `days` (transcripts kept). Returns count of meetings affected."""
    if days <= 0:
        return 0
    cutoff = time.time() - days * 86400
    rows = db.query("SELECT m.id FROM meetings m JOIN recordings r ON r.meeting_id = m.id"
                    " WHERE m.started_at < ? AND r.status != 'audio_deleted' AND m.status IN ('ready','failed')", (cutoff,))
    for r in rows:
        delete_audio(db, cfg, r["id"])
    return len(rows)


def delete_all_data(db: Database, cfg: EngineConfig) -> None:
    ids = [r["id"] for r in db.query("SELECT id FROM meetings")]
    for mid in ids:
        delete_meeting(db, cfg, mid)


def audio_path(db: Database, cfg: EngineConfig, meeting_id: str) -> str | None:
    rec = get_recording(db, meeting_id)
    if not rec or rec.status == "audio_deleted":
        return None
    p = Path(rec.file_path)
    try:
        inside = p.resolve().is_relative_to(cfg.data_dir.resolve())
    except OSError:
        inside = False
    if inside and p.exists() and p.suffix.lower() == ".wav":
        # Recorded with system audio: play the mix of both streams. Older recordings get the
        # mix generated on first playback.
        mix = p.with_name("mix.wav")
        if mix.exists():
            return str(mix)
        if rec.system_file_path and Path(rec.system_file_path).exists():
            try:
                from ..audio import write_playback_mix
                if write_playback_mix(p, Path(rec.system_file_path), mix):
                    return str(mix)
            except Exception:
                pass
        return str(p)
    if rec.processed_path and Path(rec.processed_path).exists():
        return rec.processed_path
    return str(p) if p.exists() else None
