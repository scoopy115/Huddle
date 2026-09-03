"""FastAPI application — the typed localhost API the desktop app talks to.

Bound to 127.0.0.1 only. When ``HUDDLE_TOKEN`` is set every request must carry
``Authorization: Bearer <token>``; the desktop shell generates the token per launch.
"""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse

from . import __version__
from .context import EngineContext
from .mcp_network import NetworkMcpServer
from .providers.compute import compute_devices
from .resolver import (
    LLM_CANDIDATES,
    WHISPER_CANDIDATES,
    ResolverContext,
    additional_bytes,
    candidates_for,
    is_general_chat_model,
    is_recommended_size,
    resolve_all,
)
from .schemas import (
    AskRequest,
    ConfirmSuggestionRequest,
    CreateActionItemRequest,
    CreateFromRecordingRequest,
    DownloadCandidate,
    Environment,
    ImportRequest,
    McpStatus,
    MoveDirRequest,
    RefineRequest,
    RenameSpeakerRequest,
    SetupPlan,
    StorageInfo,
    UpdateActionItemRequest,
    UpdateMeetingRequest,
    UpdateSegmentRequest,
)
from .services import action_items as ai_svc
from .services import api_keys, transcripts
from .services import ask as ask_svc
from .services import exports as export_svc
from .services import meetings as ms
from .services import search as search_svc

log = logging.getLogger("huddle")

_ctx: EngineContext | None = None


def ctx() -> EngineContext:
    assert _ctx is not None, "engine not started"
    return _ctx


def _sync_mcp_network() -> None:
    c = ctx()
    s = c.settings()
    if c.mcp_network is None:
        from .mcp_server import build_server
        c.mcp_network = NetworkMcpServer(c.db, build_server, c.cfg)
    if s.get("mcp.networkEnabled"):
        try:
            c.mcp_network.start(int(s.get("mcp.port", 48800)))
        except Exception as e:
            log.exception("network MCP failed to start")
            c.mcp_network.error = str(e)
    else:
        c.mcp_network.stop()


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _ctx
    # HUDDLE_NO_JOBS=1: a second engine (browser dev mode) must not process jobs alongside the app's engine.
    _ctx = EngineContext(start_jobs=not os.getenv("HUDDLE_NO_JOBS"))
    try:
        _ctx.registry.quick_check()
    except Exception:
        log.exception("quick provider check failed")
    _ctx.registry.scan_async()
    try:
        days = int(_ctx.settings().get("privacy.retentionDays") or 0)
        if days > 0:
            n = ms.apply_retention(_ctx.db, _ctx.cfg, days)
            if n:
                log.info("retention: removed audio of %d meetings older than %d days", n, days)
        _ctx.enforce_storage()
    except Exception:
        log.exception("retention failed")
    try:
        _sync_mcp_network()
    except Exception:
        log.exception("mcp network init failed")
    log.info("engine ready · data dir %s · schema v%d", _ctx.cfg.data_dir, _ctx.db.schema_version)
    yield
    _ctx.close()


app = FastAPI(title="Huddle Engine", version=__version__, lifespan=lifespan)

# Development only: lets the React app run in a plain browser (npm run dev) against the
# engine. Never set in the packaged app — the desktop shell talks to the engine via Rust.
if os.getenv("HUDDLE_DEV_CORS"):
    from fastapi.middleware.cors import CORSMiddleware
    app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:1420", "http://127.0.0.1:1420"],
                       allow_methods=["*"], allow_headers=["*"])


@app.middleware("http")
async def auth(request: Request, call_next):
    token = os.getenv("HUDDLE_TOKEN")
    if token and request.method != "OPTIONS":
        header = request.headers.get("authorization", "")
        query_token = request.query_params.get("token") if request.url.path.endswith("/audio") else None
        if header != f"Bearer {token}" and query_token != token:
            return JSONResponse({"detail": "unauthorized"}, status_code=401)
    return await call_next(request)


def _404(what: str = "Meeting"):
    return HTTPException(404, f"{what} not found")


