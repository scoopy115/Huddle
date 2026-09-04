"""Processing stages. Each is a function ``stage(ctx) -> detail_str | None`` that
raises ``ProviderError`` (user-facing message + technical detail) on failure.
Stages only touch their own tables, so any one can be retried independently."""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf

from ..audio import TARGET_SR, decode_to_wav, write_playback_mix
from ..db import Database
from ..discovery.registry import Registry
from ..providers.base import ProviderError, Segment
from ..providers.diarization import DEFAULT_SHERPA_THRESHOLD, LEGACY_EMBEDDING_MODEL, SherpaDiarizationProvider
from ..providers.llm import ExtractiveProvider, OllamaProvider
from ..providers.summarize import infer_speaker_names, summarize
from ..providers.transcription import make_transcription_provider
from ..refine import html_to_text
from ..resolver import ResolverContext, resolve_llm, resolve_transcription
from ..services import meetings as ms
from ..services import transcripts
from ..settings import EngineConfig, resolve_notes_language

log = logging.getLogger(__name__)


@dataclass
class StageContext:
    db: Database
    cfg: EngineConfig
    registry: Registry
    settings: dict[str, Any]
    meeting_id: str
    memory_bytes: int | None = None
    progress: Any = None        # Callable[[float], None] — fraction 0..1 of the current stage
    cancelled: Any = None       # Callable[[], bool]

    def resolver(self) -> ResolverContext:
        return ResolverContext(registry=self.registry, settings=self.settings, memory_bytes=self.memory_bytes)

    def report(self, fraction: float) -> None:
        if self.progress:
            self.progress(fraction)

    def check_cancelled(self) -> None:
        if self.cancelled and self.cancelled():
            raise JobCancelled()


class JobCancelled(Exception):
    """The meeting was deleted (or processing was cancelled) while a stage ran."""


def _processed_wav(ctx: StageContext) -> Path:
    rec = ms.get_recording(ctx.db, ctx.meeting_id)
    if not rec or rec.status == "audio_deleted":
        raise ProviderError("The audio for this meeting is no longer available.")
    if rec.processed_path and Path(rec.processed_path).exists():
        return Path(rec.processed_path)
    raise ProviderError("The audio has not been prepared yet. Retry from the beginning.")


# ---- 1. preprocessing ------------------------------------------------------- #
def preprocessing(ctx: StageContext) -> str:
    rec = ms.get_recording(ctx.db, ctx.meeting_id)
    if not rec:
        raise ProviderError("No recording is attached to this meeting.")
    src = Path(rec.file_path)
    if not src.exists():
        raise ProviderError("The recording file could not be found.", detail=str(src))
    dst = ctx.cfg.recordings_dir / ctx.meeting_id / "processed.wav"
    try:
        duration, _ = decode_to_wav(src, dst)
        detail = ""
        sys_path = Path(rec.system_file_path) if rec.system_file_path else None
        if sys_path and sys_path.exists() and sys_path.stat().st_size > 44:
            duration = _mix_in(dst, sys_path)
            detail = " (microphone + system audio)"
            if src.suffix.lower() == ".wav":
                # what the player plays: both streams, at the microphone's sample rate
                write_playback_mix(src, sys_path, src.with_name("mix.wav"))
    except ProviderError:
        raise
    except Exception as e:
        raise ProviderError("The audio file could not be read. It may be corrupted or in an unsupported format.",
                            detail=repr(e)) from e
    if duration < 0.5:
        raise ProviderError("The recording is empty (less than half a second of audio).")
    ctx.db.execute("UPDATE recordings SET processed_path = ?, duration_sec = ?, status = 'processed' WHERE id = ?",
                   (str(dst), duration, rec.id))
    ctx.db.execute("UPDATE meetings SET duration_sec = ?, ended_at = started_at + ? WHERE id = ?",
                   (duration, duration, ctx.meeting_id))
    return f"{duration / 60:.1f} min of audio prepared{detail}"


