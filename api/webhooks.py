"""
Webhook dispatch for NotiTK.

Callers register a (username, callback_url) pair via register_webhook(). A
background asyncio task polls TikTok via get_live_status_sse and fires an HTTP
POST to the callback whenever is_live changes.

Optional HMAC-SHA256 signing: if `secret` is provided at registration, every
POST includes an `X-NotiTK-Signature: sha256=<hex>` header so the receiver
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

Public API:
  restore_webhooks()                                     — call on app startup
  shutdown_webhooks()                                    — call on app shutdown
  register_webhook(username, callback_url, secret=None)  → watch_id (str)
  unregister_webhook(watch_id)                           → bool (True = removed)
  list_webhooks()                                        → list[_Watcher]

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
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

import httpx

from .models import LiveStatus
from .tiktok import get_live_status_sse, TikTokUserNotFound, TikTokAPIError, POLL_INTERVAL

logger = logging.getLogger(__name__)

DISPATCH_TIMEOUT = 10
MAX_DISPATCH_FAILURES = 5
MAX_WEBHOOKS_TOTAL = int(os.getenv("MAX_WEBHOOKS", "100"))
MAX_WEBHOOKS_PER_USER = int(os.getenv("MAX_WEBHOOKS_PER_USER", "5"))

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


@dataclass
class _Watcher:
    watch_id: str
    username: str
    callback_url: str
    secret: str | None
    task: asyncio.Task | None = field(default=None, repr=False)


# watch_id → _Watcher
_watchers: dict[str, _Watcher] = {}


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


# ── SQLite persistence ────────────────────────────────────────────────────────


def _db_setup() -> None:
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(_DB_PATH) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS webhooks (
                watch_id     TEXT PRIMARY KEY,
                username     TEXT NOT NULL,
                callback_url TEXT NOT NULL,
                secret       TEXT
            )
        """)


def _db_insert_sync(watch_id: str, username: str, callback_url: str, secret: str | None) -> None:
    _db_setup()
    with sqlite3.connect(_DB_PATH) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO webhooks VALUES (?, ?, ?, ?)",
            (watch_id, username, callback_url, secret),
        )


def _db_delete_sync(watch_id: str) -> None:
    _db_setup()
    with sqlite3.connect(_DB_PATH) as conn:
        conn.execute("DELETE FROM webhooks WHERE watch_id = ?", (watch_id,))


def _db_load_all_sync() -> list[tuple]:
    with sqlite3.connect(_DB_PATH) as conn:
        return conn.execute(
            "SELECT watch_id, username, callback_url, secret FROM webhooks"
        ).fetchall()


# ── Public API ────────────────────────────────────────────────────────────────


async def restore_webhooks() -> None:
    """Load persisted webhooks from DB and restart poll tasks. Call once on startup."""
    await asyncio.to_thread(_db_setup)
    rows = await asyncio.to_thread(_db_load_all_sync)
    for watch_id, username, callback_url, secret in rows:
        watcher = _Watcher(
            watch_id=watch_id, username=username, callback_url=callback_url, secret=secret
        )
        watcher.task = asyncio.create_task(_poll_loop(watcher), name=f"webhook-{watch_id[:8]}")
        _watchers[watch_id] = watcher
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


async def register_webhook(username: str, callback_url: str, secret: str | None = None) -> str:
    """
    Register a new webhook and start its background poll loop.

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

    watch_id = str(uuid.uuid4())
    watcher = _Watcher(watch_id=watch_id, username=uname, callback_url=callback_url, secret=secret)
    watcher.task = asyncio.create_task(_poll_loop(watcher), name=f"webhook-{watch_id[:8]}")
    _watchers[watch_id] = watcher
    await asyncio.to_thread(_db_insert_sync, watch_id, uname, callback_url, secret)
    logger.info("Registered webhook %s for '%s' → %s", watch_id[:8], uname, callback_url)
    return watch_id


async def unregister_webhook(watch_id: str) -> bool:
    """Cancel the poll task, remove from memory and DB. Returns False if not found."""
    watcher = _watchers.pop(watch_id, None)
    if watcher is None:
        return False
    if watcher.task and not watcher.task.done():
        watcher.task.cancel()
    await asyncio.to_thread(_db_delete_sync, watch_id)
    logger.info("Unregistered webhook %s ('%s').", watch_id[:8], watcher.username)
    return True


def list_webhooks() -> list[_Watcher]:
    """Return all active watchers. Callers must not expose the secret field."""
    return list(_watchers.values())


# ── Background poll loop ──────────────────────────────────────────────────────


async def _poll_loop(watcher: _Watcher) -> None:
    """Background task: polls TikTok and dispatches to callback on is_live change."""
    last_live: bool | None = None
    failures = 0

    while True:
        try:
            status = await get_live_status_sse(watcher.username)
            if last_live is None or status.is_live != last_live:
                last_live = status.is_live
                ok = await _dispatch(watcher, status)
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


async def _dispatch(watcher: _Watcher, status: LiveStatus) -> bool:
    """POST status JSON to the callback URL. Returns True on 2xx, False otherwise."""
    payload = status.model_dump_json().encode()
    headers = {"Content-Type": "application/json"}

    if watcher.secret:
        sig = hmac.new(watcher.secret.encode(), payload, hashlib.sha256).hexdigest()
        headers["X-NotiTK-Signature"] = f"sha256={sig}"

    try:
        async with httpx.AsyncClient(timeout=DISPATCH_TIMEOUT) as client:
            resp = await client.post(watcher.callback_url, content=payload, headers=headers)
            return resp.is_success
    except Exception:
        return False
