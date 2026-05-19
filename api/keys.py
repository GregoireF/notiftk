"""
Self-service API key management for NotiTK.

Keys are stored as SHA-256 hashes in the same SQLite database as webhooks.
The raw key is returned once at generation time and cannot be recovered later.

Key format: sk_live_<48 random hex chars>  (192-bit entropy)

Limits (env vars):
  MAX_KEYS         — global active key cap (default: 1000)
  MAX_KEYS_PER_IP  — max keys a single IP can generate per 24 h (default: 3)
  ADMIN_SECRET     — required value for X-Admin-Secret header (unset = admin endpoints disabled)

Public API:
  is_db_key_valid(key)          → bool   — used by auth.py
  generate_key(ip, label=None)  → (id, raw_key) — raises ValueError on limit exceeded
  list_keys()                   → list[dict]
  revoke_key(key_id)            → bool
"""

import hashlib
import os
import secrets
import sqlite3
import time
from pathlib import Path

DB_PATH = Path(os.getenv("WEBHOOKS_DB", "data/webhooks.db"))
MAX_KEYS = int(os.getenv("MAX_KEYS", "1000"))
MAX_KEYS_PER_IP = int(os.getenv("MAX_KEYS_PER_IP", "3"))
ADMIN_SECRET: str = os.getenv("ADMIN_SECRET", "")

# ip → list of generation timestamps within the last 24 h
_gen_log: dict[str, list[float]] = {}


def _db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS api_keys (
            id         TEXT PRIMARY KEY,
            key_hash   TEXT UNIQUE NOT NULL,
            label      TEXT,
            created_at REAL NOT NULL,
            is_active  INTEGER NOT NULL DEFAULT 1
        )
    """)
    conn.commit()
    return conn


def _hash(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def is_db_key_valid(key: str) -> bool:
    """Return True if *key* exists in the DB and is active."""
    with _db() as conn:
        row = conn.execute(
            "SELECT 1 FROM api_keys WHERE key_hash = ? AND is_active = 1",
            [_hash(key)],
        ).fetchone()
    return row is not None


def generate_key(ip: str, label: str | None = None) -> tuple[str, str]:
    """
    Generate a new key for *ip*.  Returns (key_id, raw_key).
    Raises ValueError when per-IP or global limit is exceeded.
    """
    now = time.time()
    window = now - 86400  # 24 h

    timestamps = [t for t in _gen_log.get(ip, []) if t > window]
    _gen_log[ip] = timestamps
    if len(timestamps) >= MAX_KEYS_PER_IP:
        raise ValueError(f"Limite atteinte : max {MAX_KEYS_PER_IP} clés par IP toutes les 24 h.")

    with _db() as conn:
        total = conn.execute("SELECT COUNT(*) FROM api_keys WHERE is_active = 1").fetchone()[0]
    if total >= MAX_KEYS:
        raise ValueError("Capacité maximale atteinte. Contactez l'administrateur.")

    raw = "sk_live_" + secrets.token_hex(24)
    key_id = secrets.token_hex(8)

    with _db() as conn:
        conn.execute(
            "INSERT INTO api_keys (id, key_hash, label, created_at) VALUES (?, ?, ?, ?)",
            (key_id, _hash(raw), label, now),
        )

    _gen_log.setdefault(ip, []).append(now)
    return key_id, raw


def list_keys() -> list[dict]:
    with _db() as conn:
        rows = conn.execute(
            "SELECT id, label, created_at, is_active FROM api_keys ORDER BY created_at DESC"
        ).fetchall()
    return [{"id": r[0], "label": r[1], "created_at": r[2], "is_active": bool(r[3])} for r in rows]


def revoke_key(key_id: str) -> bool:
    with _db() as conn:
        conn.execute("UPDATE api_keys SET is_active = 0 WHERE id = ?", [key_id])
        return conn.total_changes > 0
