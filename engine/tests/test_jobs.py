"""Processing job state machine: persistence, per-stage failure isolation, retry, crash recovery."""
import json

import numpy as np
import soundfile as sf

from huddle_engine.discovery.registry import Registry
from huddle_engine.jobs import stages as st
from huddle_engine.jobs.runner import JobRunner
from huddle_engine.providers.base import ProviderError
from huddle_engine.schemas import STAGES, CreateFromRecordingRequest
from huddle_engine.services import meetings as ms


def _meeting(db, cfg, mid="m1"):
    (cfg.recordings_dir / mid).mkdir(parents=True, exist_ok=True)
    wav = cfg.recordings_dir / mid / "audio.wav"
    t = np.linspace(0, 3, 48000 * 3, dtype=np.float32)
    sf.write(str(wav), 0.1 * np.sin(2 * np.pi * 440 * t), 48000, subtype="PCM_16")
    return ms.create_from_recording(db, cfg, CreateFromRecordingRequest(
        id=mid, file_path=str(wav), started_at=1_700_000_000.0, duration_sec=3.0, format="wav"))


def _runner(db, cfg, fakes):
    reg = Registry(db, cfg.models_dir)
    r = JobRunner(db, cfg, reg, lambda: {"speakers.diarization": True, "speakers.recognition": True})
    for name, fn in fakes.items():
        st.STAGE_FUNCS[name] = fn
    return r


def _restore():
    st.STAGE_FUNCS.update({"preprocessing": st.preprocessing, "transcribing": st.transcribing, "diarizing": st.diarizing,
                           "identifying_speakers": st.identifying_speakers, "summarizing": st.summarizing,
                           "indexing": st.indexing})


def test_all_stages_done_marks_ready(db, cfg):
    _meeting(db, cfg)
    r = _runner(db, cfg, {n: (lambda ctx, n=n: f"{n} ok") for n in STAGES if n != "preprocessing"})
    try:
        r.enqueue("m1")
        r._run("m1", list(STAGES))
    finally:
        _restore()
    job = ms.get_job(db, "m1")
    assert job.state == "ready" and all(s.status == "done" for s in job.stages.values())
    assert job.stages["preprocessing"].detail.startswith("0.1 min")   # real preprocessing ran (3 s → 16 kHz)
    assert ms.get_recording(db, "m1").processed_path.endswith("processed.wav")
    assert ms.get_meeting(db, "m1").status == "ready"


def test_summary_failure_keeps_transcript_and_is_retryable(db, cfg):
    _meeting(db, cfg)

    def boom(ctx):
        raise ProviderError("Summary generation failed.", detail="OOM")

    fakes = {n: (lambda ctx: "ok") for n in STAGES if n != "preprocessing"}
    fakes["summarizing"] = boom
    r = _runner(db, cfg, fakes)
    try:
        r.enqueue("m1")
        r._run("m1", list(STAGES))
        job = ms.get_job(db, "m1")
        assert job.state == "failed"
        assert job.stages["transcribing"].status == "done"
        assert job.stages["summarizing"].status == "failed"
        assert job.stages["summarizing"].error == "Summary generation failed."
        assert job.stages["summarizing"].error_detail == "OOM"
        assert job.stages["indexing"].status == "done"          # independent stage still ran
        assert ms.get_meeting(db, "m1").status == "ready"       # transcript exists → meeting usable
        # retry only the failed stage
        st.STAGE_FUNCS["summarizing"] = lambda ctx: "fixed"
        r.retry_stage("m1", "summarizing")
        r._run("m1", ["summarizing"])
        job = ms.get_job(db, "m1")
        assert job.state == "ready" and job.stages["summarizing"].status == "done"
    finally:
        _restore()