def _mix_in(processed: Path, system_wav: Path) -> float:
    """Mix a second (desktop/system) stream into the processed 16 kHz mono file."""
    from faster_whisper.audio import decode_audio
    mic, _ = sf.read(str(processed), dtype="float32")
    other = np.asarray(decode_audio(str(system_wav), sampling_rate=TARGET_SR), dtype=np.float32)
    if other.ndim > 1:
        other = other.mean(axis=1)
    n = max(len(mic), len(other))
    mix = np.zeros(n, dtype=np.float32)
    mix[: len(mic)] += mic
    mix[: len(other)] += other
    peak = float(np.max(np.abs(mix))) or 1.0
    if peak > 0.99:
        mix /= peak
    sf.write(str(processed), mix, TARGET_SR, subtype="PCM_16")
    return n / TARGET_SR


# ---- 2. transcribing -------------------------------------------------------- #
def transcribing(ctx: StageContext) -> str:
    from ..live import live_segments_for
    from ..providers.transcription import Cancelled, split_at_pauses

    wav = _processed_wav(ctx)
    res = resolve_transcription(ctx.resolver())
    if res.status != "ready" or not res.model:
        raise ProviderError("No Whisper model is installed. Download one under Settings → Models, then retry.",
                            detail=res.reason)
    vocab = ctx.settings.get("transcription.vocabulary") or []
    provider = make_transcription_provider(res.model, vocab, ctx.settings.get("general.computeDevice", "auto"))
    row = ctx.db.one("SELECT language_override FROM meetings WHERE id = ?", (ctx.meeting_id,))
    language = (row["language_override"] if row and row["language_override"] else None) or "auto"

    # Reuse what was transcribed live while recording; only the tail is new work.
    rec = ms.get_recording(ctx.db, ctx.meeting_id)
    live, covered = live_segments_for(ctx.db, ctx.meeting_id) if rec else ([], 0.0)
    if language != "auto" and not (live and all(getattr(seg, "language", None) == language for seg in live)):
        live, covered = [], 0.0            # forced language the live pass did not use: redo everything
    duration = rec.duration_sec or 0.0 if rec else 0.0
    if live and duration and covered >= duration - 2.0:
        segments, lang_list = live, _language_list(live)
        ctx.report(1.0)
        reused = "live transcript reused"
    else:
        try:
            result = provider.transcribe(str(wav), language=None if language == "auto" else language,
                                         progress=ctx.report, cancelled=ctx.cancelled, start_sec=covered if live else 0.0)
        except Cancelled:
            raise JobCancelled()
        segments = list(live) + result.segments if live else result.segments
        if live:
            segments = split_at_pauses(segments)
        lang_list = _language_list(segments) if live else result.language
        reused = f"{len(live)} live segments reused" if live else ""
    if not segments:
        raise ProviderError("No speech was detected in the recording.")
    ctx.check_cancelled()

    with ctx.db.tx() as c:
        c.execute("DELETE FROM transcript_segments WHERE meeting_id = ?", (ctx.meeting_id,))
        c.execute("DELETE FROM meeting_speakers WHERE meeting_id = ?", (ctx.meeting_id,))
        for i, s in enumerate(segments):
            cur = c.execute("INSERT INTO transcript_segments(meeting_id, idx, start, \"end\", text, confidence, language)"
                            " VALUES (?,?,?,?,?,?,?)", (ctx.meeting_id, i, s.start, s.end, s.text, s.confidence, s.language))
            sid = cur.lastrowid
            if s.words:
                c.executemany("INSERT INTO transcript_words(segment_id, start, \"end\", word, confidence) VALUES (?,?,?,?,?)",
                              [(sid, w.start, w.end, w.word, w.confidence) for w in s.words])
        c.execute("UPDATE meetings SET language = ? WHERE id = ?", (lang_list, ctx.meeting_id))
        c.execute("DELETE FROM live_segments WHERE recording_id = ?", (ctx.meeting_id,))
    return f"{len(segments)} segments · {(lang_list or '?').upper()}" + (f" · {reused}" if reused else "")


