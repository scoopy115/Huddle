"""Processing on a Huddle Server: result bundle round trip, the upload/remote stages against a
mocked server, and the pipeline chosen per meeting."""
import json
import time

import httpx
import numpy as np
import pytest
import soundfile as sf

from huddle_engine import server_client
from huddle_engine.jobs import stages as st
from huddle_engine.jobs.runner import JobRunner
from huddle_engine.schemas import DEFAULT_PIPELINE, REMOTE_PIPELINE, CreateFromRecordingRequest, pipeline_for
from huddle_engine.services import meetings as ms
from huddle_engine.services import transcripts
from huddle_engine.services.bundle import export_bundle, import_bundle


def _meeting(db, cfg, mid="m1", target="local"):
    d = cfg.recordings_dir / mid
    d.mkdir(parents=True, exist_ok=True)
    wav = d / "audio.wav"
    sf.write(str(wav), np.zeros(16000 * 2, dtype=np.int16), 16000)
    m = ms.create_from_recording(db, cfg, CreateFromRecordingRequest(
        id=mid, file_path=str(wav), started_at="2026-10-09T09:00:00Z", duration_sec=2.0, input_device="Mic",
        sample_rate=16000, channels=1, format="wav/pcm16", processing_target=target))
    return m, wav


def _fill(db, mid="m1"):
    with db.tx() as c:
        a = c.execute("INSERT INTO meeting_speakers(meeting_id,label,embedding,embedding_model,color_index,display_name,name_source)"
                      " VALUES (?,?,?,?,0,?,?)", (mid, "Speaker 1", json.dumps([1.0, 0.0]), "titanet-large", "Alex", "inferred")).lastrowid
        b = c.execute("INSERT INTO meeting_speakers(meeting_id,label,embedding,embedding_model,color_index) VALUES (?,?,?,?,1)",
                      (mid, "Speaker 2", json.dumps([0.0, 1.0]), "titanet-large")).lastrowid
        s1 = c.execute("INSERT INTO transcript_segments(meeting_id,meeting_speaker_id,idx,start,\"end\",text,language) VALUES (?,?,0,0.0,4.0,?,?)",
                       (mid, a, "We kiezen blauw.", "nl")).lastrowid
        c.execute("INSERT INTO transcript_words(segment_id,start,\"end\",word,confidence) VALUES (?,0.0,1.0,'We',0.9)", (s1,))
        s2 = c.execute("INSERT INTO transcript_segments(meeting_id,meeting_speaker_id,idx,start,\"end\",text) VALUES (?,?,1,4.5,8.0,?)",
                       (mid, b, "Prima, ik stuur de copy vrijdag.")).lastrowid
        c.execute("UPDATE meetings SET language = 'nl', title = 'Kleuren en copy' WHERE id = ?", (mid,))
        c.execute("INSERT INTO summaries(meeting_id, summary, provider, model, created_at) VALUES (?,?,?,?,?)",
                  (mid, "Korte samenvatting.", "ollama", "qwen", time.time()))
        c.execute("INSERT INTO topics(meeting_id, position, title, summary) VALUES (?,0,'Branding','Kleuren')", (mid,))
        c.execute("INSERT INTO decisions(meeting_id, position, text, evidence_start, evidence_end, segment_id) VALUES (?,0,'Blauw.',0.0,4.0,?)", (mid, s1))
        c.execute("INSERT INTO action_items(meeting_id, position, text, owner, due_date, confidence, evidence_start, evidence_end,"
                  " segment_id, done, source, created_at) VALUES (?,0,'Copy sturen','Speaker 2','2026-10-10',0.8,4.5,8.0,?,0,'auto',?)",
                  (mid, s2, time.time()))


