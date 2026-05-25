"""
Webhook dispatch for NotiTFK.

Callers register a (username, callback_url) pair via register_webhook(). A
background asyncio task polls TikTok via get_live_status_sse and fires an HTTP
POST to the callback whenever is_live changes.

Optional HMAC-SHA256 signing: if `secret` is provided at registration, every
POST includes an `X-NotiTFK-Signature: sha256=<hex>` header so the receiver
can verify authenticity.

Security:
  Callback URLs are validated against a blocklist of private/loopback IP ranges
  (SSRF prevention). Hostname-based URLs are allowed but not DNS-resolved at
  registration time — ensure your callback host is publicly reachable.

Persistence:
  Webhooks survive server restarts via a SQLite database.
  Default path: data/webhooks.db (relative to the project root).
  Override with the WEBHOOKS_DB environment variable.
  On Fly.io, mount a persistent volume and set:
      fly secrets set WEBHOOKS_DB=/data/webhooks.db

Limits (configurable via env vars):
  MAX_WEBHOOKS       — global cap (default: 100)
  MAX_WEBHOOKS_PER_USER — per username cap (default: 5)

Ownership:
  Webhooks created with a key are bound to that key via owner_key_hash
  (SHA-256 of the raw key). List/delete filter to the caller's hash.
  Legacy webhooks with NULL owner_key_hash are accessible to all callers.
  Admin (via X-Admin-Secret) always sees and can delete all webhooks.

Public API:
  restore_webhooks()                                             — call on app startup
  shutdown_webhooks()                                            — call on app shutdown
  register_webhook(username, callback_url, secret, owner_key)   → watch_id (str)
  unregister_webhook(watch_id, owner_key)                        → bool (True = removed)
  list_webhooks(owner_key)                                       → list[_Watcher]
  list_all_webhooks()                                            → list[_Watcher] (admin)
  get_delivery_history(watch_id)                                 → list[dict] | None

Exceptions:
  WebhookLimitExceeded  — global or per-user cap reached  → HTTP 429
  WebhookURLError       — SSRF or invalid URL             → HTTP 422
"""

import asyncio
import hashlib
import hmac
import ipaddress
import logging
import os
import sqlite3
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

import httpx

from .models import LiveStatus
from .tiktok import get_live_status_sse, TikTokUserNotFound, TikTokAPIError, POLL_INTERVAL

logger = logging.getLogger(__name__)

DISPATCH_TIMEOUT = 10
MAX_DISPATCH_FAILURES = 5
DISPATCH_MAX_ATTEMPTS = 3   # per-event retry attempts before counting as a failure
DISPATCH_BACKOFF_BASE = 1   # seconds — waits 1s then 2s between retries
MAX_WEBHOOKS_TOTAL = int(os.getenv("MAX_WEBHOOKS", "100"))
MAX_WEBHOOKS_PER_USER = int(os.getenv("MAX_WEBHOOKS_PER_USER", "5"))

_MAX_DELIVERY_HISTORY = 20

_DB_PATH = Path(
    os.getenv("WEBHOOKS_DB", str(Path(__file__).parent.parent / "data" / "webhooks.db"))
)

# Private / reserved IP ranges blocked to prevent SSRF attacks.
_BLOCKED_NETWORKS = [
    ipaddress.ip_network(cidr)
    for cidr in (
        "127.0.0.0/8",  # loopback
        "10.0.0.0/8",  # RFC-1918 private
        "172.16.0.0/12",  # RFC-1918 private
        "192.168.0.0/16",  # RFC-1918 private
        "169.254.0.0/16",  # link-local / AWS EC2 metadata
        "100.64.0.0/10",  # shared address space (RFC-6598)
        "::1/128",  # IPv6 loopback
        "fc00::/7",  # IPv6 unique-local
        "fe80::/10",  # IPv6 link-local
    )
]
_BLOCKED_HOSTNAMES = frozenset({"localhost", "0.0.0.0", "[::]"})


# ── Custom exceptions ─────────────────────────────────────────────────────────


class WebhookLimitExceeded(ValueError):
    """Global or per-user webhook cap reached. Maps to HTTP 429."""


