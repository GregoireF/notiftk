"""
Self-service API key management for NotiTFK.

Keys are stored as SHA-256 hashes in the same SQLite database as webhooks.
The raw key is returned once at generation time and cannot be recovered later.

Key format: sk_live_<48 random hex chars>  (192-bit entropy)

Limits (env vars):
  MAX_KEYS         — global active key cap (default: 1000)
  MAX_KEYS_PER_IP  — max keys a single IP can generate per 24 h (default: 3)
  ADMIN_SECRET     — required value for X-Admin-Secret header (unset = admin endpoints disabled)
  KEY_INVITE_CODE  — if set, POST /api/keys requires ?invite=<code> (default: unset = open)

Public API:
  is_db_key_valid(key)                           → bool   — used by auth.py
  generate_key(ip, label=None, expires_in=None)  → (id, raw_key, expires_at) — raises ValueError on limit exceeded
  list_keys()                                    → list[dict]
  revoke_key(key_id)                             → bool
"""

import hashlib
import os
import secrets
import sqlite3
import threading
import time
from pathlib import Path


class KeyValidationError(ValueError):
    """Bad input (label too long, invalid expires_in). Maps to HTTP 422."""


class KeyRateLimitError(ValueError):
    """Per-IP or global cap reached. Maps to HTTP 429."""


DB_PATH = Path(os.getenv("WEBHOOKS_DB", "data/webhooks.db"))
MAX_KEYS = int(os.getenv("MAX_KEYS", "1000"))
MAX_KEYS_PER_IP = int(os.getenv("MAX_KEYS_PER_IP", "3"))
MAX_LABEL_LEN = 100
MAX_EXPIRES_IN = 365 * 24 * 3600  # 1 year ceiling
ADMIN_SECRET: str = os.getenv("ADMIN_SECRET", "")
KEY_INVITE_CODE: str = os.getenv("KEY_INVITE_CODE", "")

# ip → list of generation timestamps within the last 24 h
_gen_log: dict[str, list[float]] = {}
# Protects _gen_log from concurrent access when called via asyncio.to_thread.
_gen_log_lock = threading.Lock()


def _db() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS api_keys (
            id         TEXT PRIMARY KEY,
            key_hash   TEXT UNIQUE NOT NULL,
            label      TEXT,
            created_at REAL NOT NULL,
            is_active  INTEGER NOT NULL DEFAULT 1,
            expires_at REAL
        )
    """)
    # Migrate existing databases that predate the expires_at column.
    try:
        conn.execute("ALTER TABLE api_keys ADD COLUMN expires_at REAL")
    except sqlite3.OperationalError:
        pass  # column already exists
    conn.commit()
    return conn


def _hash(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def is_db_key_valid(key: str) -> bool:
    """Return True if *key* exists in the DB, is active, and has not expired."""
    now = time.time()
    with _db() as conn:
        row = conn.execute(
            "SELECT 1 FROM api_keys WHERE key_hash = ? AND is_active = 1"
            " AND (expires_at IS NULL OR expires_at > ?)",
            [_hash(key), now],
        ).fetchone()
    return row is not None


def generate_key(
    ip: str,
    label: str | None = None,
    expires_in: int | None = None,
) -> tuple[str, str, float | None]:
    """
    Generate a new key for *ip*.  Returns (key_id, raw_key, expires_at).
    expires_at is a Unix timestamp (float) or None if the key never expires.
    Raises ValueError when any limit is exceeded or inputs are invalid.
    """
    if label is not None and len(label) > MAX_LABEL_LEN:
        raise KeyValidationError(f"Label must not exceed {MAX_LABEL_LEN} characters.")

    if expires_in is not None:
        if expires_in <= 0:
            raise KeyValidationError("expires_in must be a positive number of seconds.")
        if expires_in > MAX_EXPIRES_IN:
            raise KeyValidationError(f"expires_in cannot exceed {MAX_EXPIRES_IN} seconds (1 year).")

    now = time.time()
    window = now - 86400  # 24 h

    with _gen_log_lock:
        timestamps = [t for t in _gen_log.get(ip, []) if t > window]
        _gen_log[ip] = timestamps
        if len(timestamps) >= MAX_KEYS_PER_IP:
            raise KeyRateLimitError(
                f"Limite atteinte : max {MAX_KEYS_PER_IP} clés par IP toutes les 24 h."
            )

        with _db() as conn:
            total = conn.execute("SELECT COUNT(*) FROM api_keys WHERE is_active = 1").fetchone()[0]
        if total >= MAX_KEYS:
            raise KeyRateLimitError("Capacité maximale atteinte. Contactez l'administrateur.")

        raw = "sk_live_" + secrets.token_hex(24)
        key_id = secrets.token_hex(8)
        expires_at = now + expires_in if expires_in is not None else None

        with _db() as conn:
            conn.execute(
                "INSERT INTO api_keys (id, key_hash, label, created_at, expires_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (key_id, _hash(raw), label, now, expires_at),
            )

        _gen_log.setdefault(ip, []).append(now)

    return key_id, raw, expires_at


def list_keys() -> list[dict]:
    with _db() as conn:
        rows = conn.execute(
            "SELECT id, label, created_at, is_active, expires_at"
            " FROM api_keys ORDER BY created_at DESC"
        ).fetchall()
    return [
        {
            "id": r[0],
            "label": r[1],
            "created_at": r[2],
            "is_active": bool(r[3]),
            "expires_at": r[4],
        }
        for r in rows
    ]


def revoke_key(key_id: str) -> bool:
    with _db() as conn:
        conn.execute("UPDATE api_keys SET is_active = 0 WHERE id = ?", [key_id])
        return conn.total_changes > 0
