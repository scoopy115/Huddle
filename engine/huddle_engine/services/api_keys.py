"""API keys for the network MCP server. Only a SHA-256 hash is stored; the plaintext
is returned once at creation. Keys look like ``hud_<40 chars>`` and expire after
30/60/90 days. Renewing keeps the same secret and extends the expiry by the original
validity — convenient, but rotating (create new, delete old) is the safer habit."""
from __future__ import annotations

import hashlib
import secrets
import time

from ..db import Database
from ..schemas import ApiKey

DAY = 86400


def _hash(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def _row(r, key: str | None = None) -> ApiKey:
    return ApiKey(id=r["id"], name=r["name"], prefix=r["prefix"], created_at=r["created_at"], last_used_at=r["last_used_at"],
                  expires_at=r["expires_at"], validity_days=r["validity_days"],
                  expired=bool(r["expires_at"] and r["expires_at"] < time.time()), key=key)


def create(db: Database, name: str, validity_days: int = 30) -> ApiKey:
    validity_days = validity_days if validity_days in (30, 60, 90) else 30
    key = "hud_" + secrets.token_urlsafe(30)
    now = time.time()
    cur = db.execute("INSERT INTO api_keys(name, key_hash, prefix, created_at, expires_at, validity_days) VALUES (?,?,?,?,?,?)",
                     (name.strip() or "Unnamed key", _hash(key), key[:12], now, now + validity_days * DAY, validity_days))
    return _row(db.one("SELECT * FROM api_keys WHERE id = ?", (cur.lastrowid,)), key)


def renew(db: Database, key_id: int) -> ApiKey | None:
    r = db.one("SELECT * FROM api_keys WHERE id = ?", (key_id,))
    if not r:
        return None
    db.execute("UPDATE api_keys SET expires_at = ? WHERE id = ?", (time.time() + (r["validity_days"] or 30) * DAY, key_id))
    return _row(db.one("SELECT * FROM api_keys WHERE id = ?", (key_id,)))


def list_keys(db: Database) -> list[ApiKey]:
    return [_row(r) for r in db.query("SELECT * FROM api_keys ORDER BY created_at")]


def delete(db: Database, key_id: int) -> None:
    db.execute("DELETE FROM api_keys WHERE id = ?", (key_id,))


def verify(db: Database, key: str | None) -> bool:
    if not key or not key.startswith("hud_"):
        return False
    row = db.one("SELECT id, expires_at FROM api_keys WHERE key_hash = ?", (_hash(key),))
    if not row or (row["expires_at"] and row["expires_at"] < time.time()):
        return False
    db.execute("UPDATE api_keys SET last_used_at = ? WHERE id = ?", (time.time(), row["id"]))
    return True
