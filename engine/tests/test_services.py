"""Meetings, transcripts (rename persistence), search, action items, exports."""
import json
import time
from datetime import UTC

import numpy as np
import soundfile as sf

from huddle_engine.schemas import CreateFromRecordingRequest
from huddle_engine.services import action_items as ai
from huddle_engine.services import exports, search, transcripts
from huddle_engine.services import meetings as ms


def _wav(path, seconds=2.0, sr=16000):
    sf.write(str(path), np.zeros(int(sr * seconds), dtype=np.float32), sr, subtype="PCM_16")
    return path


def _meeting(db, cfg, mid="m1"):
    (cfg.recordings_dir / mid).mkdir(parents=True, exist_ok=True)
    wav = _wav(cfg.recordings_dir / mid / "audio.wav")
    return ms.create_from_recording(db, cfg, CreateFromRecordingRequest(
        id=mid, file_path=str(wav), started_at="2026-09-03T09:00:00Z", duration_sec=2.0, input_device="Mic",
        sample_rate=48000, channels=1, format="wav/pcm16", title="Branding meeting"))


def _transcript(db, mid="m1"):
    with db.tx() as c:
        a = c.execute("INSERT INTO meeting_speakers(meeting_id,label,embedding,color_index) VALUES (?,?,?,0)",
                      (mid, "Speaker 1", json.dumps([1.0, 0.0]))).lastrowid
        b = c.execute("INSERT INTO meeting_speakers(meeting_id,label,embedding,color_index) VALUES (?,?,?,1)",
                      (mid, "Speaker 2", json.dumps([0.0, 1.0]))).lastrowid
        rows = [(mid, a, 0, 0.0, 4.0, "We moeten naast blauw ook een warme kleur gebruiken."),
                (mid, b, 1, 4.5, 8.0, "I can update the Figma file on Friday."),
                (mid, a, 2, 8.5, 12.0, "Dan sturen we donderdag de copy.")]
        for r in rows:
            c.execute("INSERT INTO transcript_segments(meeting_id,meeting_speaker_id,idx,start,\"end\",text) VALUES (?,?,?,?,?,?)", r)
    return a, b


def test_create_and_list_meeting(db, cfg):
    m = _meeting(db, cfg)
    assert m.title == "Branding meeting" and m.status == "saved"
    assert ms.get_recording(db, "m1").input_device == "Mic"
    assert [x.id for x in ms.list_meetings(db)] == ["m1"]
    from datetime import datetime
    assert abs(m.started_at - datetime(2026, 9, 3, 9, 0, tzinfo=UTC).timestamp()) < 1


def test_rename_speaker_persists_and_enrolls(db, cfg):
    _meeting(db, cfg)
    a, _ = _transcript(db)
    s = transcripts.rename_speaker(db, a, "Alex", enroll=True)
    assert s.display_name == "Alex" and s.speaker_id is not None
    segs = transcripts.segments(db, "m1")
    assert segs[0].speaker_name == "Alex" and segs[1].speaker_name == "Speaker 2"
    # Segment IDs unchanged → evidence timestamps remain valid.
    assert [x.idx for x in segs] == [0, 1, 2]
    known = transcripts.known_speakers(db)
    assert known[0]["name"] == "Alex" and known[0]["hasEmbedding"] and known[0]["nSamples"] == 1
    # Enrolling again folds the embedding into a running mean.
    transcripts.rename_speaker(db, a, "Alex", enroll=True)
    assert transcripts.known_speakers(db)[0]["nSamples"] == 2
    # Survives reopening the database.
    db2 = type(db)(cfg.db_path)
    assert transcripts.segments(db2, "m1")[0].speaker_name == "Alex"
    db2.close()


def test_rename_without_enroll_keeps_no_profile(db, cfg):
    _meeting(db, cfg)
    a, _ = _transcript(db)
    transcripts.rename_speaker(db, a, "Guest", enroll=False)
    assert transcripts.known_speakers(db) == []
    assert transcripts.segments(db, "m1")[0].speaker_name == "Guest"


