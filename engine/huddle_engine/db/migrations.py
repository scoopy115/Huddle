"""Versioned schema migrations. ``PRAGMA user_version`` tracks the applied version.

Rules: never edit an applied migration; append a new one. Each entry is a full
SQL script executed inside one transaction.
"""
from __future__ import annotations

import sqlite3

MIGRATIONS: list[tuple[int, str]] = [
    (1, """
    CREATE TABLE meetings (
        id            TEXT PRIMARY KEY,
        title         TEXT NOT NULL,
        created_at    REAL NOT NULL,
        started_at    REAL NOT NULL,
        ended_at      REAL,
        duration_sec  REAL,
        language      TEXT,
        status        TEXT NOT NULL DEFAULT 'saved',   -- recording|saved|processing|ready|failed
        source        TEXT NOT NULL DEFAULT 'recorded', -- recorded|imported|recovered
        notes         TEXT
    );

    CREATE TABLE recordings (
        id             TEXT PRIMARY KEY,
        meeting_id     TEXT NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
        file_path      TEXT NOT NULL,
        processed_path TEXT,
        format         TEXT,
        sample_rate    INTEGER,
        channels       INTEGER,
        duration_sec   REAL,
        size_bytes     INTEGER,
        input_device   TEXT,
        started_at     REAL,
        status         TEXT NOT NULL DEFAULT 'saved'
    );
    CREATE INDEX idx_recordings_meeting ON recordings(meeting_id);

    -- Known (named) speakers with a running-mean voice embedding. Local only.
    CREATE TABLE speakers (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        name        TEXT NOT NULL UNIQUE,
        embedding   TEXT,                 -- json list[float]
        n_samples   INTEGER NOT NULL DEFAULT 0,
        created_at  REAL NOT NULL,
        updated_at  REAL NOT NULL
    );

    -- Per-meeting diarization clusters ("Speaker 1"…) and how they map to people.
    CREATE TABLE meeting_speakers (
        id                    INTEGER PRIMARY KEY AUTOINCREMENT,
        meeting_id            TEXT NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
        label                 TEXT NOT NULL,          -- raw cluster label
        display_name          TEXT,                   -- user-assigned name (may differ from speaker)
        speaker_id            INTEGER REFERENCES speakers(id) ON DELETE SET NULL,
        embedding             TEXT,                   -- json list[float]
        suggested_speaker_id  INTEGER REFERENCES speakers(id) ON DELETE SET NULL,
        suggested_confidence  REAL,
        color_index           INTEGER NOT NULL DEFAULT 0,
        UNIQUE(meeting_id, label)
    );

    CREATE TABLE transcript_segments (
        id                  INTEGER PRIMARY KEY AUTOINCREMENT,
        meeting_id          TEXT NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
        meeting_speaker_id  INTEGER REFERENCES meeting_speakers(id) ON DELETE SET NULL,
        idx                 INTEGER NOT NULL,
        start               REAL NOT NULL,
        "end"               REAL NOT NULL,
        text                TEXT NOT NULL,
        confidence          REAL
    );
    CREATE INDEX idx_segments_meeting ON transcript_segments(meeting_id, start);

    CREATE TABLE transcript_words (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        segment_id  INTEGER NOT NULL REFERENCES transcript_segments(id) ON DELETE CASCADE,
        start       REAL NOT NULL,
        "end"       REAL NOT NULL,
        word        TEXT NOT NULL,
        confidence  REAL
    );
    CREATE INDEX idx_words_segment ON transcript_words(segment_id);

    CREATE TABLE summaries (
        meeting_id  TEXT PRIMARY KEY REFERENCES meetings(id) ON DELETE CASCADE,
        summary     TEXT NOT NULL DEFAULT '',
        provider    TEXT,
        model       TEXT,
        raw_json    TEXT,
        created_at  REAL NOT NULL
    );

    CREATE TABLE topics (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        meeting_id  TEXT NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
        position    INTEGER NOT NULL DEFAULT 0,
        title       TEXT NOT NULL,
        summary     TEXT NOT NULL DEFAULT ''
    );
    CREATE INDEX idx_topics_meeting ON topics(meeting_id);

    CREATE TABLE decisions (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        meeting_id      TEXT NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
        position        INTEGER NOT NULL DEFAULT 0,
        text            TEXT NOT NULL,
        evidence_start  REAL,
        evidence_end    REAL,
        segment_id      INTEGER REFERENCES transcript_segments(id) ON DELETE SET NULL
    );
    CREATE INDEX idx_decisions_meeting ON decisions(meeting_id);

    CREATE TABLE action_items (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        meeting_id      TEXT NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
        position        INTEGER NOT NULL DEFAULT 0,
        text            TEXT NOT NULL,
        owner           TEXT,                        -- NULL when nobody was named
        due_date        TEXT,                        -- ISO date or NULL
        confidence      REAL,
        evidence_start  REAL,
        evidence_end    REAL,
        segment_id      INTEGER REFERENCES transcript_segments(id) ON DELETE SET NULL,
        done            INTEGER NOT NULL DEFAULT 0,
        source          TEXT NOT NULL DEFAULT 'auto', -- auto|manual
        created_at      REAL NOT NULL
    );
    CREATE INDEX idx_actions_meeting ON action_items(meeting_id);

    CREATE TABLE embeddings (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        meeting_id  TEXT NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
        segment_id  INTEGER REFERENCES transcript_segments(id) ON DELETE CASCADE,
        model       TEXT NOT NULL,
        dim         INTEGER NOT NULL,
        vector      BLOB NOT NULL
    );
    CREATE INDEX idx_embeddings_meeting ON embeddings(meeting_id);

    -- One job per meeting; per-stage state kept as JSON so stages can be retried individually.
    CREATE TABLE processing_jobs (
        meeting_id     TEXT PRIMARY KEY REFERENCES meetings(id) ON DELETE CASCADE,
        state          TEXT NOT NULL DEFAULT 'queued',  -- queued|running|ready|failed
        current_stage  TEXT,
        stages_json    TEXT NOT NULL DEFAULT '{}',
        error          TEXT,
        error_detail   TEXT,
        created_at     REAL NOT NULL,
        updated_at     REAL NOT NULL
    );

    CREATE TABLE models (
        id                   TEXT PRIMARY KEY,
        name                 TEXT NOT NULL,
        family               TEXT,
        task                 TEXT NOT NULL,           -- transcription|diarization|llm|embedding
        source               TEXT NOT NULL,           -- our_app|ollama|lm_studio|huggingface|whisper_cpp|...
        format               TEXT NOT NULL,
        quantization         TEXT,
        path                 TEXT,
        size_bytes           INTEGER,
        externally_managed   INTEGER NOT NULL DEFAULT 1,
        compatible_runtimes  TEXT NOT NULL DEFAULT '[]',
        meta_json            TEXT NOT NULL DEFAULT '{}',
        sha256               TEXT,
        discovered_at        REAL NOT NULL
    );

    CREATE TABLE providers (
        id          TEXT PRIMARY KEY,
        kind        TEXT NOT NULL,
        name        TEXT NOT NULL,
        status      TEXT NOT NULL,      -- available|installed_not_running|not_found|error
        detail_json TEXT NOT NULL DEFAULT '{}',
        checked_at  REAL NOT NULL
    );

    CREATE TABLE settings (
        key    TEXT PRIMARY KEY,
        value  TEXT NOT NULL             -- json
    );

    -- Full-text search over transcript text. unicode61 without stemming so Dutch and
    -- English behave the same; queries add prefix matching for recall.
    CREATE VIRTUAL TABLE segments_fts USING fts5(
        text, meeting_id UNINDEXED,
        content='transcript_segments', content_rowid='id',
        tokenize = 'unicode61 remove_diacritics 2'
    );
    CREATE TRIGGER segments_ai AFTER INSERT ON transcript_segments BEGIN
        INSERT INTO segments_fts(rowid, text, meeting_id) VALUES (new.id, new.text, new.meeting_id);
    END;
    CREATE TRIGGER segments_ad AFTER DELETE ON transcript_segments BEGIN
        INSERT INTO segments_fts(segments_fts, rowid, text, meeting_id)
        VALUES ('delete', old.id, old.text, old.meeting_id);
    END;
    CREATE TRIGGER segments_au AFTER UPDATE OF text ON transcript_segments BEGIN
        INSERT INTO segments_fts(segments_fts, rowid, text, meeting_id)
        VALUES ('delete', old.id, old.text, old.meeting_id);
        INSERT INTO segments_fts(rowid, text, meeting_id) VALUES (new.id, new.text, new.meeting_id);
    END;
    """),
    (2, """
    -- who named a diarization cluster: user | inferred (addressed by name in the transcript) | recognized (voice)
    ALTER TABLE meeting_speakers ADD COLUMN name_source TEXT;
    -- per-segment language (mixed NL/EN meetings); meetings.language becomes a comma list
    ALTER TABLE transcript_segments ADD COLUMN language TEXT;
    -- optional second stream (system/desktop audio) mixed into processed.wav
    ALTER TABLE recordings ADD COLUMN system_file_path TEXT;
    -- API keys for the network MCP server (hash only; plaintext shown once)
    CREATE TABLE api_keys (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        name          TEXT NOT NULL,
        key_hash      TEXT NOT NULL UNIQUE,
        prefix        TEXT NOT NULL,
        created_at    REAL NOT NULL,
        last_used_at  REAL
    );
    """),
    (3, """
    -- Segments transcribed while a recording is still running; reused by the final pass.
    CREATE TABLE live_segments (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        recording_id  TEXT NOT NULL,
        idx           INTEGER NOT NULL,
        start         REAL NOT NULL,
        "end"         REAL NOT NULL,
        text          TEXT NOT NULL,
        language      TEXT,
        confidence    REAL,
        words_json    TEXT NOT NULL DEFAULT '[]'
    );
    CREATE INDEX idx_live_recording ON live_segments(recording_id, idx);
    """),
]

