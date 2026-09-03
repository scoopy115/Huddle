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