def _resolver() -> ResolverContext:
    c = ctx()
    return ResolverContext(registry=c.registry, settings=c.settings(), memory_bytes=c.hardware.get("memoryBytes"))


# ---- health / system ---------------------------------------------------------- #
@app.get("/health")
def health():
    c = ctx()
    return {"ok": True, "version": __version__, "dataDir": str(c.cfg.data_dir), "schemaVersion": c.db.schema_version,
            "activeJob": c.jobs.active_meeting}


def _annotated_models():
    c = ctx()
    resolutions = resolve_all(_resolver())
    in_use = {r.model.id for r in resolutions if r.model}
    pulled = set(c.downloads.pulled_by_huddle())
    mem = c.hardware.get("memoryBytes")
    models = c.registry.models()
    for m in models:
        m.in_use = m.id in in_use
        if m.task == "llm":
            m.meta = {**m.meta, "recommended": bool(m.compatible and is_general_chat_model(m) and is_recommended_size(m, mem)),
                      "generalChat": is_general_chat_model(m), "pulledByHuddle": m.name in pulled}
            if m.source == "ollama" and m.name in pulled:
                m.externally_managed = False
        if m.task == "transcription":
            from .providers.transcription import mlx_available
            turbo = (m.meta or {}).get("whisperSize") in ("large-v3-turbo", "turbo")
            m.meta = {**m.meta, "recommended": turbo and (m.format == "MLX" or not mlx_available())}
    return models, resolutions


@app.get("/system/environment", response_model=Environment)
def environment():
    c = ctx()
    models, _ = _annotated_models()
    return Environment(hardware=c.hardware, devices=compute_devices(), providers=c.registry.providers(), models=models,
                       last_scan_at=c.registry.last_scan_at, scanning=c.registry.scanning)


@app.post("/system/rescan", response_model=Environment)
def rescan():
    ctx().registry.full_scan()
    return environment()


@app.get("/system/storage", response_model=StorageInfo)
def storage():
    c = ctx()
    models_bytes = 0
    if c.cfg.models_dir.exists():
        models_bytes = sum(p.stat().st_size for p in c.cfg.models_dir.rglob("*") if p.is_file())
    return StorageInfo(recordings_bytes=ms.recordings_bytes(c.cfg), max_bytes=int(c.settings().get("storage.maxBytes") or 0),
                       meeting_count=len(ms.list_meetings(c.db)), data_dir=str(c.cfg.data_dir),
                       models_dir=str(c.cfg.models_dir), logs_dir=str(c.cfg.logs_dir), models_bytes=models_bytes)


@app.post("/system/move-dir", response_model=StorageInfo)
def move_dir(req: MoveDirRequest):
    try:
        ctx().move_dir(req.kind, req.path, req.move_files)
    except (OSError, ValueError) as e:
        raise HTTPException(400, f"Could not move the folder: {e}")
    return storage()


@app.get("/setup/plan", response_model=SetupPlan)
def setup_plan():
    c = ctx()
    res = resolve_all(_resolver())
    return SetupPlan(hardware=c.hardware, devices=compute_devices(), providers=c.registry.providers(), resolutions=res,
                     additional_bytes=additional_bytes(res),
                     ready=all(r.status in ("ready", "builtin") for r in res if r.task != "llm"))


@app.get("/settings")
def get_settings():
    return ctx().settings()


@app.put("/settings")
def put_settings(patch: dict):
    s = ctx().update_settings(patch)
    if any(k.startswith("mcp.") for k in patch):
        _sync_mcp_network()
    return s


# ---- models -------------------------------------------------------------------- #
@app.get("/models/candidates", response_model=list[DownloadCandidate])
def candidates():
    """Marketplace list; the Recommended flag reflects this machine (MLX only where it runs, AI model by memory)."""
    return candidates_for(_resolver())


@app.get("/models/downloads")
def downloads():
    return [d.model_dump(by_alias=True) for d in ctx().downloads.list()]


