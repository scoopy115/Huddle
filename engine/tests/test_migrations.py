import contextlib
import sqlite3

from huddle_engine.db import Database
from huddle_engine.db.migrations import LATEST_VERSION, MIGRATIONS, migrate


def test_fresh_db_reaches_latest_version(db):
    assert db.schema_version == LATEST_VERSION
    tables = {r["name"] for r in db.query("SELECT name FROM sqlite_master WHERE type='table'")}
    for t in ("meetings", "recordings", "speakers", "meeting_speakers", "transcript_segments", "transcript_words",
              "summaries", "topics", "decisions", "action_items", "embeddings", "processing_jobs", "models",
              "providers", "settings"):
        assert t in tables, t
    assert "segments_fts" in {r["name"] for r in db.query("SELECT name FROM sqlite_master")}


def test_migrate_is_idempotent(cfg):
    d1 = Database(cfg.db_path)
    v1 = d1.schema_version
    d1.close()
    d2 = Database(cfg.db_path)
    assert d2.schema_version == v1 == LATEST_VERSION
    d2.close()


def test_migrations_are_monotonic():
    versions = [v for v, _ in MIGRATIONS]
    assert versions == sorted(versions) and len(set(versions)) == len(versions)


def test_failed_migration_rolls_back(tmp_path):
    conn = sqlite3.connect(tmp_path / "x.db", isolation_level=None)
    import huddle_engine.db.migrations as m
    original = m.MIGRATIONS
    try:
        m.MIGRATIONS = [*original, (original[-1][0] + 1, "CREATE TABLE ok(x); CREATE TABLE broken(;")]
        with contextlib.suppress(sqlite3.OperationalError):
            migrate(conn)
        assert conn.execute("PRAGMA user_version").fetchone()[0] == original[-1][0]
        names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master")}
        assert "ok" not in names
    finally:
        m.MIGRATIONS = original
        conn.close()


def test_fts_triggers_keep_index_in_sync(db):
    import time
    db.execute("INSERT INTO meetings(id,title,created_at,started_at,status,source) VALUES ('m1','T',?,?,'ready','recorded')",
               (time.time(), time.time()))
    db.execute("INSERT INTO transcript_segments(meeting_id, idx, start, \"end\", text) VALUES ('m1',0,0,1,'de homepage kleuren')")
    assert db.query("SELECT rowid FROM segments_fts WHERE segments_fts MATCH '\"kleuren\"'")
    db.execute("UPDATE transcript_segments SET text='iets anders' WHERE idx=0")
    assert not db.query("SELECT rowid FROM segments_fts WHERE segments_fts MATCH '\"kleuren\"'")
    assert db.query("SELECT rowid FROM segments_fts WHERE segments_fts MATCH '\"anders\"'")
    db.execute("DELETE FROM meetings WHERE id='m1'")
    assert not db.query("SELECT rowid FROM segments_fts WHERE segments_fts MATCH '\"anders\"'")
