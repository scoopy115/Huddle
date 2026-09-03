"""Live transcription while a recording is still running.

The desktop shell writes ``audio.wav`` progressively (header patched every second). A
``LiveSession`` wakes up every few seconds, decodes the audio that arrived since the
last pass, runs the same VAD → language → chunk → transcribe path as the final stage,
and stores the resulting segments in ``live_segments``. When the recording stops, the
final ``transcribing`` stage reuses those segments and only transcribes the tail, so
wrapping up a meeting takes seconds instead of the full transcription time.

Speech that is still "open" at the end of the available audio (no trailing silence yet)
is left for the next pass so we never cut a word in half.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path

import numpy as np

from .db import Database
from .providers.transcription import FasterWhisperProvider, _collect, group_regions, pick_language, split_at_pauses

log = logging.getLogger(__name__)
SR = 16000
TAIL_GUARD_SEC = 1.5      # leave this much of the newest audio for the next pass
POLL_SEC = 4.0


class LiveSession:
    def __init__(self, db: Database, recording_id: str, wav_path: str, provider: FasterWhisperProvider,
                 language: str | None):
        self.db = db
        self.recording_id = recording_id
        self.wav_path = Path(wav_path)
        self.provider = provider
        self.language = None if language in (None, "auto") else language
        self.processed_sec = 0.0
        self.idx = 0
        self.error: str | None = None
        self.state = "starting"
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, name=f"huddle-live-{recording_id}", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self, final: bool = True) -> None:
        """Ask the loop to finish. With `final`, one last pass consumes the remaining audio."""
        self._final = final
        self._stop.set()
        self._thread.join(timeout=600)

    # ---- worker ----------------------------------------------------------------- #
    def _read_new_audio(self) -> np.ndarray | None:
        import soundfile as sf
        try:
            info = sf.info(str(self.wav_path))
        except Exception:
            return None
        total = info.frames / info.samplerate
        if total - self.processed_sec < 3.0:
            return None
        start = int(self.processed_sec * info.samplerate)
        audio, sr = sf.read(str(self.wav_path), dtype="float32", start=start)
        if audio.ndim > 1:
            audio = audio.mean(axis=1)
        if sr != SR:
            from faster_whisper.audio import decode_audio  # noqa: F401  (resample via numpy for simplicity)
            ratio = SR / sr
            idx = (np.arange(int(len(audio) * ratio)) / ratio).astype(np.int64)
            audio = audio[np.clip(idx, 0, len(audio) - 1)]
        return audio

    def _loop(self) -> None:
        from faster_whisper.vad import VadOptions, get_speech_timestamps
        try:
            model, _t = self.provider.load()
            prompt = getattr(model, "_initial_prompt", None) if _t is None else _t._initial_prompt
            detector = self.provider._detector() if self.language is None else None
        except Exception as e:
            self.error = str(e)
            self.state = "failed"
            return
        self.state = "running"
        self._final = False
        while True:
            stopping = self._stop.is_set()
            audio = self._read_new_audio()
            if audio is not None and len(audio) > SR:
                try:
                    self._process(audio, model, detector, prompt, VadOptions, get_speech_timestamps,
                                  consume_all=stopping and self._final)
                except Exception as e:  # keep recording; live text is best-effort
                    log.exception("live transcription pass failed")
                    self.error = str(e)
            if stopping:
                break
            self._stop.wait(POLL_SEC)
        self.state = "stopped"

    def _process(self, audio, model, detector, prompt, VadOptions, get_speech_timestamps, consume_all: bool) -> None:
        avail = len(audio) / SR
        speech = get_speech_timestamps(audio, VadOptions(min_silence_duration_ms=300, speech_pad_ms=200))
        regions = [(s["start"] / SR, s["end"] / SR) for s in speech]
        if not consume_all:
            # only regions that are followed by silence before the tail guard are "closed"
            regions = [(a, b) for a, b in regions if b < avail - TAIL_GUARD_SEC]
        if not regions:
            if consume_all:
                self.processed_sec += avail
            return
        if self.language is None and detector is not None:
            # decide once, on the first pass with enough speech; keep it for the whole recording
            if sum(b - a for a, b in regions) >= 8.0:
                self.language = pick_language(detector, audio, regions, SR)
            else:
                return
        chunks = [(a, b, self.language) for a, b in group_regions(regions)]
        base = self.processed_sec
        segs = []
        for c0, c1, lang in chunks:
            clip = audio[int(c0 * SR):int(c1 * SR)]
            out, _ = model.transcribe(clip, language=lang, beam_size=5, vad_filter=False, word_timestamps=True,
                                      initial_prompt=prompt, condition_on_previous_text=False)
            for s in _collect(out, base + c0):
                s.language = lang
                segs.append(s)
        segs = split_at_pauses(segs)
        rows = []
        for s in segs:
            rows.append((self.recording_id, self.idx, s.start, s.end, s.text, s.language, s.confidence,
                         json.dumps([[w.start, w.end, w.word, w.confidence] for w in s.words])))
            self.idx += 1
        if rows:
            self.db.executemany("INSERT INTO live_segments(recording_id, idx, start, \"end\", text, language, confidence, words_json)"
                                " VALUES (?,?,?,?,?,?,?,?)", rows)
        # everything up to the end of the last consumed chunk is done
        self.processed_sec = base + (avail if consume_all else max(c1 for _, c1, _ in chunks))


class LiveManager:
    def __init__(self, db: Database):
        self.db = db
        self._sessions: dict[str, LiveSession] = {}
        self._lock = threading.Lock()

    def start(self, recording_id: str, wav_path: str, provider: FasterWhisperProvider, language: str | None) -> LiveSession:
        with self._lock:
            if recording_id in self._sessions:
                return self._sessions[recording_id]
            self.db.execute("DELETE FROM live_segments WHERE recording_id = ?", (recording_id,))
            s = LiveSession(self.db, recording_id, wav_path, provider, language)
            self._sessions[recording_id] = s
            s.start()
            return s

    def get(self, recording_id: str) -> LiveSession | None:
        return self._sessions.get(recording_id)

    def stop(self, recording_id: str, final: bool = True) -> LiveSession | None:
        with self._lock:
            s = self._sessions.pop(recording_id, None)
        if s:
            s.stop(final=final)
        return s

    def status(self, recording_id: str) -> dict:
        s = self._sessions.get(recording_id)
        rows = self.db.query("SELECT start, \"end\", text, language FROM live_segments WHERE recording_id = ? ORDER BY idx",
                             (recording_id,))
        return {"active": s is not None, "state": s.state if s else "stopped", "processedSec": s.processed_sec if s else 0.0,
                "error": s.error if s else None, "segmentCount": len(rows),
                "recent": [{"start": r["start"], "end": r["end"], "text": r["text"], "language": r["language"]} for r in rows[-4:]]}


def live_segments_for(db: Database, recording_id: str) -> tuple[list, float]:
    """Stored live segments (as provider Segments) and the time up to which they cover."""
    from .providers.base import Segment, Word
    rows = db.query("SELECT * FROM live_segments WHERE recording_id = ? ORDER BY idx", (recording_id,))
    segs = []
    for r in rows:
        words = [Word(start=w[0], end=w[1], word=w[2], confidence=w[3]) for w in json.loads(r["words_json"])]
        segs.append(Segment(start=r["start"], end=r["end"], text=r["text"], confidence=r["confidence"], words=words,
                            language=r["language"]))
    covered = max((s.end for s in segs), default=0.0)
    return segs, covered


def now() -> float:
    return time.time()