class WebhookURLError(ValueError):
    """Callback URL blocked (SSRF) or otherwise invalid. Maps to HTTP 422."""


# ── Data model ────────────────────────────────────────────────────────────────


def _hash_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


@dataclass
class _Watcher:
    watch_id: str
    username: str
    callback_url: str
    secret: str | None
    owner_key_hash: str | None = None
    task: asyncio.Task | None = field(default=None, repr=False)


@dataclass
class _DeliveryRecord:
    timestamp: float
    http_status: int | None
    attempt_count: int
    success: bool


# watch_id → _Watcher
_watchers: dict[str, _Watcher] = {}

# watch_id → ring buffer of last _MAX_DELIVERY_HISTORY delivery records
_delivery_history: dict[str, deque[_DeliveryRecord]] = {}


# ── SSRF guard ────────────────────────────────────────────────────────────────


def _assert_not_ssrf(url: str) -> None:
    """
    Block callback URLs targeting private/loopback addresses.

    Raises WebhookURLError if the URL resolves to a blocked range.
    Hostname-based URLs (not raw IPs) pass through — they are not DNS-resolved
    at registration time (DNS rebinding is out of scope for a self-hosted tool).
    """
    hostname = urlparse(url).hostname or ""
    if hostname.lower() in _BLOCKED_HOSTNAMES:
        raise WebhookURLError(f"Callback URL hostname '{hostname}' is not allowed.")
    try:
        addr = ipaddress.ip_address(hostname)
        for net in _BLOCKED_NETWORKS:
            if addr in net:
                raise WebhookURLError(
                    f"Callback URL must point to a public IP address (got '{hostname}')."
                )
    except ValueError as exc:
        # "does not appear to be an IPv4 or IPv6 address" — it's a hostname, not an IP.
        if isinstance(exc, WebhookURLError):
            raise


# ── Delivery history ──────────────────────────────────────────────────────────


def _record_delivery(watch_id: str, http_status: int | None, attempt_count: int, success: bool) -> None:
    buf = _delivery_history.get(watch_id)
    if buf is None:
        return
    buf.append(_DeliveryRecord(
        timestamp=time.time(),
        http_status=http_status,
        attempt_count=attempt_count,
        success=success,
    ))


def get_delivery_history(watch_id: str) -> list[dict] | None:
    """
    Return delivery records for a watch_id in reverse-chronological order.
    Returns None if the watch_id is unknown (allows 404 vs empty list distinction).
    """
    buf = _delivery_history.get(watch_id)
    if buf is None:
        return None
    return [
        {
            "timestamp": r.timestamp,
            "http_status": r.http_status,
            "attempt_count": r.attempt_count,
            "success": r.success,
        }
        for r in reversed(buf)
    ]


# ── SQLite persistence ────────────────────────────────────────────────────────