@app.post("/models/downloads/{candidate_id:path}")
def start_download(candidate_id: str):
    cand = next((c for c in WHISPER_CANDIDATES + LLM_CANDIDATES if c.id == candidate_id), None)
    if not cand and candidate_id.startswith("ollama:"):
        name = candidate_id.split(":", 1)[1]
        cand = DownloadCandidate(id=candidate_id, name=name, task="llm", purpose="Meeting summaries", size_bytes=0,
                                 source="ollama", url=name, license="see Ollama library")
    if not cand:
        raise _404("Download candidate")
    return ctx().downloads.start(cand).model_dump(by_alias=True)


@app.delete("/models/downloads/{candidate_id:path}")
def cancel_download(candidate_id: str):
    ctx().downloads.cancel(candidate_id)
    return {"ok": True}


@app.delete("/models/{model_id:path}")
def delete_model(model_id: str):
    c = ctx()
    try:
        if model_id.startswith("ollama:"):
            c.downloads.delete_ollama_model(model_id.split(":", 1)[1])
        else:
            c.registry.delete_managed(model_id)
    except KeyError:
        raise _404("Model")
    except PermissionError as e:
        raise HTTPException(403, str(e))
    except Exception as e:
        raise HTTPException(500, f"The model could not be removed: {e}")
    for key in ("models.whisper", "models.ai"):
        if c.settings().get(key) == model_id:
            c.update_settings({key: None})
    return {"ok": True}


# ---- meetings ------------------------------------------------------------------ #
@app.get("/meetings")
def list_meetings(q: str | None = None, limit: int = 500):
    return [m.model_dump(by_alias=True) for m in ms.list_meetings(ctx().db, limit=limit, query=q)]


@app.post("/meetings/from-recording")
def from_recording(req: CreateFromRecordingRequest):
    c = ctx()
    if not c.db.one("SELECT 1 FROM meetings WHERE id = ?", (req.id,)):
        ms.create_from_recording(c.db, c.cfg, req)
    if req.process:
        c.jobs.enqueue(req.id)
    return ms.get_meeting(c.db, req.id).model_dump(by_alias=True)


@app.post("/meetings/import")
def import_meeting(req: ImportRequest):
    c = ctx()
    try:
        m = ms.import_file(c.db, c.cfg, req.path, req.title)
    except (FileNotFoundError, ValueError) as e:
        raise HTTPException(400, str(e))
    c.jobs.enqueue(m.id)
    return ms.get_meeting(c.db, m.id).model_dump(by_alias=True)


@app.get("/meetings/{meeting_id}")
def meeting_detail(meeting_id: str):
    d = ms.get_detail(ctx().db, meeting_id)
    if not d:
        raise _404()
    out = d.model_dump(by_alias=True)
    out["audioPath"] = ms.audio_path(ctx().db, ctx().cfg, meeting_id)
    return out


@app.get("/meetings/{meeting_id}/audio")
def meeting_audio(meeting_id: str):
    path = ms.audio_path(ctx().db, ctx().cfg, meeting_id)
    if not path:
        raise _404("Audio")
    return FileResponse(path, media_type="audio/wav")


@app.patch("/meetings/{meeting_id}")
def update_meeting(meeting_id: str, req: UpdateMeetingRequest):
    c = ctx()
    m = ms.update_meeting(c.db, meeting_id, title=req.title, notes=req.notes, language_override=req.language_override,
                          speaker_count_hint=req.speaker_count_hint)
    if not m:
        raise _404()
    if req.language_override is not None:
        # a different spoken language means the transcript (and everything after it) must be redone
        c.jobs.retry_stage(meeting_id, "transcribing")
    return ms.get_meeting(c.db, meeting_id).model_dump(by_alias=True)


@app.delete("/meetings/{meeting_id}")
def delete_meeting(meeting_id: str):
    c = ctx()
    if not ms.get_meeting(c.db, meeting_id):
        raise _404()
    c.jobs.cancel(meeting_id)
    ms.delete_meeting(c.db, c.cfg, meeting_id)
    return {"ok": True}


