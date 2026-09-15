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
from .services import projects as projects_svc
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
        "Local, private meeting memory. Meetings can be filed in projects (folders): list_projects, then "
        "get_project for everything about one project (its meetings, decisions, open action items), or pass "
        "project_id to list_meetings / search_transcripts / search_meetings / get_open_action_items to stay inside "
        "it. Use search_transcripts/search_meetings to find relevant moments, then get_transcript_context for "
        "surrounding lines. Every hit carries meetingId, segmentId and timestamps so answers can cite evidence. "
        "Avoid fetching whole transcripts unless the user asks for one."))

    def _meeting_brief(m) -> dict[str, Any]:
        return {"meetingId": m.id, "title": m.title, "date": _date(m.started_at),
                "durationMin": round((m.duration_sec or 0) / 60, 1), "status": m.status, "language": m.language,
                "participants": m.participants, "openActionItems": m.open_action_count,
                "summaryPreview": m.summary_preview,
                "projectId": m.project_id, "projectName": m.project_name}

    def _project_brief(p) -> dict[str, Any]:
        return {"projectId": p.id, "name": p.name, "description": p.description, "meetingCount": p.meeting_count,
                "openActionItems": p.open_action_count, "lastMeeting": _date(p.last_meeting_at)}

    @mcp.tool()
    def list_meetings(limit: int = 30, query: str | None = None, project_id: str | None = None) -> list[dict]:
        """List recent meetings (newest first). Optional title filter; optional project_id to list one project's meetings."""
        return [_meeting_brief(m) for m in ms.list_meetings(ctx.db, limit=limit, query=query, project_id=project_id)]

    @mcp.tool()
    def list_projects() -> list[dict]:
        """Projects (folders of meetings) with meeting counts and open action items."""
        return [_project_brief(p) for p in projects_svc.list_projects(ctx.db)]

    @mcp.tool()
    def get_project(project_id: str, max_meetings: int = 50) -> dict:
        """Everything about one project: its meetings (newest first, with summaries), every decision and
        the open action items across them. Use search_transcripts with project_id for the details."""
        p = projects_svc.get_project(ctx.db, project_id)
        if not p:
            by_name = projects_svc.find_by_name(ctx.db, project_id)
            if not by_name:
                return {"error": "project not found"}
            p = by_name
        meetings = ms.list_meetings(ctx.db, limit=max_meetings, project_id=p.id)
        decisions = []
        for m in meetings:
            for x in ms.get_decisions(ctx.db, m.id):
                decisions.append({"meetingId": m.id, "meetingTitle": m.title, "date": _date(m.started_at), "text": x.text,
                                  "timestamp": _fmt(x.evidence_start), "segmentId": x.segment_id})
        summaries = {r["meeting_id"]: r["summary"] for r in ctx.db.query(
            "SELECT s.meeting_id, s.summary FROM summaries s JOIN meetings m ON m.id = s.meeting_id WHERE m.project_id = ?", (p.id,))}
        return {
            **_project_brief(p),
            "meetings": [{**_meeting_brief(m), "summary": summaries.get(m.id)} for m in meetings],
            "decisions": decisions,
            "openActionItems": [{"id": a.id, "text": a.text, "owner": a.owner, "dueDate": a.due_date, "meetingId": a.meeting_id,
                                 "meetingTitle": a.meeting_title, "meetingDate": _date(a.meeting_started_at),
                                 "timestamp": _fmt(a.evidence_start), "segmentId": a.segment_id}
                                for a in ai_svc.list_all(ctx.db, open_only=True, project_id=p.id, limit=200)],
        }

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
    def get_open_action_items(owner: str | None = None, limit: int = 100, project_id: str | None = None) -> list[dict]:
        """Open (not done) action items across all meetings (or one project), optionally filtered by owner name."""
        return [{"id": a.id, "text": a.text, "owner": a.owner, "dueDate": a.due_date, "meetingId": a.meeting_id,
                 "meetingTitle": a.meeting_title, "meetingDate": _date(a.meeting_started_at),
                 "timestamp": _fmt(a.evidence_start), "segmentId": a.segment_id}
                for a in ai_svc.list_all(ctx.db, open_only=True, owner=owner, limit=limit, project_id=project_id)]

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
    def search_transcripts(query: str, limit: int = 20, meeting_id: str | None = None,
                           project_id: str | None = None) -> list[dict]:
        """Full-text search across all transcripts (or one meeting / one project). Returns cite-able hits."""
        return [{"meetingId": h.meeting_id, "meetingTitle": h.meeting_title, "date": _date(h.meeting_started_at),
                 "speaker": h.speaker_name, "timestamp": _fmt(h.start), "start": h.start, "end": h.end,
                 "segmentId": h.segment_id, "text": h.text, "snippet": h.snippet}
                for h in search_svc.search(ctx.db, query, limit=limit, meeting_id=meeting_id, project_id=project_id)]

    @mcp.tool()
    def search_meetings(query: str, limit: int = 10, project_id: str | None = None) -> list[dict]:
        """Meetings ranked by how much they discuss the query (optionally within one project)."""
        out = search_svc.search_meetings(ctx.db, query, limit=limit, project_id=project_id)
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
