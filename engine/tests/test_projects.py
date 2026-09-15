"""Projects: folders of meetings, project suggestions, scoped search/ask/MCP."""
import json

from fastapi.testclient import TestClient

from huddle_engine.providers.llm import ExtractiveProvider
from huddle_engine.schemas import CreateProjectRequest
from huddle_engine.services import action_items as ai
from huddle_engine.services import meetings as ms
from huddle_engine.services import projects, search


def _meeting(db, mid, title, summary=None, transcript=(), topics=(), started=1_000.0):
    with db.tx() as c:
        c.execute("INSERT INTO meetings(id,title,created_at,started_at,duration_sec,status,source) VALUES (?,?,?,?,600,'ready','recorded')",
                  (mid, title, started, started))
        if summary:
            c.execute("INSERT INTO summaries(meeting_id,summary,created_at) VALUES (?,?,1)", (mid, summary))
        for i, t in enumerate(topics):
            c.execute("INSERT INTO topics(meeting_id,position,title) VALUES (?,?,?)", (mid, i, t))
        for i, t in enumerate(transcript):
            c.execute("INSERT INTO transcript_segments(meeting_id,idx,start,\"end\",text) VALUES (?,?,?,?,?)",
                      (mid, i, i * 10.0, i * 10.0 + 8, t))


def test_project_crud_and_membership(db):
    _meeting(db, "m1", "Lighthouse branding kickoff")
    _meeting(db, "m2", "Weekly design sync")
    p = projects.create(db, CreateProjectRequest(name="Lighthouse", description="Branding for Lighthouse", meeting_ids=["m1"]))
    assert p.meeting_count == 1 and p.name == "Lighthouse"
    assert ms.get_meeting(db, "m1").project_name == "Lighthouse"
    # names are unique, case-insensitively
    try:
        projects.create(db, CreateProjectRequest(name="lighthouse"))
        raise AssertionError("duplicate accepted")
    except ValueError:
        pass
    projects.assign(db, "m2", p.id)
    assert [m.id for m in ms.list_meetings(db, project_id=p.id)] == ["m2", "m1"] or len(ms.list_meetings(db, project_id=p.id)) == 2
    projects.assign(db, "m2", None)
    assert ms.get_meeting(db, "m2").project_id is None
    assert [m.id for m in ms.list_meetings(db, unassigned=True)] == ["m2"]
    p = projects.update(db, p.id, name="Lighthouse brand", description="")
    assert p.name == "Lighthouse brand" and p.description is None
    projects.delete(db, p.id)
    assert projects.list_projects(db) == []
    assert ms.get_meeting(db, "m1").project_id is None      # the meeting survives the folder


def test_lexical_suggestion_prefers_named_project(db):
    _meeting(db, "a1", "Lighthouse homepage", summary="Homepage copy and hero image for the Lighthouse website.",
             topics=["Hero image", "Copy deadline"])
    _meeting(db, "b1", "Bakery app sprint", summary="Ordering flow of the bakery app, push notifications.",
             topics=["Ordering flow"])
    web = projects.create(db, CreateProjectRequest(name="Lighthouse website", meeting_ids=["a1"]))
    projects.create(db, CreateProjectRequest(name="Bakery app", meeting_ids=["b1"]))
    _meeting(db, "new", "Meeting 5 Sep 2026, 10:00", summary="Lighthouse wants the homepage hero image swapped and the website copy shortened.",
             transcript=["Let's look at the Lighthouse website again.", "The homepage hero image is too dark."])
    ranked = projects.lexical_scores(db, "new", projects.list_projects(db))
    assert ranked[0][0].id == web.id and ranked[0][1] > ranked[1][1]
    # no AI model: the lexical score decides
    suggested = projects.suggest(db, "new", ExtractiveProvider())
    assert suggested and suggested.id == web.id
    m = ms.get_meeting(db, "new")
    assert m.suggested_project_id == web.id and m.suggested_project_name == "Lighthouse website"
    assert m.suggested_project_confidence >= projects.MIN_CONFIDENCE and m.project_id is None
    # the project lists it as a suggestion until somebody decides
    d = projects.detail(db, web.id)
    assert [x.id for x in d.suggested] == ["new"] and [x.id for x in d.meetings] == ["a1"]
    assert projects.get_project(db, web.id).suggestion_count == 1
    projects.dismiss_suggestion(db, "new")
    assert ms.get_meeting(db, "new").suggested_project_id is None
    # accepting clears the suggestion and files the meeting
    projects.suggest(db, "new", None)
    projects.assign(db, "new", web.id)
    m = ms.get_meeting(db, "new")
    assert m.project_id == web.id and m.suggested_project_id is None