def _db_setup() -> None:
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(_DB_PATH) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS webhooks (
                watch_id       TEXT PRIMARY KEY,
                username       TEXT NOT NULL,
                callback_url   TEXT NOT NULL,
                secret         TEXT,
                owner_key_hash TEXT
            )
        """)
        try:
            conn.execute("ALTER TABLE webhooks ADD COLUMN owner_key_hash TEXT")
        except sqlite3.OperationalError:
            pass  # column already exists


def _db_insert_sync(
    watch_id: str, username: str, callback_url: str, secret: str | None, owner_key_hash: str | None
) -> None:
    _db_setup()
    with sqlite3.connect(_DB_PATH) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO webhooks VALUES (?, ?, ?, ?, ?)",
            (watch_id, username, callback_url, secret, owner_key_hash),
        )


def _db_delete_sync(watch_id: str) -> None:
    _db_setup()
    with sqlite3.connect(_DB_PATH) as conn:
        conn.execute("DELETE FROM webhooks WHERE watch_id = ?", (watch_id,))


def _db_load_all_sync() -> list[tuple]:
    with sqlite3.connect(_DB_PATH) as conn:
        return conn.execute(
            "SELECT watch_id, username, callback_url, secret, owner_key_hash FROM webhooks"
        ).fetchall()


# ── Public API ────────────────────────────────────────────────────────────────


async def restore_webhooks() -> None:
    """Load persisted webhooks from DB and restart poll tasks. Call once on startup."""
    await asyncio.to_thread(_db_setup)
    rows = await asyncio.to_thread(_db_load_all_sync)
    for watch_id, username, callback_url, secret, owner_key_hash in rows:
        watcher = _Watcher(
            watch_id=watch_id,
            username=username,
            callback_url=callback_url,
            secret=secret,
            owner_key_hash=owner_key_hash,
        )
        watcher.task = asyncio.create_task(_poll_loop(watcher), name=f"webhook-{watch_id[:8]}")
        _watchers[watch_id] = watcher
        _delivery_history[watch_id] = deque(maxlen=_MAX_DELIVERY_HISTORY)
    if rows:
        logger.info("Restored %d webhook(s) from database.", len(rows))


async def shutdown_webhooks() -> None:
    """Cancel all active poll tasks and wait for them to finish. Call on app shutdown."""
    tasks = [w.task for w in _watchers.values() if w.task and not w.task.done()]
    if not tasks:
        return
    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    logger.info("Cancelled %d webhook task(s) on shutdown.", len(tasks))


async def register_webhook(
    username: str,
    callback_url: str,
    secret: str | None = None,
    owner_key: str | None = None,
) -> str:
    """
    Register a new webhook and start its background poll loop.

    owner_key: raw API key of the creator. Hashed before storage. Pass None
    when auth is disabled (anonymous registration → legacy NULL ownership).

    Raises:
        WebhookURLError:      Callback URL targets a private/loopback address.
        WebhookLimitExceeded: Global or per-user cap reached.
    """
    _assert_not_ssrf(callback_url)

    uname = username.lower()
    if len(_watchers) >= MAX_WEBHOOKS_TOTAL:
        raise WebhookLimitExceeded(f"Global webhook limit reached ({MAX_WEBHOOKS_TOTAL}).")
    per_user = sum(1 for w in _watchers.values() if w.username == uname)
    if per_user >= MAX_WEBHOOKS_PER_USER:
        raise WebhookLimitExceeded(
            f"Limit of {MAX_WEBHOOKS_PER_USER} webhooks per username reached for '{uname}'."
        )

    okh = _hash_key(owner_key) if owner_key else None
    watch_id = str(uuid.uuid4())
    watcher = _Watcher(
        watch_id=watch_id,
        username=uname,
        callback_url=callback_url,
        secret=secret,
        owner_key_hash=okh,
    )
    watcher.task = asyncio.create_task(_poll_loop(watcher), name=f"webhook-{watch_id[:8]}")
    _watchers[watch_id] = watcher
    _delivery_history[watch_id] = deque(maxlen=_MAX_DELIVERY_HISTORY)
    await asyncio.to_thread(_db_insert_sync, watch_id, uname, callback_url, secret, okh)
    logger.info("Registered webhook %s for '%s' → %s", watch_id[:8], uname, callback_url)
    return watch_id


async def unregister_webhook(watch_id: str, owner_key: str | None = None) -> bool:
    """
    Cancel the poll task, remove from memory and DB.

    Returns False if not found OR if owner_key doesn't match the stored
    owner_key_hash (both surface as 404 to the client — no enumeration).
    Pass owner_key=None to bypass ownership check (admin path).
    NULL owner_key_hash (legacy) is always deletable by any authenticated caller.
    """
    watcher = _watchers.get(watch_id)
    if watcher is None:
        return False
    if owner_key is not None and watcher.owner_key_hash is not None:
        if watcher.owner_key_hash != _hash_key(owner_key):
            return False
    _watchers.pop(watch_id, None)
    if watcher.task and not watcher.task.done():
        watcher.task.cancel()
    _delivery_history.pop(watch_id, None)
    await asyncio.to_thread(_db_delete_sync, watch_id)
    logger.info("Unregistered webhook %s ('%s').", watch_id[:8], watcher.username)
    return True


def list_webhooks(owner_key: str | None = None) -> list[_Watcher]:
    """
    Return watchers visible to owner_key.

    If owner_key is provided, returns webhooks where owner_key_hash matches
    OR owner_key_hash is NULL (legacy webhooks created before ownership was
    introduced). Pass owner_key=None for the admin path (returns all).
    """
    if owner_key is None:
        return list(_watchers.values())
    okh = _hash_key(owner_key)
    return [w for w in _watchers.values() if w.owner_key_hash is None or w.owner_key_hash == okh]


def list_all_webhooks() -> list[_Watcher]:
    """Return all watchers regardless of ownership. Admin-only path."""
    return list(_watchers.values())


# ── Background poll loop ──────────────────────────────────────────────────────


async def _dispatch_with_retry(watcher: _Watcher, status: LiveStatus) -> bool:
    """
    POST status to the callback URL with exponential backoff on transient failures.

    Why retry here instead of at the poll level?
    A status change fires once — if the receiver is briefly down (deploy, restart,
    transient 503) the event would be silently lost without retry. Retrying at the
    delivery level preserves the event; retrying at the poll level would not re-fire
    because is_live hasn't changed.

    Backoff schedule: immediate → 1 s → 2 s (3 attempts total).
    The failure counter in _poll_loop only increments when all attempts fail.
    """
    last_http_status: int | None = None
    for attempt in range(DISPATCH_MAX_ATTEMPTS):
        ok, http_status = await _dispatch(watcher, status)
        last_http_status = http_status
        if ok:
            _record_delivery(watcher.watch_id, http_status, attempt + 1, True)
            return True
        if attempt < DISPATCH_MAX_ATTEMPTS - 1:
            await asyncio.sleep(DISPATCH_BACKOFF_BASE * (2 ** attempt))
    _record_delivery(watcher.watch_id, last_http_status, DISPATCH_MAX_ATTEMPTS, False)
    return False


async def _poll_loop(watcher: _Watcher) -> None:
    """Background task: polls TikTok and dispatches to callback on is_live change."""
    last_live: bool | None = None
    failures = 0

    while True:
        try:
            status = await get_live_status_sse(watcher.username)
            if last_live is None or status.is_live != last_live:
                last_live = status.is_live
                ok = await _dispatch_with_retry(watcher, status)
                if not ok:
                    failures += 1
                    logger.warning(
                        "Webhook %s: delivery failed (%d/%d).",
                        watcher.watch_id[:8],
                        failures,
                        MAX_DISPATCH_FAILURES,
                    )
                    if failures >= MAX_DISPATCH_FAILURES:
                        logger.error(
                            "Webhook %s: max failures reached, removing.", watcher.watch_id[:8]
                        )
                        _watchers.pop(watcher.watch_id, None)
                        await asyncio.to_thread(_db_delete_sync, watcher.watch_id)
                        return
                else:
                    failures = 0

        except TikTokUserNotFound:
            logger.warning(
                "Webhook %s: user '%s' not found, removing.", watcher.watch_id[:8], watcher.username
            )
            _watchers.pop(watcher.watch_id, None)
            await asyncio.to_thread(_db_delete_sync, watcher.watch_id)
            return

        except TikTokAPIError:
            pass  # transient — keep polling

        except asyncio.CancelledError:
            return

        await asyncio.sleep(POLL_INTERVAL)


async def _dispatch(watcher: _Watcher, status: LiveStatus) -> tuple[bool, int | None]:
    """POST status JSON to the callback URL. Returns (success, http_status_or_None)."""
    payload = status.model_dump_json().encode()
    headers = {"Content-Type": "application/json"}

    if watcher.secret:
        sig = hmac.new(watcher.secret.encode(), payload, hashlib.sha256).hexdigest()
        headers["X-NotiTFK-Signature"] = f"sha256={sig}"

    try:
        async with httpx.AsyncClient(timeout=DISPATCH_TIMEOUT) as client:
            resp = await client.post(watcher.callback_url, content=payload, headers=headers)
            return resp.is_success, resp.status_code
    except Exception:
        return False, None