def test_bundle_round_trip(db, cfg):
    _meeting(db, cfg, "src")
    _fill(db, "src")
    b = export_bundle(db, "src")
    assert b["version"] == 1 and len(b["segments"]) == 2 and b["segments"][0]["speaker"] == "Speaker 1"
    assert b["speakers"][0]["embedding"] == [1.0, 0.0] and b["speakers"][0]["displayName"] == "Alex"
    assert b["notes"]["decisions"][0]["segmentIdx"] == 0 and b["notes"]["actionItems"][0]["segmentIdx"] == 1
    assert b["segments"][0]["words"][0]["word"] == "We"
    # JSON-serialisable
    b = json.loads(json.dumps(b))

    _meeting(db, cfg, "dst")
    counts = import_bundle(db, "dst", b)
    assert counts == {"speakers": 2, "segments": 2, "notes": 1}
    segs = transcripts.segments(db, "dst", with_words=True)
    assert [s.text for s in segs] == ["We kiezen blauw.", "Prima, ik stuur de copy vrijdag."]
    assert segs[0].speaker_name == "Alex" and segs[0].words[0].word == "We"
    d = ms.get_detail(db, "dst")
    assert d.meeting.language == "nl" and d.meeting.title == "Kleuren en copy"
    assert d.summary.provider == "server:ollama" and d.topics[0].title == "Branding"
    assert d.decisions[0].segment_id == segs[0].id and d.action_items[0].segment_id == segs[1].id
    emb = db.one("SELECT embedding, embedding_model FROM meeting_speakers WHERE meeting_id='dst' AND label='Speaker 2'")
    assert json.loads(emb["embedding"]) == [0.0, 1.0] and emb["embedding_model"] == "titanet-large"
    # a custom local title is kept
    ms.update_meeting(db, "dst", title="Mijn titel")
    import_bundle(db, "dst", b)
    assert ms.get_meeting(db, "dst").title == "Mijn titel"


def test_pipeline_per_target(db, cfg):
    assert pipeline_for("local") == DEFAULT_PIPELINE and pipeline_for("remote") == REMOTE_PIPELINE
    _meeting(db, cfg, "r", target="remote")
    assert ms.processing_target(db, "r") == "remote"
    assert st.downstream("preprocessing", "remote")[0] == "uploading"
    assert st.downstream("preprocessing", "local")[0] == "transcribing"


class FakeServer:
    """Mock transport that behaves like Huddle Server's /v1 API."""

    def __init__(self, bundle, fail=False):
        self.bundle, self.fail = bundle, fail
        self.polls = 0
        self.uploaded = None

    def handler(self, request: httpx.Request) -> httpx.Response:
        auth = request.headers.get("authorization")
        if auth != "Bearer hsk_test":
            return httpx.Response(401, json={"detail": "bad key"})
        p = request.url.path
        if p == "/v1/ping":
            return httpx.Response(200, json={"name": "Thuisserver", "version": "0.1.0", "capabilities": {"llm": True}})
        if p == "/v1/recordings" and request.method == "POST":
            self.uploaded = request.content
            assert b'name="audio"; filename="upload.flac"' in self.uploaded
            assert b'"notesLanguage"' in self.uploaded
            return httpx.Response(200, json={"id": "m1", "state": "queued"})
        if p == "/v1/recordings/m1":
            self.polls += 1
            if self.polls < 3:
                return httpx.Response(200, json={"id": "m1", "state": "running",
                                                  "job": {"currentStage": "transcribing", "stages": {"preprocessing": {"status": "done"}, "transcribing": {"status": "running", "progress": 0.5}}}})
            if self.fail:
                return httpx.Response(200, json={"id": "m1", "state": "failed", "error": "No speech was detected."})
            return httpx.Response(200, json={"id": "m1", "state": "ready", "job": {"stages": {}}})
        if p == "/v1/recordings/m1/result":
            return httpx.Response(200, json=self.bundle)
        return httpx.Response(404, json={"detail": "nope"})


