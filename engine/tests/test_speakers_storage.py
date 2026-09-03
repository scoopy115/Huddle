"""Speaker-name inference, rename propagation into notes, storage quota, API keys, settings migration."""
import json

import numpy as np
import soundfile as sf

from huddle_engine.providers.base import Segment
from huddle_engine.providers.llm import ExtractiveProvider
from huddle_engine.providers.summarize import heuristic_speaker_names, infer_speaker_names
from huddle_engine.schemas import CreateFromRecordingRequest
from huddle_engine.services import action_items as ai
from huddle_engine.services import api_keys, transcripts
from huddle_engine.services import meetings as ms
from huddle_engine.settings import normalize_settings


def test_heuristic_names_from_addressing():
    segs = [Segment(0, 4, "Dan besluiten we dat. Daan, kun jij de kleuren aanpassen in Figma?", speaker_label="Speaker 1"),
            Segment(4, 8, "Sure. I can do that on Friday.", speaker_label="Speaker 3"),
            Segment(8, 11, "Prima. Goedemorgen, laten we verder gaan.", speaker_label="Speaker 1"),
            Segment(11, 15, "Thanks Xander. I will deliver the copy on Thursday.", speaker_label="Speaker 2")]
    names = heuristic_speaker_names(segs)
    assert names["Speaker 3"][0] == "Daan"          # addressed → answers next
    assert names["Speaker 1"][0] == "Xander"        # thanked → previous speaker
    assert "Speaker 2" not in names                 # no evidence
    # stop-words are never names
    assert not heuristic_speaker_names([Segment(0, 2, "Goedemorgen, kun je beginnen?", speaker_label="Speaker 1"),
                                        Segment(2, 3, "Ja.", speaker_label="Speaker 2")])


def test_infer_names_llm_overrides_only_generic_labels():
    segs = [Segment(0, 4, "Daan, kun jij dit doen?", speaker_label="Speaker 1"), Segment(4, 6, "Ja.", speaker_label="Speaker 2")]

    class Fake:
        id = "fake"
        model = "fake"

        def complete_json(self, s, u, max_tokens=2048):
            return json.dumps({"speakers": [{"label": "Speaker 2", "name": "Daan", "confidence": 0.9, "evidenceSegments": [0]},
                                            {"label": "Speaker 1", "name": "Xander", "confidence": 0.5, "evidenceSegments": []},
                                            {"label": "Speaker 9", "name": "Ghost", "confidence": 0.99, "evidenceSegments": []}]})

    names = infer_speaker_names(Fake(), segs, {"Speaker 1": "Speaker 1", "Speaker 2": "Speaker 2"})
    assert names == {"Speaker 2": ("Daan", 0.9)}                       # low confidence + unknown label ignored
    # Already-named speakers are never overridden.
    assert infer_speaker_names(Fake(), segs, {"Speaker 1": "Xander", "Speaker 2": "Karen"}) == {}
    # Extractive provider → heuristic only, still works.
    assert infer_speaker_names(ExtractiveProvider(), segs, {"Speaker 1": "Speaker 1", "Speaker 2": "Speaker 2"})["Speaker 2"][0] == "Daan"


def _meeting(db, cfg, mid="m1", seconds=2.0):
    (cfg.recordings_dir / mid).mkdir(parents=True, exist_ok=True)
    wav = cfg.recordings_dir / mid / "audio.wav"
    sf.write(str(wav), np.zeros(int(16000 * seconds), dtype=np.float32), 16000, subtype="PCM_16")
    return ms.create_from_recording(db, cfg, CreateFromRecordingRequest(
        id=mid, file_path=str(wav), started_at=1_700_000_000.0 + hash(mid) % 1000, duration_sec=seconds, title=mid))


def test_rename_propagates_into_notes(db, cfg):
    _meeting(db, cfg)
    with db.tx() as c:
        sp = c.execute("INSERT INTO meeting_speakers(meeting_id,label,color_index) VALUES ('m1','Speaker 2',1)").lastrowid
        c.execute("INSERT INTO summaries(meeting_id,summary,created_at) VALUES ('m1','Speaker 2 levert de copy. Speaker 20 niet.',1)")
        c.execute("INSERT INTO topics(meeting_id,position,title,summary) VALUES ('m1',0,'Copy','Speaker 2 doet dit.')")
        c.execute("INSERT INTO decisions(meeting_id,position,text) VALUES ('m1',0,'Speaker 2 stuurt donderdag.')")
    ai.create(db, "m1", "Copy aanleveren", "Speaker 2", None)
    transcripts.rename_speaker(db, sp, "Samantha", enroll=False)
    assert db.one("SELECT summary FROM summaries")["summary"] == "Samantha levert de copy. Speaker 20 niet."
    assert db.one("SELECT summary FROM topics")["summary"] == "Samantha doet dit."
    assert db.one("SELECT text FROM decisions")["text"] == "Samantha stuurt donderdag."
    assert ai.list_all(db)[0].owner == "Samantha"
    assert transcripts.speakers(db, "m1")[0].name_source == "user"
    assert ms.list_meetings(db)[0].participants == ["Samantha"]       # display_name without a known speaker
    # renaming again replaces the previous name, not the label
    transcripts.rename_speaker(db, sp, "Sam", enroll=False)
    assert ai.list_all(db)[0].owner == "Sam" and "Sam levert" in db.one("SELECT summary FROM summaries")["summary"]


def test_storage_quota_deletes_oldest_audio_only(db, cfg):
    for mid, secs in (("old", 4.0), ("mid", 4.0), ("new", 4.0)):
        _meeting(db, cfg, mid, secs)
    db.execute("UPDATE meetings SET status = 'ready'")
    db.execute("UPDATE meetings SET started_at = CASE id WHEN 'old' THEN 1 WHEN 'mid' THEN 2 ELSE 3 END")
    with db.tx() as c:
        c.execute("INSERT INTO transcript_segments(meeting_id, idx, start, \"end\", text) VALUES ('old',0,0,1,'keep me')")
    total = ms.recordings_bytes(cfg)
    per = total // 3
    removed = ms.enforce_storage_limit(db, cfg, per * 2 + 10)      # room for two recordings
    assert removed == ["old"]
    assert ms.recordings_bytes(cfg) <= per * 2 + 10
    assert ms.get_recording(db, "old").status == "audio_deleted"
    assert len(transcripts.segments(db, "old")) == 1                # transcript kept
    assert ms.enforce_storage_limit(db, cfg, 0) == []               # 0 = unlimited


def test_api_keys_hash_only(db):
    k = api_keys.create(db, "Compute farm")
    assert k.key.startswith("hud_") and k.prefix == k.key[:12]
    stored = db.one("SELECT key_hash FROM api_keys")["key_hash"]
    assert k.key not in stored and len(stored) == 64
    assert api_keys.verify(db, k.key) and not api_keys.verify(db, "hud_wrong") and not api_keys.verify(db, None)
    listed = api_keys.list_keys(db)
    assert listed[0].key is None and listed[0].last_used_at is not None
    api_keys.delete(db, k.id)
    assert not api_keys.verify(db, k.key)


def test_settings_migration_renames_old_keys():
    s = normalize_settings({"transcription.language": "nl", "ai.model": "ollama:x", "unknown.key": 1, "junk": 2})
    assert s["general.language"] == "nl" and s["models.ai"] == "ollama:x" and s["unknown.key"] == 1
    assert "junk" not in s and s["storage.maxBytes"] == 20 * 1024 ** 3