# ---- live transcription (while recording) ------------------------------------------ #
def _whisper_provider():
    from .providers.transcription import make_transcription_provider
    from .resolver import resolve_transcription
    c = ctx()
    res = resolve_transcription(_resolver())
    if res.status != "ready" or not res.model:
        raise HTTPException(409, "No Whisper model is installed yet.")
    s = c.settings()
    return make_transcription_provider(res.model, s.get("transcription.vocabulary") or [],
                                       s.get("general.computeDevice", "auto")), s.get("general.language", "auto")


@app.post("/live/start")
def live_start(body: dict):
    rid, path = str(body.get("recordingId", "")), str(body.get("filePath", ""))
    if not rid or not path:
        raise HTTPException(400, "recordingId and filePath are required")
    provider, language = _whisper_provider()
    ctx().live.start(rid, path, provider, language)
    return ctx().live.status(rid)


@app.get("/live/{recording_id}")
def live_status(recording_id: str):
    return ctx().live.status(recording_id)


@app.post("/live/{recording_id}/stop")
def live_stop(recording_id: str, final: bool = True):
    ctx().live.stop(recording_id, final=final)
    return ctx().live.status(recording_id)


@app.post("/meetings/{meeting_id}/delete-audio")
def delete_audio(meeting_id: str):
    c = ctx()
    if not ms.get_meeting(c.db, meeting_id):
        raise _404()
    return {"freedBytes": ms.delete_audio(c.db, c.cfg, meeting_id)}


@app.post("/meetings/{meeting_id}/process")
def process(meeting_id: str, body: dict | None = None):
    """Reprocess. Optional body: {"languageOverride": "nl" | "" (auto), "speakerCount": 2 | 0 (auto)}.
    The previous transcript and notes stay until each stage finishes, so cancelling keeps the old version."""
    c = ctx()
    if not ms.get_meeting(c.db, meeting_id):
        raise _404()
    if body and "languageOverride" in body:
        ms.update_meeting(c.db, meeting_id, language_override=str(body.get("languageOverride") or ""))
    if body and "speakerCount" in body:
        ms.update_meeting(c.db, meeting_id, speaker_count_hint=int(body.get("speakerCount") or 0))
    c.jobs.enqueue(meeting_id)
    return ms.get_job(c.db, meeting_id).model_dump(by_alias=True)


@app.post("/meetings/{meeting_id}/cancel")
def cancel_processing(meeting_id: str):
    c = ctx()
    if not ms.get_meeting(c.db, meeting_id):
        raise _404()
    c.jobs.cancel(meeting_id)
    return {"ok": True}


@app.get("/processes")
def processes():
    """Everything currently running or waiting: processing jobs, live transcriptions, downloads."""
    c = ctx()
    rows = c.db.query("SELECT j.*, m.title FROM processing_jobs j JOIN meetings m ON m.id = j.meeting_id"
                      " WHERE j.state IN ('queued','running') ORDER BY j.updated_at")
    import json as _json
    jobs = []
    for r in rows:
        stages = _json.loads(r["stages_json"])
        cur = r["current_stage"]
        jobs.append({"meetingId": r["meeting_id"], "title": r["title"], "state": r["state"], "stage": cur,
                     "progress": stages.get(cur, {}).get("progress") if cur else None,
                     "startedAt": stages.get(cur, {}).get("started_at") if cur else None,
                     "stages": stages})
    live = [{"recordingId": rid, **c.live.status(rid)} for rid in list(c.live._sessions.keys())]
    downloads = [d.model_dump(by_alias=True) for d in c.downloads.list() if d.state in ("downloading", "verifying")]
    return {"jobs": jobs, "live": live, "downloads": downloads}


@app.post("/meetings/{meeting_id}/retry/{stage}")
def retry(meeting_id: str, stage: str):
    c = ctx()
    if not ms.get_meeting(c.db, meeting_id):
        raise _404()
    try:
        c.jobs.retry_stage(meeting_id, stage)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return ms.get_job(c.db, meeting_id).model_dump(by_alias=True)


@app.get("/meetings/{meeting_id}/job")
def job(meeting_id: str):
    j = ms.get_job(ctx().db, meeting_id)
    return j.model_dump(by_alias=True) if j else None