def test_unrelated_meeting_gets_no_suggestion(db):
    _meeting(db, "a1", "Lighthouse homepage", summary="Homepage copy for the Lighthouse website.")
    projects.create(db, CreateProjectRequest(name="Lighthouse website", meeting_ids=["a1"]))
    _meeting(db, "new", "Lunch planning", summary="Where to have the team lunch on Friday.",
             transcript=["Pizza or sushi?", "Sushi, but not the place from last time."])
    assert projects.suggest(db, "new", None) is None
    assert ms.get_meeting(db, "new").suggested_project_id is None


class _FakeLLM:
    """Answers the project question with a fixed pick; records the prompt."""
    id = "ollama"
    model = "fake"

    def __init__(self, answer):
        self.answer = answer
        self.prompts = []

    def complete_json(self, system, user, max_tokens=2048):
        self.prompts.append((system, user))
        return json.dumps(self.answer)

    def complete(self, system, user, max_tokens=1024):
        raise AssertionError("not used")


def test_llm_makes_the_final_call(db):
    _meeting(db, "a1", "Lighthouse homepage", summary="Homepage copy for the Lighthouse website.")
    _meeting(db, "b1", "Bakery app sprint", summary="Ordering flow of the bakery app.")
    web = projects.create(db, CreateProjectRequest(name="Lighthouse website", meeting_ids=["a1"]))
    bakery = projects.create(db, CreateProjectRequest(name="Bakery app", meeting_ids=["b1"]))
    _meeting(db, "new", "Planning", summary="Sprint planning for the ordering flow and the checkout of the app.")
    llm = _FakeLLM({"projectId": bakery.id, "confidence": 0.9, "reason": "Same product: the ordering flow of the bakery app."})
    assert projects.suggest(db, "new", llm, language="Dutch").id == bakery.id
    system, user = llm.prompts[0]
    assert "Dutch" in system and bakery.id in user and web.id in user and "checkout" in user
    m = ms.get_meeting(db, "new")
    assert m.suggested_project_confidence == 0.9 and "ordering flow" in m.suggested_project_reason
    # the model may decline
    _meeting(db, "other", "Other", summary="Nothing to do with either.")
    assert projects.suggest(db, "other", _FakeLLM({"projectId": None, "confidence": 0.2, "reason": "none"})) is None
    # a low-confidence pick is not shown
    assert projects.suggest(db, "other", _FakeLLM({"projectId": web.id, "confidence": 0.3, "reason": "maybe"})) is None
    # a broken answer falls back to the text match rather than failing the stage
    _meeting(db, "broken", "Lighthouse website copy", summary="Lighthouse website homepage copy.")

    class _Broken(_FakeLLM):
        def complete_json(self, system, user, max_tokens=2048):
            return "not json at all"
    assert projects.suggest(db, "broken", _Broken(None)).id == web.id


def test_scoped_search_and_action_items(db):
    _meeting(db, "a1", "Lighthouse homepage", transcript=["The hero image needs a warmer colour."])
    _meeting(db, "b1", "Bakery app", transcript=["The app icon needs a warmer colour too."])
    web = projects.create(db, CreateProjectRequest(name="Lighthouse", meeting_ids=["a1"]))
    ai.create(db, "a1", "Swap hero image", None, None)
    ai.create(db, "b1", "Redraw icon", None, None)
    hits = search.search(db, "warmer colour", project_id=web.id)
    assert [h.meeting_id for h in hits] == ["a1"]
    assert {h.meeting_id for h in search.search(db, "warmer colour")} == {"a1", "b1"}
    assert [c["meetingId"] for c in search.search_meetings(db, "warmer", project_id=web.id)] == ["a1"]
    assert [a.meeting_id for a in ai.list_all(db, open_only=True, project_id=web.id)] == ["a1"]