def test_transcription_failure_skips_dependents(db, cfg):
    _meeting(db, cfg)

    def boom(ctx):
        raise ProviderError("No transcription model is installed.")

    fakes = {n: (lambda ctx: "ok") for n in STAGES if n != "preprocessing"}
    fakes["transcribing"] = boom
    r = _runner(db, cfg, fakes)
    try:
        r.enqueue("m1")
        r._run("m1", list(STAGES))
    finally:
        _restore()
    job = ms.get_job(db, "m1")
    assert job.state == "failed"
    assert job.stages["diarizing"].status == "skipped"
    assert job.stages["summarizing"].status == "skipped"
    assert ms.get_meeting(db, "m1").status == "failed"
    assert job.error == "No transcription model is installed."


def test_unexpected_exception_becomes_friendly_error(db, cfg):
    _meeting(db, cfg)

    def crash(ctx):
        raise KeyError("weird")

    fakes = {n: (lambda ctx: "ok") for n in STAGES if n != "preprocessing"}
    fakes["diarizing"] = crash
    r = _runner(db, cfg, fakes)
    try:
        r.enqueue("m1")
        r._run("m1", list(STAGES))
    finally:
        _restore()
    s = ms.get_job(db, "m1").stages["diarizing"]
    assert s.status == "failed" and s.error == "Speaker detection failed unexpectedly." and "KeyError" in s.error_detail


def test_recover_marks_interrupted_jobs(db, cfg):
    _meeting(db, cfg)
    stages = {n: {"status": "pending"} for n in STAGES}
    stages["preprocessing"] = {"status": "done"}
    stages["transcribing"] = {"status": "running", "started_at": 1.0}
    db.execute("INSERT INTO processing_jobs(meeting_id,state,current_stage,stages_json,created_at,updated_at)"
               " VALUES ('m1','running','transcribing',?,1,1)", (json.dumps(stages),))
    r = _runner(db, cfg, {})
    r.recover()
    job = ms.get_job(db, "m1")
    # interrupted work is resumed, not reported as failed
    assert job.state == "queued"
    assert job.stages["transcribing"].status == "pending" and job.stages["preprocessing"].status == "done"
    mid, names = r._q.get_nowait()
    assert mid == "m1" and names[0] == "transcribing" and "summarizing" in names


def test_retry_downstream_expands(db, cfg):
    _meeting(db, cfg)
    r = _runner(db, cfg, {})
    r.retry_stage("m1", "diarizing")
    job = ms.get_job(db, "m1")
    assert job.state == "queued"
    assert job.stages["diarizing"].status == "pending" and job.stages["identifying_speakers"].status == "pending"
    _mid, names = r._q.get_nowait()
    assert names == ["diarizing", "identifying_speakers", "summarizing"]


def test_no_ai_model_skips_notes_and_keeps_the_transcript(db, cfg, monkeypatch):
    """Without an AI model the summary is skipped — no keyword-picked stand-in — the job ends
    ready, and the transcript stays readable and exportable."""
    from huddle_engine.providers.llm import ExtractiveProvider
    from huddle_engine.services import exports
    _meeting(db, cfg)
    with db.tx() as c:
        sp = c.execute("INSERT INTO meeting_speakers(meeting_id,label) VALUES ('m1','Speaker 1')").lastrowid
        c.execute("INSERT INTO transcript_segments(meeting_id,meeting_speaker_id,idx,start,\"end\",text) VALUES ('m1',?,0,0,2,?)",
                  (sp, "We decided to launch the new homepage next Monday."))
    monkeypatch.setattr(st, "_llm_provider", lambda ctx: (ExtractiveProvider(), None))
    fakes = {n: (lambda ctx: "ok") for n in STAGES if n not in ("preprocessing", "summarizing")}
    r = _runner(db, cfg, fakes)
    try:
        r.enqueue("m1")
        r._run("m1", ["summarizing", "indexing"])
    finally:
        _restore()
    job = ms.get_job(db, "m1")
    assert job.stages["summarizing"].status == "skipped"
    assert job.stages["summarizing"].detail == "Needs a local AI model"
    assert job.state == "ready"
    assert ms.get_summary(db, "m1") is None and ms.get_topics(db, "m1") == [] and ms.get_decisions(db, "m1") == []
    body, _ = exports.export(db, "m1", "md")
    assert "launch the new homepage" in body
    # action items: the same, skipped rather than guessed
    with __import__("pytest").raises(st.StageSkipped):
        st.extracting_actions(st.StageContext(db=db, cfg=cfg, registry=None, settings={}, meeting_id="m1"))