@app.get("/meetings/{meeting_id}/transcript")
def transcript(meeting_id: str, words: bool = False):
    return [s.model_dump(by_alias=True) for s in transcripts.segments(ctx().db, meeting_id, with_words=words)]


@app.get("/meetings/{meeting_id}/export")
def export(meeting_id: str, format: str = "md"):
    try:
        body, media = export_svc.export(ctx().db, meeting_id, format)
    except KeyError:
        raise _404()
    except ValueError as e:
        raise HTTPException(400, str(e))
    return PlainTextResponse(body, media_type=media)


def _llm():
    from .jobs.stages import StageContext, _llm_provider
    c = ctx()
    sc = StageContext(db=c.db, cfg=c.cfg, registry=c.registry, settings=c.settings(), meeting_id="",
                      memory_bytes=c.hardware.get("memoryBytes"))
    try:
        provider, _ = _llm_provider(sc)
    except Exception as e:
        raise HTTPException(503, str(e))
    return provider


def _ui_language() -> str:
    from .providers.summarize import LANG_NAMES
    from .settings import resolve_notes_language
    code = resolve_notes_language(ctx().settings())
    return LANG_NAMES.get(code, code)


@app.post("/meetings/{meeting_id}/ask")
def ask_meeting(meeting_id: str, req: AskRequest):
    if not ms.get_meeting(ctx().db, meeting_id):
        raise _404()
    return ask_svc.ask(ctx().db, _llm(), req.question, meeting_id=meeting_id, language=_ui_language())


@app.post("/ask")
def ask_all(req: AskRequest):
    return ask_svc.ask(ctx().db, _llm(), req.question, language=_ui_language())


@app.post("/meetings/{meeting_id}/refine")
def refine_meeting(meeting_id: str, req: RefineRequest):
    """Store the user's feedback/context on the meeting and rerun: refining (transcript
    corrections) → summarizing → action items (if any were generated before) → indexing."""
    c = ctx()
    if not ms.get_meeting(c.db, meeting_id):
        raise _404()
    ms.update_meeting(c.db, meeting_id, context_html=req.context_html)
    has_auto = bool(c.db.one("SELECT 1 FROM action_items WHERE meeting_id = ? AND source = 'auto'", (meeting_id,)))
    c.jobs.enqueue(meeting_id, ["refining", "summarizing"] + (["extracting_actions"] if has_auto else []) + ["indexing"])
    return ms.get_job(c.db, meeting_id).model_dump(by_alias=True)


@app.post("/meetings/{meeting_id}/action-items/generate")
def generate_action_items(meeting_id: str):
    """Start action-item extraction as a tracked job (progress on the meeting and in Processes)."""
    c = ctx()
    if not ms.get_meeting(c.db, meeting_id):
        raise _404()
    c.jobs.enqueue(meeting_id, ["extracting_actions"])
    return ms.get_job(c.db, meeting_id).model_dump(by_alias=True)



# ---- speakers ------------------------------------------------------------------ #
@app.post("/meetings/{meeting_id}/speakers/rename")
def rename_speaker(meeting_id: str, req: RenameSpeakerRequest):
    s = transcripts.rename_speaker(ctx().db, req.meeting_speaker_id, req.name, enroll=req.enroll)
    if not s:
        raise _404("Speaker")
    return s.model_dump(by_alias=True)


@app.post("/meetings/{meeting_id}/speakers/confirm")
def confirm_speaker(meeting_id: str, req: ConfirmSuggestionRequest):
    s = transcripts.confirm_suggestion(ctx().db, req.meeting_speaker_id)
    if not s:
        raise _404("Suggestion")
    return s.model_dump(by_alias=True)


@app.post("/meetings/{meeting_id}/speakers/{source_id}/merge-into/{target_id}")
def merge_speakers(meeting_id: str, source_id: int, target_id: int):
    transcripts.merge_speakers(ctx().db, source_id, target_id)
    return [s.model_dump(by_alias=True) for s in transcripts.speakers(ctx().db, meeting_id)]


