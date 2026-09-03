"""HTTP API auth + MCP retrieval over the shared services."""
import json
import os

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("HUDDLE_DATA_DIR", str(tmp_path / "api"))
    monkeypatch.setenv("HUDDLE_TOKEN", "secret")
    import importlib

    import huddle_engine.app as appmod
    importlib.reload(appmod)
    # don't hit the network / run a full scan in tests
    monkeypatch.setattr("huddle_engine.discovery.registry.Registry.scan_async", lambda self: None)
    monkeypatch.setattr("huddle_engine.discovery.ollama._api_models", lambda: None)
    monkeypatch.setattr("huddle_engine.discovery.ollama.installed", lambda: False)
    monkeypatch.setattr("huddle_engine.discovery.lmstudio.installed", lambda: False)
    with TestClient(appmod.app) as c:
        yield c


def test_auth_required(client):
    assert client.get("/health").status_code == 401
    r = client.get("/health", headers={"Authorization": "Bearer secret"})
    assert r.status_code == 200 and r.json()["ok"]


def test_settings_roundtrip_and_plan(client):
    h = {"Authorization": "Bearer secret"}
    s = client.get("/settings", headers=h).json()
    assert s["general.language"] == "auto" and s["onboarding.completed"] is False
    s = client.put("/settings", json={"general.language": "nl", "onboarding.completed": True}, headers=h).json()
    assert s["general.language"] == "nl" and s["onboarding.completed"] is True
    plan = client.get("/setup/plan", headers=h).json()
    assert {r["task"] for r in plan["resolutions"]} == {"transcription", "diarization", "llm"}
    assert plan["additionalBytes"] >= 0
    env = client.get("/system/environment", headers=h).json()
    assert env["devices"] and env["providers"]


def test_mcp_tools_share_services(tmp_path, monkeypatch):
    monkeypatch.setenv("HUDDLE_DATA_DIR", str(tmp_path / "mcp"))
    from huddle_engine.context import EngineContext
    from huddle_engine.mcp_server import build_server
    from huddle_engine.settings import EngineConfig
    cfg = EngineConfig(data_dir=tmp_path / "mcp")
    ctx = EngineContext(cfg, start_jobs=False)
    with ctx.db.tx() as c:
        c.execute("INSERT INTO meetings(id,title,created_at,started_at,duration_sec,status,source) VALUES ('m1','Branding',1,1,600,'ready','recorded')")
        sp = c.execute("INSERT INTO meeting_speakers(meeting_id,label,display_name) VALUES ('m1','Speaker 1','Alex')").lastrowid
        for i, t in enumerate(["We kiezen een warme accentkleur.", "Daan levert donderdag de copy.", "Analytics later."]):
            c.execute("INSERT INTO transcript_segments(meeting_id,meeting_speaker_id,idx,start,\"end\",text) VALUES ('m1',?,?,?,?,?)",
                      (sp, i, i * 10.0, i * 10.0 + 8, t))
        c.execute("INSERT INTO decisions(meeting_id,position,text,evidence_start,evidence_end) VALUES ('m1',0,'Warme accentkleur.',0,8)")
        c.execute("INSERT INTO action_items(meeting_id,position,text,owner,due_date,done,source,created_at) VALUES ('m1',0,'Copy aanleveren','Daan',NULL,0,'auto',1)")
    ctx.close()

    server = build_server(cfg)
    import asyncio
    tools = asyncio.run(server.list_tools())
    names = {t.name for t in tools}
    for n in ("list_meetings", "get_meeting", "get_transcript", "get_transcript_context", "search_transcripts",
              "search_meetings", "get_summary", "get_topics", "get_decisions", "get_action_items",
              "get_open_action_items", "search_semantic"):
        assert n in names, n

    async def call(name, **args):
        res = await server.call_tool(name, args)
        # mcp 2.x: CallToolResult with structured_content; mcp 1.x: list of content blocks / tuple
        sc = getattr(res, "structured_content", None)
        if sc is not None:
            return sc["result"] if isinstance(sc, dict) and set(sc) == {"result"} else sc
        content = getattr(res, "content", None)
        if content is None:
            content = res[0] if isinstance(res, tuple) else res
        texts = [c.text for c in content if getattr(c, "type", "") == "text"]
        return json.loads(texts[0]) if len(texts) == 1 else [json.loads(t) for t in texts]

    meetings = asyncio.run(call("list_meetings"))
    assert meetings[0]["meetingId"] == "m1" and meetings[0]["openActionItems"] == 1
    hits = asyncio.run(call("search_transcripts", query="accentkleur"))
    hit = hits[0] if isinstance(hits, list) else hits
    assert hit["meetingId"] == "m1" and hit["segmentId"] and hit["timestamp"] == "00:00" and hit["speaker"] == "Alex"
    ctxs = asyncio.run(call("get_transcript_context", segment_id=hit["segmentId"], before=0, after=1))
    ctxs = ctxs if isinstance(ctxs, list) else [ctxs]
    assert len(ctxs) == 2 and ctxs[1]["text"].startswith("Daan")
    opens = asyncio.run(call("get_open_action_items"))
    opens = opens if isinstance(opens, list) else [opens]
    assert opens[0]["owner"] == "Daan" and opens[0]["dueDate"] is None and opens[0]["meetingTitle"] == "Branding"
    dec = asyncio.run(call("get_decisions", meeting_id="m1"))
    dec = dec if isinstance(dec, list) else [dec]
    assert dec[0]["evidenceStart"] == 0 and dec[0]["timestamp"] == "00:00"
    assert os.environ["HUDDLE_DATA_DIR"].endswith("mcp")
