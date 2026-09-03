"""Exports: Markdown (notes + transcript), TXT, JSON, SRT and VTT."""
from __future__ import annotations

import json
from datetime import datetime

from ..db import Database
from . import meetings as ms
from . import transcripts

MEDIA = {"md": "text/markdown", "txt": "text/plain", "json": "application/json", "srt": "text/plain", "vtt": "text/vtt"}


def _clock(sec: float | None) -> str:
    """m:ss / h:mm:ss for notes."""
    if sec is None:
        return ""
    m, s = divmod(int(sec), 60)
    h, m = divmod(m, 60)
    return f"{h:d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def _cue(sec: float, sep: str) -> str:
    """hh:mm:ss,mmm (SRT) or hh:mm:ss.mmm (VTT)."""
    ms_total = round(max(0.0, sec) * 1000)
    h, rem = divmod(ms_total, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, milli = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}{sep}{milli:03d}"


def to_srt(segments) -> str:
    out = []
    for i, s in enumerate(segments, 1):
        out += [str(i), f"{_cue(s.start, ',')} --> {_cue(s.end, ',')}", f"{s.speaker_name or 'Speaker'}: {s.text}", ""]
    return "\n".join(out)


def to_vtt(segments) -> str:
    out = ["WEBVTT", ""]
    for s in segments:
        out += [f"{_cue(s.start, '.')} --> {_cue(s.end, '.')}", f"<v {s.speaker_name or 'Speaker'}>{s.text}", ""]
    return "\n".join(out)


def to_txt(segments) -> str:
    return "\n".join(f"[{_clock(s.start)}] {s.speaker_name or 'Speaker'}: {s.text}" for s in segments) + "\n"


def export(db: Database, meeting_id: str, fmt: str) -> tuple[str, str]:
    fmt = fmt.lower()
    if fmt not in MEDIA:
        raise ValueError(f"Unknown export format '{fmt}'")
    detail = ms.get_detail(db, meeting_id)
    if not detail:
        raise KeyError(meeting_id)
    if fmt in ("srt", "vtt", "txt"):
        segs = transcripts.segments(db, meeting_id)
        body = {"srt": to_srt, "vtt": to_vtt, "txt": to_txt}[fmt](segs)
        return body, MEDIA[fmt]
    if fmt == "json":
        return json.dumps(detail.model_dump(by_alias=True, exclude={"job"}), indent=2, ensure_ascii=False), MEDIA["json"]
    return to_markdown(detail), MEDIA["md"]


def to_markdown(d) -> str:
    m = d.meeting
    when = datetime.fromtimestamp(m.started_at).strftime("%Y-%m-%d %H:%M")
    mins = (m.duration_sec or 0) / 60
    names = [s.display_name or s.speaker_name or s.label for s in d.speakers]
    lines = [f"# {m.title}", "", f"*{when} · {mins:.0f} min*", ""]
    if names:
        lines += ["**Participants:** " + ", ".join(names), ""]
    if d.summary and d.summary.summary:
        lines += ["## Summary", "", d.summary.summary, ""]
    if d.topics:
        lines += ["## Topics", ""] + [f"- **{t.title}**" + (f" — {t.summary}" if t.summary else "") for t in d.topics] + [""]
    if d.decisions:
        lines += ["## Decisions", ""]
        for x in d.decisions:
            ev = f" `{_clock(x.evidence_start)}`" if x.evidence_start is not None else ""
            lines.append(f"- {x.text}{ev}")
        lines.append("")
    if d.action_items:
        lines += ["## Action items", ""]
        for a in d.action_items:
            meta = " · ".join(x for x in [a.owner or "Unassigned", a.due_date] if x)
            box = "[x]" if a.done else "[ ]"
            ev = f" `{_clock(a.evidence_start)}`" if a.evidence_start is not None else ""
            lines.append(f"- {box} {a.text}  _({meta})_{ev}")
        lines.append("")
    lines += ["## Transcript", ""]
    for s in d.segments:
        lines.append(f"**{s.speaker_name or 'Speaker'}** · {_clock(s.start)}  ")
        lines.append(s.text)
        lines.append("")
    return "\n".join(lines)