@app.get("/speakers")
def known_speakers():
    return transcripts.known_speakers(ctx().db)


@app.delete("/speakers/{speaker_id}")
def delete_known_speaker(speaker_id: int):
    transcripts.delete_known_speaker(ctx().db, speaker_id)
    return {"ok": True}


@app.patch("/segments/{segment_id}")
def update_segment(segment_id: int, req: UpdateSegmentRequest):
    s = transcripts.update_segment(ctx().db, segment_id, req.text, req.meeting_speaker_id)
    if not s:
        raise _404("Segment")
    return s.model_dump(by_alias=True)


# ---- search / action items ------------------------------------------------------ #
@app.get("/search")
def search(q: str, limit: int = 50, meeting_id: str | None = None):
    return [h.model_dump(by_alias=True) for h in search_svc.search(ctx().db, q, limit=limit, meeting_id=meeting_id)]


@app.get("/search/meetings")
def search_meetings(q: str, limit: int = 20):
    return search_svc.search_meetings(ctx().db, q, limit=limit)


@app.get("/action-items")
def action_items(open_only: bool = False, owner: str | None = None):
    return [a.model_dump(by_alias=True) for a in ai_svc.list_all(ctx().db, open_only=open_only, owner=owner)]


@app.post("/meetings/{meeting_id}/action-items")
def create_action_item(meeting_id: str, req: CreateActionItemRequest):
    if not ms.get_meeting(ctx().db, meeting_id):
        raise _404()
    return ai_svc.create(ctx().db, meeting_id, req.text, req.owner, req.due_date).model_dump(by_alias=True)


@app.patch("/action-items/{item_id}")
def update_action_item(item_id: int, req: UpdateActionItemRequest):
    fields = req.model_dump(exclude_unset=True, by_alias=False)
    a = ai_svc.update(ctx().db, item_id, text=req.text, owner=req.owner, due_date=req.due_date, done=req.done,
                      clear_owner="owner" in fields and req.owner is None,
                      clear_due="due_date" in fields and req.due_date is None)
    if not a:
        raise _404("Action item")
    return a.model_dump(by_alias=True)


@app.delete("/action-items/{item_id}")
def delete_action_item(item_id: int):
    ai_svc.delete(ctx().db, item_id)
    return {"ok": True}


# ---- MCP ------------------------------------------------------------------------ #
@app.get("/mcp/status", response_model=McpStatus)
def mcp_status():
    c = ctx()
    if c.mcp_network is None:
        _sync_mcp_network()
    return c.mcp_network.status(c.settings())


@app.get("/mcp/keys")
def list_api_keys():
    return [k.model_dump(by_alias=True) for k in api_keys.list_keys(ctx().db)]


@app.post("/mcp/keys")
def create_api_key(body: dict):
    return api_keys.create(ctx().db, str(body.get("name", "")), int(body.get("validityDays", 30) or 30)).model_dump(by_alias=True)


@app.post("/mcp/keys/{key_id}/renew")
def renew_api_key(key_id: int):
    k = api_keys.renew(ctx().db, key_id)
    if not k:
        raise _404("API key")
    return k.model_dump(by_alias=True)


@app.delete("/mcp/keys/{key_id}")
def delete_api_key(key_id: int):
    api_keys.delete(ctx().db, key_id)
    return {"ok": True}


# ---- privacy ------------------------------------------------------------------- #
@app.post("/privacy/delete-all-meetings")
def delete_all_meetings():
    c = ctx()
    ms.delete_all_data(c.db, c.cfg)
    return {"ok": True}


@app.post("/privacy/delete-speaker-embeddings")
def delete_embeddings():
    transcripts.delete_all_embeddings(ctx().db)
    return {"ok": True}


@app.get("/diagnostics/log")
def engine_log(lines: int = 400):
    p = Path(ctx().cfg.logs_dir) / "engine.log"
    if not p.exists():
        return PlainTextResponse("")
    data = p.read_text(errors="replace").splitlines()[-lines:]
    return PlainTextResponse("\n".join(data))
