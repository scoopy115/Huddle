"""MCP server exposing local meeting knowledge (spec §45–46).

Runs over stdio (``huddle-engine mcp``) so MCP clients (Claude Desktop, Cursor, …)
launch it as a subprocess; nothing is bound to the network. It performs no
transcription — it only reads the meeting database through the shared services,
and encourages targeted retrieval (search → context) over bulk dumps.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any

try:  # mcp >= 2
    from mcp.server.mcpserver import MCPServer as FastMCP
except ImportError:  # mcp 1.x
    from mcp.server.fastmcp import FastMCP

from .context import EngineContext
from .services import action_items as ai_svc
from .services import meetings as ms
from .services import search as search_svc
from .services import transcripts
from .settings import EngineConfig


def _fmt(sec: float | None) -> str:
    if sec is None:
        return ""
    m, s = divmod(int(sec), 60)
    h, m = divmod(m, 60)
    return f"{h:d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def _date(ts: float | None) -> str | None:
    return datetime.fromtimestamp(ts).isoformat(timespec="minutes") if ts else None


def build_server(cfg: EngineConfig | None = None) -> FastMCP:
    ctx = EngineContext(cfg, start_jobs=False)
    mcp = FastMCP("Huddle", instructions=(
        "Local, private meeting memory. Use search_transcripts/search_meetings to find relevant moments, then "
        "get_transcript_context for surrounding lines. Every hit carries meetingId, segmentId and timestamps so "
        "answers can cite evidence. Avoid fetching whole transcripts unless the user asks for one."))

    def _meeting_brief(m) -> dict[str, Any]:
        return {"meetingId": m.id, "title": m.title, "date": _date(m.started_at),
                "durationMin": round((m.duration_sec or 0) / 60, 1), "status": m.status, "language": m.language,
                "participants": m.participants, "openActionItems": m.open_action_count,
                "summaryPreview": m.summary_preview}

    @mcp.tool()
    def list_meetings(limit: int = 30, query: str | None = None) -> list[dict]:
        """List recent meetings (newest first). Optional title filter."""
        return [_meeting_brief(m) for m in ms.list_meetings(ctx.db, limit=limit, query=query)]

    @mcp.tool()
    def get_meeting(meeting_id: str) -> dict:
        """Meeting header, participants, summary, topics, decisions and action items (no transcript)."""
        d = ms.get_detail(ctx.db, meeting_id)
        if not d:
            return {"error": "meeting not found"}
        return {
            **_meeting_brief(d.meeting),
            "speakers": [{"id": s.id, "name": s.display_name or s.speaker_name or s.label,
                          "talkTimeSec": round(s.talk_time_sec)} for s in d.speakers],
            "summary": d.summary.summary if d.summary else None,
            "topics": [{"title": t.title, "summary": t.summary} for t in d.topics],
            "decisions": [{"text": x.text, "timestamp": _fmt(x.evidence_start), "segmentId": x.segment_id,
                           "evidenceStart": x.evidence_start, "evidenceEnd": x.evidence_end} for x in d.decisions],
            "actionItems": [{"id": a.id, "text": a.text, "owner": a.owner, "dueDate": a.due_date, "done": a.done,
                             "timestamp": _fmt(a.evidence_start), "segmentId": a.segment_id} for a in d.action_items],
        }

    @mcp.tool()
    def get_summary(meeting_id: str) -> dict:
        """Narrative summary of one meeting."""
        s = ms.get_summary(ctx.db, meeting_id)
        return {"meetingId": meeting_id, "summary": s.summary if s else None, "provider": s.provider if s else None}

    @mcp.tool()
    def get_topics(meeting_id: str) -> list[dict]:
        """Topics discussed in a meeting."""
        return [{"title": t.title, "summary": t.summary} for t in ms.get_topics(ctx.db, meeting_id)]

    @mcp.tool()
    def get_decisions(meeting_id: str) -> list[dict]:
        """Decisions made in a meeting, with evidence timestamps."""
        return [{"text": x.text, "timestamp": _fmt(x.evidence_start), "meetingId": meeting_id, "segmentId": x.segment_id,
                 "evidenceStart": x.evidence_start, "evidenceEnd": x.evidence_end} for x in ms.get_decisions(ctx.db, meeting_id)]

    @mcp.tool()
    def get_action_items(meeting_id: str) -> list[dict]:
        """Action items of one meeting (owner/dueDate are null when not stated in the meeting)."""
        return [{"id": a.id, "text": a.text, "owner": a.owner, "dueDate": a.due_date, "done": a.done,
                 "confidence": a.confidence, "timestamp": _fmt(a.evidence_start), "meetingId": meeting_id,
                 "segmentId": a.segment_id} for a in ms.get_action_items(ctx.db, meeting_id)]

    @mcp.tool()
    def get_open_action_items(owner: str | None = None, limit: int = 100) -> list[dict]:
        """Open (not done) action items across all meetings, optionally filtered by owner name."""
        return [{"id": a.id, "text": a.text, "owner": a.owner, "dueDate": a.due_date, "meetingId": a.meeting_id,
                 "meetingTitle": a.meeting_title, "meetingDate": _date(a.meeting_started_at),
                 "timestamp": _fmt(a.evidence_start), "segmentId": a.segment_id}
                for a in ai_svc.list_all(ctx.db, open_only=True, owner=owner, limit=limit)]

    @mcp.tool()
    def get_transcript(meeting_id: str, start_sec: float | None = None, end_sec: float | None = None,
                       max_segments: int = 400) -> list[dict]:
        """Speaker-labelled transcript of a meeting. Prefer a time window (start_sec/end_sec) over the whole thing."""
        segs = transcripts.segments(ctx.db, meeting_id)
        if start_sec is not None:
            segs = [s for s in segs if s.end >= start_sec]
        if end_sec is not None:
            segs = [s for s in segs if s.start <= end_sec]
        return [{"segmentId": s.id, "timestamp": _fmt(s.start), "start": s.start, "end": s.end,
                 "speaker": s.speaker_name, "text": s.text} for s in segs[:max_segments]]

    @mcp.tool()
    def get_transcript_context(segment_id: int, before: int = 4, after: int = 4) -> list[dict]:
        """Transcript lines around a segment (use after search_transcripts to read the surrounding conversation)."""
        return [{"segmentId": s.id, "meetingId": s.meeting_id, "timestamp": _fmt(s.start), "start": s.start, "end": s.end,
                 "speaker": s.speaker_name, "text": s.text}
                for s in transcripts.segment_window(ctx.db, segment_id, before=before, after=after)]

    @mcp.tool()
    def search_transcripts(query: str, limit: int = 20, meeting_id: str | None = None) -> list[dict]:
        """Full-text search across all transcripts (or one meeting). Returns cite-able hits."""
        return [{"meetingId": h.meeting_id, "meetingTitle": h.meeting_title, "date": _date(h.meeting_started_at),
                 "speaker": h.speaker_name, "timestamp": _fmt(h.start), "start": h.start, "end": h.end,
                 "segmentId": h.segment_id, "text": h.text, "snippet": h.snippet}
                for h in search_svc.search(ctx.db, query, limit=limit, meeting_id=meeting_id)]

    @mcp.tool()
    def search_meetings(query: str, limit: int = 10) -> list[dict]:
        """Meetings ranked by how much they discuss the query."""
        out = search_svc.search_meetings(ctx.db, query, limit=limit)
        for c in out:
            c["date"] = _date(c.pop("startedAt"))
        return out

    @mcp.tool()
    def search_semantic(query: str, limit: int = 20) -> dict:
        """Semantic (embedding) search. Not yet available in this version — falls back to full-text search."""
        return {"note": "Semantic search is not available yet; results are full-text matches.",
                "hits": json.loads(json.dumps(search_transcripts(query, limit)))}

    return mcp


def main(cfg: EngineConfig | None = None) -> None:
    build_server(cfg).run(transport="stdio")