def test_project_api(tmp_path, monkeypatch):
    monkeypatch.setenv("HUDDLE_DATA_DIR", str(tmp_path / "api"))
    monkeypatch.setenv("HUDDLE_TOKEN", "secret")
    import importlib

    import huddle_engine.app as appmod
    importlib.reload(appmod)
    monkeypatch.setattr("huddle_engine.discovery.registry.Registry.scan_async", lambda self: None)
    monkeypatch.setattr("huddle_engine.discovery.ollama._api_models", lambda: None)
    monkeypatch.setattr("huddle_engine.discovery.ollama.installed", lambda: False)
    monkeypatch.setattr("huddle_engine.discovery.lmstudio.installed", lambda: False)
    h = {"Authorization": "Bearer secret"}
    with TestClient(appmod.app) as c:
        db = appmod.ctx().db
        _meeting(db, "m1", "Lighthouse homepage", summary="Homepage copy for the Lighthouse website.")
        _meeting(db, "m2", "Meeting 5 Sep 2026, 10:00", summary="Lighthouse website homepage hero image.")
        r = c.post("/projects", json={"name": "Lighthouse website", "meetingIds": ["m1"]}, headers=h)
        assert r.status_code == 200
        pid = r.json()["id"]
        assert c.post("/projects", json={"name": "lighthouse WEBSITE"}, headers=h).status_code == 400
        assert c.get("/meetings", params={"project_id": pid}, headers=h).json()[0]["id"] == "m1"
        m = c.post("/meetings/m2/project-suggestion", headers=h).json()
        assert m["suggestedProjectId"] == pid and m["suggestedProjectName"] == "Lighthouse website"
        d = c.get(f"/projects/{pid}", headers=h).json()
        assert [x["id"] for x in d["suggested"]] == ["m2"] and d["project"]["suggestionCount"] == 1
        m = c.delete("/meetings/m2/project-suggestion", headers=h).json()
        assert m["suggestedProjectId"] is None
        m = c.patch("/meetings/m2", json={"projectId": pid}, headers=h).json()
        assert m["projectId"] == pid and m["projectName"] == "Lighthouse website"
        assert c.patch("/meetings/m2", json={"projectId": "nope"}, headers=h).status_code == 404
        m = c.patch("/meetings/m2", json={"projectId": ""}, headers=h).json()
        assert m["projectId"] is None
        p = c.patch(f"/projects/{pid}", json={"name": "Lighthouse site", "description": "All website work"}, headers=h).json()
        assert p["name"] == "Lighthouse site" and p["description"] == "All website work"
        assert c.get("/projects", headers=h).json()[0]["meetingCount"] == 1
        assert c.delete(f"/projects/{pid}", headers=h).json()["ok"]
        assert c.get("/projects", headers=h).json() == []
        assert c.get("/meetings/m1", headers=h).json()["meeting"]["projectId"] is None


def test_mcp_project_tools(tmp_path, monkeypatch):
    monkeypatch.setenv("HUDDLE_DATA_DIR", str(tmp_path / "mcp"))
    import asyncio

    from huddle_engine.context import EngineContext
    from huddle_engine.mcp_server import build_server
    from huddle_engine.settings import EngineConfig
    cfg = EngineConfig(data_dir=tmp_path / "mcp")
    ctx = EngineContext(cfg, start_jobs=False)
    _meeting(ctx.db, "a1", "Lighthouse homepage", summary="Homepage copy.", transcript=["The hero image needs a warmer colour."])
    _meeting(ctx.db, "b1", "Bakery app", transcript=["The app icon needs a warmer colour too."])
    p = projects.create(ctx.db, CreateProjectRequest(name="Lighthouse", meeting_ids=["a1"]))
    with ctx.db.tx() as c:
        c.execute("INSERT INTO decisions(meeting_id,position,text,evidence_start,evidence_end) VALUES ('a1',0,'Warmer colour.',0,8)")
        c.execute("INSERT INTO action_items(meeting_id,position,text,owner,done,source,created_at) VALUES ('a1',0,'Swap image','Daan',0,'auto',1)")
    ctx.close()
    server = build_server(cfg)
    names = {t.name for t in asyncio.run(server.list_tools())}
    assert {"list_projects", "get_project"} <= names

    async def call(name, **args):
        res = await server.call_tool(name, args)
        sc = getattr(res, "structured_content", None)
        if sc is not None:
            return sc["result"] if isinstance(sc, dict) and set(sc) == {"result"} else sc
        content = getattr(res, "content", None)
        if content is None:
            content = res[0] if isinstance(res, tuple) else res
        texts = [c.text for c in content if getattr(c, "type", "") == "text"]
        return json.loads(texts[0]) if len(texts) == 1 else [json.loads(t) for t in texts]

    lst = asyncio.run(call("list_projects"))
    assert lst[0]["name"] == "Lighthouse" and lst[0]["meetingCount"] == 1 and lst[0]["openActionItems"] == 1
    d = asyncio.run(call("get_project", project_id=p.id))
    assert [m["meetingId"] for m in d["meetings"]] == ["a1"] and d["meetings"][0]["summary"] == "Homepage copy."
    assert d["decisions"][0]["text"] == "Warmer colour." and d["openActionItems"][0]["owner"] == "Daan"
    # by name works too
    assert asyncio.run(call("get_project", project_id="lighthouse"))["projectId"] == p.id
    hits = asyncio.run(call("search_transcripts", query="warmer colour", project_id=p.id))
    assert [h["meetingId"] for h in hits] == ["a1"]
    assert asyncio.run(call("list_meetings", project_id=p.id))[0]["projectName"] == "Lighthouse"