def test_merge_speakers(db, cfg):
    _meeting(db, cfg)
    a, b = _transcript(db)
    transcripts.merge_speakers(db, b, a)
    assert {s.meeting_speaker_id for s in transcripts.segments(db, "m1")} == {a}
    assert len(transcripts.speakers(db, "m1")) == 1


def test_search_prefix_matching_and_timestamps(db, cfg):
    _meeting(db, cfg)
    _transcript(db)
    hits = search.search(db, "kleur")           # prefix → 'kleur' matches 'kleur' (and would match 'kleuren')
    assert len(hits) == 1 and hits[0].start == 0.0 and hits[0].segment_id
    assert "[" in hits[0].snippet
    hits = search.search(db, "figma friday")
    assert len(hits) == 1 and hits[0].speaker_name == "Speaker 2"
    assert search.search(db, "nonexistentword") == []
    # malformed FTS input never raises
    assert search.search(db, '"unbalanced (quote') == []
    ranked = search.search_meetings(db, "copy")
    assert ranked and ranked[0]["meetingId"] == "m1"


def test_action_items_null_owner_and_due(db, cfg):
    _meeting(db, cfg)
    a = ai.create(db, "m1", "Controleer analytics", None, None)
    assert a.owner is None and a.due_date is None and a.source == "manual"
    a = ai.update(db, a.id, done=True)
    assert a.done
    a = ai.update(db, a.id, owner="Alex", due_date="2026-09-04")
    assert a.owner == "Alex" and a.due_date == "2026-09-04"
    a = ai.update(db, a.id, clear_owner=True)
    assert a.owner is None
    assert ai.list_all(db, open_only=True) == []
    assert len(ai.list_all(db)) == 1 and ai.list_all(db)[0].meeting_title == "Branding meeting"


def test_exports(db, cfg):
    _meeting(db, cfg)
    a, _ = _transcript(db)
    transcripts.rename_speaker(db, a, "Alex")
    db.execute("INSERT INTO summaries(meeting_id, summary, provider, model, created_at) VALUES ('m1','Korte samenvatting.','ollama','q',?)", (time.time(),))
    db.execute("INSERT INTO decisions(meeting_id, position, text, evidence_start, evidence_end) VALUES ('m1',0,'Warme accentkleur.',0.0,4.0)")
    ai.create(db, "m1", "Copy aanleveren", "Daan", None)
    md, media = exports.export(db, "m1", "md")
    assert media == "text/markdown" and "## Decisions" in md and "Alex" in md and "Daan" in md and "Unassigned" not in md.split("## Transcript")[0].replace("Daan", "")
    srt, _ = exports.export(db, "m1", "srt")
    assert "00:00:00,000 --> 00:00:04,000" in srt and "Alex:" in srt
    js, _ = exports.export(db, "m1", "json")
    data = json.loads(js)
    assert data["meeting"]["title"] == "Branding meeting" and len(data["segments"]) == 3
    assert data["decisions"][0]["evidenceStart"] == 0.0
    txt, _ = exports.export(db, "m1", "txt")
    assert "[00:00] Alex:" in txt


def test_delete_meeting_removes_files_inside_data_dir_only(db, cfg, tmp_path):
    _meeting(db, cfg)
    outside = _wav(tmp_path / "outside.wav")
    ms.import_file(db, cfg, str(outside), "Imported")
    ids = [m.id for m in ms.list_meetings(db)]
    for mid in ids:
        ms.delete_meeting(db, cfg, mid)
    assert ms.list_meetings(db) == []
    assert not (cfg.recordings_dir / "m1").exists()
    assert outside.exists()          # imported originals are never deleted


def test_audio_path_prefers_original_wav_inside_data_dir(db, cfg):
    _meeting(db, cfg)
    p = ms.audio_path(db, cfg, "m1")
    assert p and p.endswith("audio.wav")
    ms.delete_audio(db, cfg, "m1")
    assert ms.audio_path(db, cfg, "m1") is None
    assert len(transcripts.segments(db, "m1")) == 0  # nothing to lose here, but transcript table untouched