@pytest.fixture
def fake_server(db, cfg, monkeypatch):
    _meeting(db, cfg, "src")
    _fill(db, "src")
    bundle = json.loads(json.dumps(export_bundle(db, "src")))
    ms.delete_meeting(db, cfg, "src")
    fs = FakeServer(bundle)
    monkeypatch.setattr(server_client, "_transport_override", httpx.MockTransport(fs.handler))
    monkeypatch.setattr(st.time, "sleep", lambda s: None)
    return fs


def _ctx(db, cfg, settings, mid, names):
    from huddle_engine.discovery.registry import Registry
    return st.StageContext(db=db, cfg=cfg, registry=Registry(db, cfg.models_dir), settings=settings, meeting_id=mid,
                           run_stages=names)


def test_upload_and_remote_stages(db, cfg, fake_server):
    settings = {"server.url": "https://huddle.example.test", "server.apiKey": "hsk_test", "general.uiLanguage": "nl"}
    _meeting(db, cfg, "m1", target="remote")
    st.preprocessing(_ctx(db, cfg, settings, "m1", REMOTE_PIPELINE))
    notes = []
    ctx = _ctx(db, cfg, settings, "m1", REMOTE_PIPELINE)
    ctx.status = notes.append
    detail = st.uploading(ctx)
    assert "huddle.example.test" in detail and fake_server.uploaded and ms.get_meeting(db, "m1").remote_id == "m1"
    assert not (cfg.recordings_dir / "m1" / "upload.flac").exists()
    detail = st.remote_processing(ctx)
    assert "2 segments" in detail and "notes" in detail and "Transcribing" in notes
    d = ms.get_detail(db, "m1")
    assert d.summary.provider == "server:ollama" and len(d.segments) == 2
    # the local summary stage steps aside when the server wrote the notes in this run
    with pytest.raises(st.StageSkipped):
        st.summarizing(ctx)


def test_remote_failure_is_reported(db, cfg, fake_server):
    fake_server.fail = True
    settings = {"server.url": "https://huddle.example.test", "server.apiKey": "hsk_test"}
    _meeting(db, cfg, "m1", target="remote")
    ms.set_remote(db, "m1", "m1")
    with pytest.raises(st.ProviderError) as ei:
        st.remote_processing(_ctx(db, cfg, settings, "m1", REMOTE_PIPELINE))
    assert "No speech" in str(ei.value)


def test_bad_key_is_a_clear_error(db, cfg, fake_server):
    settings = {"server.url": "https://huddle.example.test", "server.apiKey": "hsk_wrong"}
    _meeting(db, cfg, "m1", target="remote")
    st.preprocessing(_ctx(db, cfg, settings, "m1", REMOTE_PIPELINE))
    with pytest.raises(st.ProviderError) as ei:
        st.uploading(_ctx(db, cfg, settings, "m1", REMOTE_PIPELINE))
    assert "client key" in str(ei.value)


def test_runner_enqueue_uses_remote_pipeline(db, cfg):
    from huddle_engine.discovery.registry import Registry
    _meeting(db, cfg, "m1", target="remote")
    runner = JobRunner(db, cfg, Registry(db, cfg.models_dir), lambda: {})
    runner.enqueue("m1")
    stages = json.loads(db.one("SELECT stages_json FROM processing_jobs WHERE meeting_id='m1'")["stages_json"])
    assert stages["uploading"]["status"] == "pending" and stages["transcribing"]["status"] == "skipped"
    runner.retry_stage("m1", "remote_processing")
    stages = json.loads(db.one("SELECT stages_json FROM processing_jobs WHERE meeting_id='m1'")["stages_json"])
    assert stages["remote_processing"]["status"] == "pending" and stages["identifying_speakers"]["status"] == "pending"


def test_normalize_and_fingerprint():
    assert server_client.normalize_url("huddle.local:8443/") == "https://huddle.local:8443"
    assert server_client.format_fingerprint("ab:cd ef") == "AB:CD:EF"