MIGRATIONS.append((4, """
    -- user-chosen spoken language for the whole meeting (NULL = detect once, then force)
    ALTER TABLE meetings ADD COLUMN language_override TEXT;
    -- API keys expire (30/60/90 days) and can be renewed from the UI
    ALTER TABLE api_keys ADD COLUMN expires_at REAL;
    ALTER TABLE api_keys ADD COLUMN validity_days INTEGER NOT NULL DEFAULT 30;
    """))

MIGRATIONS.append((5, """
    -- voice embeddings are tagged with the model that produced them; vectors of different
    -- models are never compared (NULL = legacy profiles from before the sherpa models)
    ALTER TABLE speakers ADD COLUMN embedding_model TEXT;
    ALTER TABLE meeting_speakers ADD COLUMN embedding_model TEXT;
    -- optional user hint "N people spoke", used by speaker separation (NULL = automatic)
    ALTER TABLE meetings ADD COLUMN speaker_count_hint INTEGER;
    """))

MIGRATIONS.append((6, """
    -- rich-text feedback/context the user gave about a meeting; authoritative input for the notes
    ALTER TABLE meetings ADD COLUMN context_html TEXT;
    """))

LATEST_VERSION = MIGRATIONS[-1][0]


def migrate(conn: sqlite3.Connection) -> int:
    """Apply pending migrations. Returns the resulting schema version."""
    current = conn.execute("PRAGMA user_version").fetchone()[0]
    for version, script in MIGRATIONS:
        if version <= current:
            continue
        # executescript() commits any open transaction first, so the transaction
        # must live inside the script for atomicity.
        try:
            conn.executescript(f"BEGIN;\n{script}\nPRAGMA user_version = {version};\nCOMMIT;")
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        current = version
    return current