def _language_list(segments) -> str | None:
    time_by: dict[str, float] = {}
    for s in segments:
        if s.language:
            time_by[s.language] = time_by.get(s.language, 0.0) + (s.end - s.start)
    total = sum(time_by.values()) or 1.0
    ordered = [lang for lang, t in sorted(time_by.items(), key=lambda kv: -kv[1]) if t / total >= 0.10]
    return ",".join(ordered) if ordered else None


# ---- 3. diarizing ----------------------------------------------------------- #
def diarizing(ctx: StageContext) -> str:
    """Window-level speaker separation. Segments may be split where the speaker changes,
    so the transcript table is rewritten (words included). Runs before summarising, so
    evidence references are created against the final segment ids."""
    segs = transcripts.segments(ctx.db, ctx.meeting_id, with_words=True)
    if not segs:
        raise ProviderError("There is no transcript to detect speakers in yet.")
    if not ctx.settings.get("speakers.diarization", True):
        _single_speaker(ctx, segs)
        return "Speaker detection is disabled — one speaker assumed"
    wav = _processed_wav(ctx)
    meeting_row = ms.get_meeting(ctx.db, ctx.meeting_id)
    provider = _diarization_provider(ctx, meeting_row.speaker_count_hint if meeting_row else None)
    from ..providers.base import Word
    plain = [Segment(start=s.start, end=s.end, text=s.text, confidence=s.confidence, language=s.language,
                     words=[Word(w.start, w.end, w.word, w.confidence) for w in (s.words or [])]) for s in segs]
    from ..providers.diarization import _Cancelled
    try:
        result = provider.diarize(str(wav), plain, progress=ctx.report, cancelled=ctx.cancelled)
    except _Cancelled:
        raise JobCancelled()
    ctx.check_cancelled()
    order: dict[str, int] = {}
    for d in result.segments:
        order.setdefault(d.label, len(order))
    with ctx.db.tx() as c:
        c.execute("DELETE FROM transcript_segments WHERE meeting_id = ?", (ctx.meeting_id,))
        c.execute("DELETE FROM meeting_speakers WHERE meeting_id = ?", (ctx.meeting_id,))
        ids: dict[str, int] = {}
        for lab, idx in order.items():
            emb = result.embeddings.get(lab)
            cur = c.execute("INSERT INTO meeting_speakers(meeting_id, label, embedding, embedding_model, color_index) VALUES (?,?,?,?,?)",
                            (ctx.meeting_id, lab, json.dumps(emb) if emb else None, result.embedding_model if emb else None, idx))
            ids[lab] = int(cur.lastrowid)
        for i, d in enumerate(result.segments):
            s = d.segment
            cur = c.execute("INSERT INTO transcript_segments(meeting_id, meeting_speaker_id, idx, start, \"end\", text, confidence, language)"
                            " VALUES (?,?,?,?,?,?,?,?)", (ctx.meeting_id, ids[d.label], i, s.start, s.end, s.text, s.confidence, s.language))
            if s.words:
                c.executemany("INSERT INTO transcript_words(segment_id, start, \"end\", word, confidence) VALUES (?,?,?,?,?)",
                              [(cur.lastrowid, w.start, w.end, w.word, w.confidence) for w in s.words])
    splits = len(result.segments) - len(segs)
    # The UI shows only the count; the rest is diagnostics for the log.
    log.info("[%s] diarization: %d speakers, %d splits, provider %s%s", ctx.meeting_id, len(order), splits, result.provider,
             (" · " + "; ".join(result.notes)) if result.notes else "")
    return f"{len(order)} speaker{'s' if len(order) != 1 else ''} detected"