def test_migration_removes_fallback_notes(tmp_path):
    """Schema 9 drops what the no-model fallback wrote; transcripts and ticked items stay."""
    import sqlite3

    from huddle_engine.db.migrations import MIGRATIONS, migrate
    conn = sqlite3.connect(str(tmp_path / "h.db"))
    for version, script in MIGRATIONS:
        if version >= 9:
            break
        conn.executescript(f"BEGIN;\n{script}\nPRAGMA user_version = {version};\nCOMMIT;")
    conn.executescript("""
        INSERT INTO meetings(id,title,created_at,started_at,status,source) VALUES ('a','A',1,1,'ready','recorded'), ('b','B',1,1,'ready','recorded');
        INSERT INTO transcript_segments(meeting_id,idx,start,"end",text) VALUES ('a',0,0,1,'hello'), ('b',0,0,1,'hi');
        INSERT INTO summaries(meeting_id,summary,provider,created_at) VALUES ('a','keywords','extractive',1), ('b','real','ollama',1);
        INSERT INTO topics(meeting_id,position,title) VALUES ('a',0,'x'), ('b',0,'y');
        INSERT INTO decisions(meeting_id,position,text) VALUES ('a',0,'x'), ('b',0,'y');
        INSERT INTO action_items(meeting_id,position,text,done,source,created_at) VALUES
            ('a',0,'guess',0,'auto',1), ('a',1,'ticked',1,'auto',1), ('a',2,'mine',0,'manual',1), ('b',0,'real',0,'auto',1);
    """)
    assert migrate(conn) >= 9
    q = lambda sql: [r[0] for r in conn.execute(sql)]  # noqa: E731
    assert q("SELECT meeting_id FROM summaries") == ["b"]
    assert q("SELECT meeting_id FROM topics") == ["b"] and q("SELECT meeting_id FROM decisions") == ["b"]
    assert sorted(q("SELECT text FROM action_items")) == ["mine", "real", "ticked"]
    assert q("SELECT COUNT(*) FROM transcript_segments") == [2]


def test_notes_turned_off_skips_the_summary_without_loading_a_model(db, cfg, monkeypatch):
    """notes.enabled = False: transcript and speakers only. The AI provider is never resolved."""
    _meeting(db, cfg)
    with db.tx() as c:
        sp = c.execute("INSERT INTO meeting_speakers(meeting_id,label) VALUES ('m1','Speaker 1')").lastrowid
        c.execute("INSERT INTO transcript_segments(meeting_id,meeting_speaker_id,idx,start,\"end\",text) VALUES ('m1',?,0,0,2,?)",
                  (sp, "We decided to launch the new homepage next Monday."))
    def boom(ctx):
        raise AssertionError("the AI provider must not be touched when notes are off")
    monkeypatch.setattr(st, "_llm_provider", boom)
    fakes = {n: (lambda ctx: "ok") for n in STAGES if n not in ("preprocessing", "summarizing")}
    r = _runner(db, cfg, fakes)
    r.settings_fn = lambda: {"notes.enabled": False}
    try:
        r.enqueue("m1")
        r._run("m1", ["summarizing", "indexing"])
    finally:
        _restore()
    job = ms.get_job(db, "m1")
    assert job.stages["summarizing"].status == "skipped"
    assert job.stages["summarizing"].detail == "AI notes are turned off in Settings"
    assert job.state == "ready"
    assert ms.get_summary(db, "m1") is None