def _diarization_provider(ctx: StageContext, speaker_count: int | None) -> SherpaDiarizationProvider:
    """sherpa-onnx (pyannote segmentation + TitaNet embeddings). The packaged app ships the models;
    a development checkout fetches them once. Without them the stage fails with a clear message
    and can be retried."""
    from ..providers import speaker_models as spk

    if not spk.sherpa_available():
        raise ProviderError("Speaker separation is not available in this build (sherpa-onnx missing).")
    try:
        paths = spk.ensure_speaker_models(ctx.cfg.models_dir, progress=lambda f: ctx.report(0.04 * f), cancelled=ctx.cancelled)
    except spk._Cancelled:
        raise JobCancelled() from None
    except Exception as e:  # offline, disk full, corrupt download
        raise ProviderError("The speaker models could not be downloaded. Check the internet connection and retry this step.",
                            detail=repr(e)) from e
    return SherpaDiarizationProvider(str(paths.segmentation), str(paths.embedding), paths.embedding_id,
                                     threshold=float(ctx.settings.get("speakers.similarityThreshold", DEFAULT_SHERPA_THRESHOLD)),
                                     speaker_count=speaker_count, threads=max(2, (os.cpu_count() or 4) // 2))


def _single_speaker(ctx: StageContext, segs) -> None:
    with ctx.db.tx() as c:
        c.execute("DELETE FROM meeting_speakers WHERE meeting_id = ?", (ctx.meeting_id,))
        cur = c.execute("INSERT INTO meeting_speakers(meeting_id, label, color_index) VALUES (?,?,0)",
                        (ctx.meeting_id, "Speaker 1"))
        c.execute("UPDATE transcript_segments SET meeting_speaker_id = ? WHERE meeting_id = ?",
                  (int(cur.lastrowid), ctx.meeting_id))


# ---- 4. identifying speakers ------------------------------------------------ #
def identifying_speakers(ctx: StageContext) -> str:
    """Suggest known speakers for each cluster by voice. Never assigns silently (spec §15)."""
    from ..services.voices import match

    if not ctx.settings.get("speakers.recognition", True):
        return "Speaker recognition is disabled"
    known = [{"id": r["id"], "name": r["name"], "embedding": json.loads(r["embedding"]),
              "model": r["embedding_model"] or LEGACY_EMBEDDING_MODEL}
             for r in ctx.db.query("SELECT id, name, embedding, embedding_model FROM speakers WHERE embedding IS NOT NULL")]
    if not known:
        return "No known voices yet — name a speaker to start recognising voices"
    rows = ctx.db.query("SELECT id, embedding, embedding_model FROM meeting_speakers WHERE meeting_id = ? AND embedding IS NOT NULL"
                        " AND speaker_id IS NULL", (ctx.meeting_id,))
    n = 0
    for r in rows:
        model = r["embedding_model"] or LEGACY_EMBEDDING_MODEL
        emb = json.loads(r["embedding"])
        # only compare like with like: same embedding model, same dimensionality
        candidates = [k for k in known if k["model"] == model and len(k["embedding"]) == len(emb)]
        if not candidates:
            continue
        threshold = float(ctx.settings.get("speakers.matchThreshold", 0.75)) if model == LEGACY_EMBEDDING_MODEL \
            else float(ctx.settings.get("speakers.sherpaMatchThreshold", 0.6))
        hit = match(emb, candidates, threshold=threshold)
        if hit:
            name, score = hit
            sid = next(k["id"] for k in known if k["name"] == name)
            ctx.db.execute("UPDATE meeting_speakers SET suggested_speaker_id = ?, suggested_confidence = ? WHERE id = ?",
                           (sid, round(float(score), 3), r["id"]))
            n += 1
    return f"{n} possible match{'es' if n != 1 else ''} suggested" if n else "No known voices recognised"


# ---- 5. summarizing --------------------------------------------------------- #
def _llm_provider(ctx: StageContext):
    res = resolve_llm(ctx.resolver())
    if res.status == "ready" and res.model:
        return OllamaProvider(res.model.name), res
    if res.status == "unavailable" and res.model:
        raise ProviderError(res.reason)
    return ExtractiveProvider(), res


def _plain_segments(ctx: StageContext):
    segs = transcripts.segments(ctx.db, ctx.meeting_id)
    label_of = {s.id: s.label for s in transcripts.speakers(ctx.db, ctx.meeting_id)}
    plain = [Segment(start=s.start, end=s.end, text=s.text, speaker_label=label_of.get(s.meeting_speaker_id),
                     language=s.language) for s in segs]
    return segs, plain


def _apply_inferred_names(ctx: StageContext, provider, plain: list[Segment]) -> int:
    """Name clusters that are addressed by name in the conversation. Only touches
    clusters the user has not named; the user can always override in the UI."""
    if not ctx.settings.get("speakers.inferNames", True):
        return 0
    speakers = transcripts.speakers(ctx.db, ctx.meeting_id)
    labels = {s.label: (s.display_name or s.speaker_name or s.label) for s in speakers}
    inferred = infer_speaker_names(provider, plain, labels)
    n = 0
    for s in speakers:
        if s.label in inferred and not s.display_name and not s.speaker_id:
            name, _conf = inferred[s.label]
            ctx.db.execute("UPDATE meeting_speakers SET display_name = ?, name_source = 'inferred' WHERE id = ?", (name, s.id))
            n += 1
    return n


def summarizing(ctx: StageContext) -> str:
    segs, plain = _plain_segments(ctx)
    if not segs:
        raise ProviderError("There is no transcript to summarise yet.")
    provider, res = _llm_provider(ctx)
    ctx.report(0.1)
    inferred = _apply_inferred_names(ctx, provider, plain)
    ctx.report(0.3)
    ctx.check_cancelled()
    names = transcripts.speaker_names(ctx.db, ctx.meeting_id)
    meeting = ms.get_meeting(ctx.db, ctx.meeting_id)
    date = datetime.fromtimestamp(meeting.started_at).strftime("%Y-%m-%d (%A)")
    notes = summarize(provider, plain, names, meeting_date=date,
                      language_hint=(meeting.language or "").replace(",", " + ") or None,
                      notes_language=resolve_notes_language(ctx.settings),
                      include_actions=bool(ctx.settings.get("notes.autoActionItems", False)),
                      user_context=html_to_text(meeting.context_html))

    ctx.report(0.9)
    ctx.check_cancelled()
    # A meeting that still has its automatic date/time name gets the model's title.
    renamed = ""
    if notes.title and ms.is_default_title(meeting.title):
        ms.update_meeting(ctx.db, ctx.meeting_id, title=notes.title)
        renamed = f" · named “{notes.title}”"
    seg_ids = [s.id for s in segs]
    now = time.time()
    with ctx.db.tx() as c:
        c.execute("INSERT INTO summaries(meeting_id, summary, provider, model, raw_json, created_at) VALUES (?,?,?,?,?,?)"
                  " ON CONFLICT(meeting_id) DO UPDATE SET summary=excluded.summary, provider=excluded.provider,"
                  " model=excluded.model, raw_json=excluded.raw_json, created_at=excluded.created_at",
                  (ctx.meeting_id, notes.summary, notes.provider, notes.model, notes.raw, now))
        c.execute("DELETE FROM topics WHERE meeting_id = ?", (ctx.meeting_id,))
        c.executemany("INSERT INTO topics(meeting_id, position, title, summary) VALUES (?,?,?,?)",
                      [(ctx.meeting_id, i, t.title, t.summary) for i, t in enumerate(notes.topics)])
        c.execute("DELETE FROM decisions WHERE meeting_id = ?", (ctx.meeting_id,))
        c.executemany("INSERT INTO decisions(meeting_id, position, text, evidence_start, evidence_end, segment_id)"
                      " VALUES (?,?,?,?,?,?)",
                      [(ctx.meeting_id, i, d.text, d.evidence.start, d.evidence.end,
                        seg_ids[d.evidence.segment_idx] if d.evidence.segment_idx is not None else None)
                       for i, d in enumerate(notes.decisions)])
        prev_done = {(r["text"] or "").strip().lower(): r["done"]
                     for r in c.execute("SELECT text, done FROM action_items WHERE meeting_id = ?", (ctx.meeting_id,))}
        if ctx.settings.get("notes.autoActionItems", False):
            c.execute("DELETE FROM action_items WHERE meeting_id = ? AND source = 'auto'", (ctx.meeting_id,))
        c.executemany("INSERT INTO action_items(meeting_id, position, text, owner, due_date, confidence, evidence_start,"
                      " evidence_end, segment_id, done, source, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,'auto',?)",
                      [(ctx.meeting_id, i, a.text, a.owner, a.due_date, a.confidence, a.evidence.start, a.evidence.end,
                        seg_ids[a.evidence.segment_idx] if a.evidence.segment_idx is not None else None,
                        prev_done.get(a.text.strip().lower(), 0), now) for i, a in enumerate(notes.action_items)])
    extra = f" · {inferred} speaker{'s' if inferred != 1 else ''} named from the conversation" if inferred else ""
    if notes.provider == "extractive":
        return "Built-in notes (no AI model in Ollama)" + extra
    return f"{res.model.name if res.model else notes.model}{extra}" + renamed


# ---- 6. indexing ------------------------------------------------------------ #
def indexing(ctx: StageContext) -> str:
    n = ctx.db.one("SELECT COUNT(*) AS n FROM transcript_segments WHERE meeting_id = ?", (ctx.meeting_id,))["n"]
    return f"{n} segments searchable"


# ---- 5b. refining (on demand) ------------------------------------------------ #
def refining(ctx: StageContext) -> str:
    """Apply the user's feedback to the transcript: speaker renames and misheard words, both
    derived by the model from the feedback and applied deterministically. The feedback itself
    stays on the meeting and is fed to every later summary/action-item pass as context."""
    from ..refine import apply_replacements, derive_corrections

    meeting = ms.get_meeting(ctx.db, ctx.meeting_id)
    feedback = html_to_text(meeting.context_html if meeting else None)
    if not feedback:
        return "No feedback given"
    segs, plain = _plain_segments(ctx)
    if not segs:
        raise ProviderError("There is no transcript to refine yet.")
    provider, _res = _llm_provider(ctx)
    from ..providers.llm import ExtractiveProvider
    if isinstance(provider, ExtractiveProvider):
        raise ProviderError("Refining needs an AI model. Set one up under Settings → Models.")
    ctx.report(0.05)
    speakers = transcripts.speakers(ctx.db, ctx.meeting_id)
    names = transcripts.speaker_names(ctx.db, ctx.meeting_id)
    listed = sorted({n for n in names.values() if n})
    from ..providers.summarize import render_transcript
    corr = derive_corrections(provider, feedback, listed, render_transcript(plain, names))
    ctx.report(0.6)
    ctx.check_cancelled()

    renamed = 0
    for src, dst in corr.renames:
        target = next((s for s in speakers if src.lower() in {(s.display_name or "").lower(), (s.speaker_name or "").lower(), s.label.lower()}), None)
        if target and (target.display_name or target.speaker_name or target.label) != dst:
            transcripts.rename_speaker(ctx.db, target.id, dst, enroll=True)
            renamed += 1
    ctx.report(0.7)

    replaced = 0
    if corr.replacements:
        with ctx.db.tx() as c:
            for s in segs:
                new, n = apply_replacements(s.text, corr.replacements)
                if n:
                    c.execute("UPDATE transcript_segments SET text = ? WHERE id = ?", (new, s.id))
                    replaced += n
            # single-word corrections also fix the word table (used for speaker splits and seeking)
            for find, repl in corr.replacements:
                if " " not in find and " " not in repl:
                    c.execute("UPDATE transcript_words SET word = ? WHERE segment_id IN (SELECT id FROM transcript_segments WHERE meeting_id = ?)"
                              " AND lower(trim(word, ' ,.;:!?')) = lower(?)", (repl, ctx.meeting_id, find))
    ctx.report(1.0)
    parts = []
    if renamed:
        parts.append(f"{renamed} speaker{'s' if renamed != 1 else ''} renamed")
    if replaced:
        parts.append(f"{replaced} word{'s' if replaced != 1 else ''} corrected")
    parts.append("notes rewritten with your context" if corr.context or not parts else "")
    return " · ".join(p for p in parts if p)


# ---- 6b. extracting action items (on demand) ----------------------------------- #
def extracting_actions(ctx: StageContext) -> str:
    """Action items, chunk by chunk: each chunk's items are stored as soon as the model returns
    them, so the list fills while the stage is still running. Replaces previous auto items,
    keeps manual ones and the done-state of unchanged texts."""
    from ..providers.summarize import iter_action_items
    segs, plain = _plain_segments(ctx)
    if not segs:
        raise ProviderError("There is no transcript yet.")
    provider, _res = _llm_provider(ctx)
    names = transcripts.speaker_names(ctx.db, ctx.meeting_id)
    meeting = ms.get_meeting(ctx.db, ctx.meeting_id)
    date = datetime.fromtimestamp(meeting.started_at).strftime("%Y-%m-%d (%A)")
    seg_ids = [s.id for s in segs]
    now = time.time()
    with ctx.db.tx() as c:
        prev_done = {(r["text"] or "").strip().lower(): r["done"]
                     for r in c.execute("SELECT text, done FROM action_items WHERE meeting_id = ?", (ctx.meeting_id,))}
        c.execute("DELETE FROM action_items WHERE meeting_id = ? AND source = 'auto'", (ctx.meeting_id,))
    total = 0
    for i, n_chunks, items in iter_action_items(provider, plain, names, meeting_date=date,
                                                notes_language=resolve_notes_language(ctx.settings),
                                                user_context=html_to_text(meeting.context_html)):
        ctx.check_cancelled()
        with ctx.db.tx() as c:
            c.executemany("INSERT INTO action_items(meeting_id, position, text, owner, due_date, confidence, evidence_start,"
                          " evidence_end, segment_id, done, source, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,'auto',?)",
                          [(ctx.meeting_id, total + k, a.text, a.owner, a.due_date, a.confidence, a.evidence.start, a.evidence.end,
                            seg_ids[a.evidence.segment_idx] if a.evidence.segment_idx is not None else None,
                            prev_done.get(a.text.strip().lower(), 0), now) for k, a in enumerate(items)])
        total += len(items)
        ctx.report((i + 1) / max(1, n_chunks))
    return f"{total} action item{'s' if total != 1 else ''} found"


STAGE_FUNCS = {
    "preprocessing": preprocessing,
    "transcribing": transcribing,
    "diarizing": diarizing,
    "identifying_speakers": identifying_speakers,
    "refining": refining,
    "summarizing": summarizing,
    "extracting_actions": extracting_actions,
    "indexing": indexing,
}

DOWNSTREAM = {
    "preprocessing": ["transcribing", "diarizing", "identifying_speakers", "summarizing", "indexing"],
    "transcribing": ["diarizing", "identifying_speakers", "summarizing", "indexing"],
    "diarizing": ["identifying_speakers", "summarizing"],
    "identifying_speakers": [],
    "refining": ["summarizing", "indexing"],
    "summarizing": [],
    "extracting_actions": [],
    "indexing": [],
}
