"""
NotiTFK — TikTok live status API.

Five complementary endpoints:

  GET /api/status/{username}
      One-shot check. Returns current live status as JSON.
      Cached server-side 30s. Best for: Discord slash commands, one-time checks.

  GET /api/stream/{username}
      Server-Sent Events — change-only events when is_live flips + heartbeat
      comment every 30s. Best for: web live/offline bubbles, real-time dashboards.

  GET /api/stream?users=u1,u2,u3
      Single SSE connection tracking up to 10 usernames. Each event carries
      the `username` field. Same change-only semantics.

  POST /api/watch
      Register a webhook: NotiTFK POSTs to your URL whenever is_live changes.
      No persistent connection needed. Optional HMAC-SHA256 signing (X-NotiTFK-Signature header).

  DELETE /api/watch/{watch_id}
      Unregister a previously registered webhook.

  GET /api/watches
      List webhooks owned by the calling key (+ legacy NULL-owner webhooks).

  GET /api/admin/watches
      List ALL webhooks with owner_key_hash. Requires X-Admin-Secret header.

  GET /api/watch/{watch_id}/deliveries
      Last 20 delivery records for a webhook (timestamp, http_status,
      attempt_count, success). Cleared on server restart or unregister.

Authentication (when API_KEYS env var is set):
  REST / webhooks → X-API-Key: sk_live_abc123
  SSE             → /api/stream/ninja?key=sk_live_abc123
                   (EventSource cannot set headers, so key goes in query param)

Response shape (live status):
  {
    "username":     "ninja",
    "is_live":      true,
    "room_id":      "7496121315238087466",   // null when offline
    "viewer_count": 12500,                   // null when offline
    "title":        "Fortnite ranked grind"  // null when offline
  }

Run locally:
    uvicorn api.main:app --reload --port 8000

Environment variables:
    API_KEYS              Comma-separated valid API keys. Unset = auth disabled.
    REQUIRE_API_KEY       Set to "true" to enforce auth even with no keys.
    RATE_LIMIT_REQUESTS   Max requests per window per key/IP (default: 120)
    RATE_LIMIT_WINDOW     Window size in seconds (default: 60)
    KEY_INVITE_CODE       When set, POST /api/keys requires ?invite=<code>.
    WEBHOOKS_DB           SQLite path for webhook persistence (default: data/webhooks.db)
    MAX_WEBHOOKS          Global webhook cap (default: 100)
    MAX_WEBHOOKS_PER_USER Per-username webhook cap (default: 5)
    SSE_MAX_PER_KEY       Max concurrent SSE connections per key/IP (default: 20)
    SSE_MAX_PER_USERNAME  Max concurrent SSE connections per username (default: 50)
    LOG_FORMAT            "json" for structured logs, "text" for human-readable (default)
    LOG_LEVEL             Log level: DEBUG, INFO, WARNING, ERROR (default: INFO)
    CORS_ORIGINS          Comma-separated allowed origins (default: *)
"""

import asyncio
import json
import logging
import os
import re
import time
import tomllib
from contextlib import asynccontextmanager
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse

from .auth import api_key_dependency, AUTH_ENABLED
from .keys import (
    generate_key,
    list_keys,
    revoke_key,
    ADMIN_SECRET,
    KEY_INVITE_CODE,
    KeyValidationError,
    KeyRateLimitError,
)
from .models import (
    AdminKeyResponse,
    AdminWatchResponse,
    DeliveryRecord,
    ErrorResponse,
    HealthResponse,
    KeyResponse,
    LiveStatus,
    WatchRequest,
    WatchResponse,
)
from .webhooks import (
    register_webhook,
    unregister_webhook,
    list_webhooks,
    list_all_webhooks,
    get_delivery_history,
    restore_webhooks,
    shutdown_webhooks,
    WebhookLimitExceeded,
    WebhookURLError,
)
from .tiktok import (
    get_live_status,
    get_live_status_sse,
    TikTokUserNotFound,
    TikTokAPIError,
    CACHE_TTL,
    POLL_INTERVAL,
)


def _read_version() -> str:
    try:
        return _pkg_version("notiftk")
    except PackageNotFoundError:
        with open(Path(__file__).parent.parent / "pyproject.toml", "rb") as _f:
            return str(tomllib.load(_f)["project"]["version"])


_START_TIME = time.monotonic()

HEARTBEAT_EVERY = 6  # 6 × POLL_INTERVAL = 30s heartbeat interval
_MULTI_DONE = object()  # sentinel: all per-user tasks in _sse_generator_multi exited

_FRONTEND_PATH = Path(__file__).parent.parent / "frontend" / "index.html"
_USERNAME_RE = re.compile(r"^[a-zA-Z0-9._]{1,24}$")

_SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    # Prevents nginx / Fly.io from buffering events — they must reach the client immediately.
    "X-Accel-Buffering": "no",
}

_MAX_USERS = 10

SSE_MAX_PER_KEY = int(os.getenv("SSE_MAX_PER_KEY", "20"))
SSE_MAX_PER_USERNAME = int(os.getenv("SSE_MAX_PER_USERNAME", "50"))

# identity (API key or client IP) → open SSE connection count
_sse_connections: dict[str, int] = {}
# lowercase username → open SSE connection count (across all keys)
_sse_per_username: dict[str, int] = {}


class _JsonFormatter(logging.Formatter):
    """
    Zero-dependency JSON log formatter. Activated via LOG_FORMAT=json env var.

    Why not python-json-logger?
    Adding a dependency for a single formatter is not worth the extra pin in
    requirements.txt. The stdlib logging.Formatter gives us everything we need.

    Output fields: ts, level, logger, msg, exc (when present), + any extra= kwargs
    passed to the logger call (e.g. logger.info("...", extra={"username": "ninja"})).
    """

    _SKIP = frozenset(
        {
            "args",
            "created",
            "exc_info",
            "exc_text",
            "filename",
            "funcName",
            "levelno",
            "lineno",
            "message",
            "module",
            "msecs",
            "msg",
            "name",
            "pathname",
            "process",
            "processName",
            "relativeCreated",
            "stack_info",
            "taskName",
            "thread",
            "threadName",
        }
    )

    def format(self, record: logging.LogRecord) -> str:
        record.message = record.getMessage()
        payload: dict = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.message,
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        for k, v in vars(record).items():
            if k not in self._SKIP:
                payload[k] = v
        return json.dumps(payload, default=str)


def _configure_logging() -> None:
    """
    Configure the root logger once at startup.

    LOG_FORMAT=json  → structured JSON (one object per line) — ideal for
                       journald / Loki / any log aggregator.
    LOG_FORMAT=text  → human-readable (default, good for local dev).

    Works whether or not uvicorn has already added handlers (reconfigures
    existing handlers rather than duplicating them via basicConfig).
    """
    log_level = os.getenv("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, log_level, logging.INFO)
    root = logging.root
    root.setLevel(level)

    if os.getenv("LOG_FORMAT", "").lower() == "json":
        formatter: logging.Formatter = _JsonFormatter()
    else:
        formatter = logging.Formatter(
            "%(asctime)s %(levelname)s %(name)s — %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S",
        )

    if root.handlers:
        for h in root.handlers:
            h.setFormatter(formatter)
    else:
        handler = logging.StreamHandler()
        handler.setFormatter(formatter)
        root.addHandler(handler)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    _configure_logging()
    logging.getLogger(__name__).info("NotiTFK starting up — auth_enabled=%s", AUTH_ENABLED)
    await restore_webhooks()
    yield
    await shutdown_webhooks()
    logging.getLogger(__name__).info("NotiTFK shut down cleanly")


app = FastAPI(
    title="NotiTFK",
    description=__doc__,
    version=_read_version(),
    lifespan=lifespan,
)

_cors_origins = os.getenv("CORS_ORIGINS", "*").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["Content-Type", "X-API-Key", "X-Admin-Secret"],
    expose_headers=["Retry-After"],
)

# Shared dependency — avoids repeating Depends(api_key_dependency) everywhere
_auth = Depends(api_key_dependency)


def _validate_username(username: str) -> None:
    if not _USERNAME_RE.match(username):
        raise HTTPException(
            status_code=422,
            detail="Invalid TikTok username: 1–24 characters, alphanumeric, dots and underscores only.",
        )


# ── Meta endpoints ────────────────────────────────────────────────────────────


@app.get("/", include_in_schema=False)
async def serve_frontend():
    if not _FRONTEND_PATH.exists():
        raise HTTPException(status_code=404, detail="Frontend not found")
    return FileResponse(_FRONTEND_PATH)


def _db_ok() -> bool:
    """Quick write-read check on the SQLite file used by keys/webhooks."""
    try:
        from .keys import _db

        with _db() as conn:
            conn.execute("SELECT 1").fetchone()
        return True
    except Exception:
        return False


@app.get("/health", response_model=HealthResponse, summary="Liveness check", tags=["Meta"])
async def health() -> HealthResponse:
    """
    Returns 200 when the server process is alive.

    Does **not** hit TikTok's API — this is a liveness probe for load
    balancers (Fly.io, Docker, k8s). Use `/api/status/{username}` to verify
    TikTok connectivity.
    """
    return HealthResponse(
        status="ok",
        version=app.version,
        timestamp=time.time(),
        uptime_seconds=round(time.monotonic() - _START_TIME, 1),
        auth_enabled=AUTH_ENABLED,
        cache_ttl_seconds=CACHE_TTL,
        poll_interval_seconds=POLL_INTERVAL,
        active_webhooks=len(list_all_webhooks()),
        active_sse_connections=sum(_sse_connections.values()),
        db_ok=await asyncio.to_thread(_db_ok),
    )


# ── One-shot REST endpoint ────────────────────────────────────────────────────


@app.get(
    "/api/status/{username}",
    response_model=LiveStatus,
    responses={
        401: {"description": "Missing or invalid API key"},
        404: {"model": ErrorResponse, "description": "User not found or never went live"},
        422: {"description": "Invalid username"},
        429: {"description": "Rate limit exceeded"},
        502: {"model": ErrorResponse, "description": "TikTok API error"},
    },
    summary="One-shot live status check",
    tags=["Live Status"],
    dependencies=[_auth],
)
async def live_status(username: str):
    """
    Returns the current live status for *username*.

    Cached server-side for **30s** — safe to call from bots on every slash command.
    For continuous monitoring, prefer `/api/stream/{{username}}` (SSE) or `/api/watch` (webhook).
    """
    _validate_username(username)
    try:
        return await get_live_status(username)
    except TikTokUserNotFound as e:
        return JSONResponse(status_code=404, content={"username": username, "error": str(e)})
    except TikTokAPIError as e:
        return JSONResponse(status_code=502, content={"username": username, "error": str(e)})


# ── Single-username SSE endpoint ──────────────────────────────────────────────


@app.get(
    "/api/stream/{username}",
    summary="Real-time live status stream (SSE)",
    tags=["Live Status"],
    responses={
        200: {
            "content": {"text/event-stream": {}},
            "description": (
                "Change-only `data: <JSON>` events when is_live flips. "
                "`: heartbeat` comment every 30s to keep the connection alive. "
                "`transient: true` = temporary error, keep listening. "
                "No `transient` key = fatal error, close the connection."
            ),
        },
        401: {"description": "Missing or invalid API key"},
        422: {"description": "Invalid username"},
        429: {"description": "Rate limit exceeded or SSE connection cap reached"},
    },
)
async def live_stream(username: str, request: Request, _key: str | None = _auth):
    """
    Opens a persistent SSE connection for *username*. Events are emitted only
    when `is_live` changes, not on every poll.

    **Browser** (`EventSource`):
    ```js
    const src = new EventSource(`/api/stream/${{username}}?key=sk_live_abc123`);
    src.onmessage = (e) => {{
      const {{ is_live, viewer_count, title }} = JSON.parse(e.data);
    }};
    ```

    **Node.js** (Node 18+ built-in or `npm install eventsource`):
    ```js
    const {{ EventSource }} = require('eventsource');
    const src = new EventSource(`http://localhost:8000/api/stream/${{username}}`);
    src.onmessage = (e) => console.log(JSON.parse(e.data));
    ```
    """
    _validate_username(username)
    identity = _key or _client_ip(request)
    uname = username.lower()
    if _sse_connections.get(identity, 0) >= SSE_MAX_PER_KEY:
        raise HTTPException(
            status_code=429,
            detail=f"Maximum {SSE_MAX_PER_KEY} simultaneous SSE connections per key.",
        )
    if _sse_per_username.get(uname, 0) >= SSE_MAX_PER_USERNAME:
        raise HTTPException(
            status_code=429,
            detail=f"Maximum {SSE_MAX_PER_USERNAME} simultaneous SSE connections for @{username}.",
        )
    _sse_connections[identity] = _sse_connections.get(identity, 0) + 1
    _sse_per_username[uname] = _sse_per_username.get(uname, 0) + 1
    return StreamingResponse(
        _sse_generator(username, identity), media_type="text/event-stream", headers=_SSE_HEADERS
    )


# ── Multi-username SSE endpoint ───────────────────────────────────────────────


@app.get(
    "/api/stream",
    summary="Real-time live status stream for multiple users (SSE)",
    tags=["Live Status"],
    responses={
        200: {
            "content": {"text/event-stream": {}},
            "description": "Change-only SSE events, one per username change",
        },
        401: {"description": "Missing or invalid API key"},
        422: {"description": "Invalid username or too many users requested"},
        429: {"description": "Rate limit exceeded or SSE connection cap reached"},
    },
)
async def live_stream_multi(
    users: Annotated[
        str,
        Query(description=f"Comma-separated TikTok usernames (max {_MAX_USERS})"),
    ],
    request: Request,
    _key: str | None = _auth,
):
    """
    Single SSE connection tracking multiple usernames simultaneously.

    ```
    GET /api/stream?users=ninja,pokimane,xqc
    ```

    Each event includes a `username` field identifying which streamer changed.
    Same change-only + heartbeat semantics as `/api/stream/{{username}}`.
    """
    usernames = [u.strip() for u in users.split(",") if u.strip()]
    if not usernames:
        raise HTTPException(status_code=422, detail="At least one username is required.")
    if len(usernames) > _MAX_USERS:
        raise HTTPException(status_code=422, detail=f"Maximum {_MAX_USERS} usernames per request.")
    for u in usernames:
        _validate_username(u)
    identity = _key or _client_ip(request)
    if _sse_connections.get(identity, 0) >= SSE_MAX_PER_KEY:
        raise HTTPException(
            status_code=429,
            detail=f"Maximum {SSE_MAX_PER_KEY} simultaneous SSE connections per key.",
        )
    for u in usernames:
        if _sse_per_username.get(u.lower(), 0) >= SSE_MAX_PER_USERNAME:
            raise HTTPException(
                status_code=429,
                detail=f"Maximum {SSE_MAX_PER_USERNAME} simultaneous SSE connections for @{u}.",
            )
    _sse_connections[identity] = _sse_connections.get(identity, 0) + 1
    for u in usernames:
        uname = u.lower()
        _sse_per_username[uname] = _sse_per_username.get(uname, 0) + 1
    return StreamingResponse(
        _sse_generator_multi(usernames, identity),
        media_type="text/event-stream",
        headers=_SSE_HEADERS,
    )


# ── Self-service key endpoints ────────────────────────────────────────────────


def _client_ip(request: Request) -> str:
    xff = request.headers.get("x-forwarded-for")
    return (
        xff.split(",")[0].strip() if xff else (request.client.host if request.client else "unknown")
    )


def _require_admin(x_admin_secret: Annotated[str | None, Header(alias="X-Admin-Secret")] = None):
    if not ADMIN_SECRET or x_admin_secret != ADMIN_SECRET:
        raise HTTPException(status_code=404)


@app.post(
    "/api/keys",
    response_model=KeyResponse,
    status_code=201,
    responses={
        403: {"description": "Invalid or missing invite code"},
        422: {"description": "Invalid label or expires_in"},
        429: {"description": "Per-IP or global key cap reached"},
    },
    summary="Generate a self-service API key",
    tags=["Keys"],
)
async def create_key(
    request: Request,
    label: str | None = None,
    invite: str | None = None,
    expires_in: int | None = None,
) -> KeyResponse:
    """
    Generate a new API key. **The key is shown only once — save it immediately.**

    Rate-limited: max 3 keys per IP per 24 hours.

    - `label` — optional human-readable name for the key (max 100 chars).
    - `invite` — required when `KEY_INVITE_CODE` env var is set on the server.
    - `expires_in` — optional TTL in seconds (max 31 536 000 = 1 year). Omit for a
      non-expiring key.
    """
    if KEY_INVITE_CODE and invite != KEY_INVITE_CODE:
        raise HTTPException(status_code=403, detail="Code d'invitation requis ou invalide.")
    ip = _client_ip(request)
    try:
        key_id, raw_key, expires_at = await asyncio.to_thread(generate_key, ip, label, expires_in)
    except KeyValidationError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except KeyRateLimitError as e:
        raise HTTPException(status_code=429, detail=str(e))
    return KeyResponse(
        id=key_id,
        key=raw_key,
        expires_at=expires_at,
        warning="Copiez cette clé maintenant — elle ne sera plus affichée.",
    )


@app.get(
    "/api/keys/verify",
    summary="Verify an API key",
    tags=["Keys"],
    responses={
        200: {"description": "Key is valid"},
        401: {"description": "Missing or invalid API key"},
        429: {"description": "Rate limit exceeded"},
    },
    dependencies=[_auth],
)
async def verify_key() -> dict:
    """
    Returns `{"valid": true}` if the supplied key is valid, 401 otherwise.

    Use this instead of probing a data endpoint to test key validity — it
    does not hit TikTok's API and is cheap to call.

    ```js
    const r = await fetch('/api/keys/verify?key=sk_live_...');
    if (r.status === 401) { /* key is stale or invalid */ }
    ```
    """
    return {"valid": True}


@app.get(
    "/api/admin/keys",
    response_model=list[AdminKeyResponse],
    summary="List all API keys (admin)",
    tags=["Admin"],
    dependencies=[Depends(_require_admin)],
)
async def admin_list_keys() -> list[AdminKeyResponse]:
    """List all generated keys with their metadata. Requires `X-Admin-Secret` header."""
    keys = await asyncio.to_thread(list_keys)
    return [AdminKeyResponse(**k) for k in keys]


@app.delete(
    "/api/admin/keys/{key_id}",
    status_code=204,
    summary="Revoke an API key (admin)",
    tags=["Admin"],
    dependencies=[Depends(_require_admin)],
)
async def admin_revoke_key(key_id: str):
    """Revoke a key by its ID. The key immediately stops working."""
    removed = await asyncio.to_thread(revoke_key, key_id)
    if not removed:
        raise HTTPException(status_code=404, detail=f"Key '{key_id}' not found.")


# ── Webhook endpoints ─────────────────────────────────────────────────────────


@app.post(
    "/api/watch",
    response_model=WatchResponse,
    status_code=201,
    responses={
        401: {"description": "Missing or invalid API key"},
        422: {"description": "Invalid payload or callback URL blocked (private/loopback IP)"},
        429: {"description": "Rate limit exceeded or webhook cap reached"},
    },
    summary="Register a webhook for live status changes",
    tags=["Webhooks"],
)
async def watch(body: WatchRequest, _key: str | None = _auth):
    """
    Register a callback URL POSTed whenever *username* goes live or offline.

    ```json
    { "username": "ninja", "callback_url": "https://your-bot.example.com/hook" }
    ```

    The POST body is identical to the REST status endpoint response.
    Pass `secret` to enable HMAC-SHA256 signing (`X-NotiTFK-Signature: sha256=<hex>`).

    Returns a `watch_id` — keep it to unregister later with `DELETE /api/watch/{watch_id}`.
    Webhooks are bound to the creating key: only that key can list or delete them.
    Legacy webhooks (created without a key) remain accessible to all authenticated callers.
    """
    _validate_username(body.username)
    try:
        watch_id = await register_webhook(
            body.username, str(body.callback_url), body.secret, owner_key=_key
        )
    except WebhookURLError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except WebhookLimitExceeded as e:
        raise HTTPException(status_code=429, detail=str(e))
    return WatchResponse(
        watch_id=watch_id, username=body.username, callback_url=str(body.callback_url)
    )


@app.delete(
    "/api/watch/{watch_id}",
    status_code=204,
    responses={
        401: {"description": "Missing or invalid API key"},
        404: {"description": "Watch ID not found or not owned by this key"},
        429: {"description": "Rate limit exceeded"},
    },
    summary="Unregister a webhook",
    tags=["Webhooks"],
)
async def unwatch(watch_id: str, _key: str | None = _auth):
    """
    Remove the webhook registered under *watch_id*.

    Returns 404 if the watch_id does not exist **or** belongs to a different key.
    This prevents callers from enumerating other users' webhooks by probing for 403.
    """
    removed = await unregister_webhook(watch_id, owner_key=_key)
    if not removed:
        raise HTTPException(status_code=404, detail=f"watch_id '{watch_id}' not found.")


@app.get(
    "/api/watches",
    response_model=list[WatchResponse],
    summary="List active webhooks",
    tags=["Webhooks"],
)
async def watches(_key: str | None = _auth):
    """
    Return webhooks owned by the current key.

    Includes legacy webhooks (created before key-based ownership was introduced).
    Secrets are never included in any response.
    """
    return [
        WatchResponse(watch_id=w.watch_id, username=w.username, callback_url=w.callback_url)
        for w in list_webhooks(owner_key=_key)
    ]


@app.get(
    "/api/admin/watches",
    response_model=list[AdminWatchResponse],
    summary="List all webhooks (admin)",
    tags=["Admin"],
    dependencies=[Depends(_require_admin)],
)
async def admin_watches():
    """
    Return all registered webhooks regardless of ownership.

    Includes `owner_key_hash` so admins can cross-reference with their key list.
    Secrets are never included.
    """
    return [
        AdminWatchResponse(
            watch_id=w.watch_id,
            username=w.username,
            callback_url=w.callback_url,
            owner_key_hash=w.owner_key_hash,
        )
        for w in list_all_webhooks()
    ]


@app.get(
    "/api/watch/{watch_id}/deliveries",
    response_model=list[DeliveryRecord],
    responses={
        401: {"description": "Missing or invalid API key"},
        404: {"description": "Watch ID not found"},
        429: {"description": "Rate limit exceeded"},
    },
    summary="Webhook delivery history",
    tags=["Webhooks"],
    dependencies=[_auth],
)
async def watch_deliveries(watch_id: str):
    """
    Return the last 20 delivery attempts for *watch_id*, most recent first.

    Each record includes:
    - `timestamp` — Unix timestamp of the delivery attempt.
    - `http_status` — HTTP status code returned by the receiver, or `null` on network error.
    - `attempt_count` — number of attempts made (1–3, counting retries).
    - `success` — `true` if any attempt got a 2xx response.

    Deliveries are kept in memory only — they reset on server restart and are
    removed when the webhook is unregistered.
    """
    records = get_delivery_history(watch_id)
    if records is None:
        raise HTTPException(status_code=404, detail=f"watch_id '{watch_id}' not found.")
    return [DeliveryRecord(**r) for r in records]


# ── SSE generators ────────────────────────────────────────────────────────────


async def _sse_generator(username: str, identity: str):
    """
    Yields SSE events only when is_live changes (plus periodic heartbeats).

    Protocol:
      - `data: <JSON>`    on first poll, on every is_live flip, and after a transient error clears
      - `: heartbeat`     comment every HEARTBEAT_EVERY polls (keeps proxies from timing out)
      - Fatal error:      `data: {"error": ...}` then generator stops
      - Transient error:  `data: {"error": ..., "transient": true}` then keep going

    The outer try/finally ensures _sse_connections and _sse_per_username are
    decremented even on client disconnect, server error, or CancelledError.
    """
    last_live: bool | None = None
    had_error = False
    poll_count = 0
    uname = username.lower()
    try:
        while True:
            try:
                status = await get_live_status_sse(username)
                poll_count += 1
                changed = last_live is None or status.is_live != last_live
                if changed or had_error:
                    yield f"data: {status.model_dump_json()}\n\n"
                    last_live = status.is_live
                    had_error = False
                elif poll_count % HEARTBEAT_EVERY == 0:
                    yield ": heartbeat\n\n"

            except TikTokUserNotFound as e:
                yield f"data: {json.dumps({'username': username, 'error': str(e)})}\n\n"
                return

            except TikTokAPIError as e:
                had_error = True
                poll_count += 1
                yield f"data: {json.dumps({'username': username, 'error': str(e), 'transient': True})}\n\n"

            except asyncio.CancelledError:
                return

            await asyncio.sleep(POLL_INTERVAL)
    finally:
        _sse_connections[identity] = max(0, _sse_connections.get(identity, 0) - 1)
        if not _sse_connections.get(identity):
            _sse_connections.pop(identity, None)
        _sse_per_username[uname] = max(0, _sse_per_username.get(uname, 0) - 1)
        if not _sse_per_username.get(uname):
            _sse_per_username.pop(uname, None)


async def _sse_generator_multi(usernames: list[str], identity: str):
    """
    Yields SSE events for multiple usernames over a single connection.

    Each event carries `username` so the client knows which streamer changed.
    Change-only per username — identical semantics to _sse_generator.

    One asyncio.Queue is shared across per-user tasks. The generator exits
    automatically when all tasks have finished (all users permanently errored).
    Uses put_nowait() to remain safe inside task finally-blocks after cancellation.

    The outer try/finally decrements _sse_connections (once for the whole
    connection) and _sse_per_username for each watched username.
    """
    lower = [u.lower() for u in usernames]
    queue: asyncio.Queue = asyncio.Queue()
    last_live: dict[str, bool | None] = {u: None for u in usernames}
    had_error: dict[str, bool] = {u: False for u in usernames}
    remaining = [len(usernames)]

    async def poll_one(uname: str):
        poll_count = 0
        try:
            while True:
                try:
                    status = await get_live_status_sse(uname)
                    poll_count += 1
                    changed = last_live[uname] is None or status.is_live != last_live[uname]
                    if changed or had_error[uname]:
                        queue.put_nowait(f"data: {status.model_dump_json()}\n\n")
                        last_live[uname] = status.is_live
                        had_error[uname] = False
                    elif poll_count % HEARTBEAT_EVERY == 0:
                        queue.put_nowait(": heartbeat\n\n")
                except TikTokUserNotFound as e:
                    queue.put_nowait(
                        f"data: {json.dumps({'username': uname, 'error': str(e)})}\n\n"
                    )
                    return
                except TikTokAPIError as e:
                    had_error[uname] = True
                    poll_count += 1
                    queue.put_nowait(
                        f"data: {json.dumps({'username': uname, 'error': str(e), 'transient': True})}\n\n"
                    )
                except asyncio.CancelledError:
                    return
                await asyncio.sleep(POLL_INTERVAL)
        finally:
            remaining[0] -= 1
            if remaining[0] == 0:
                queue.put_nowait(_MULTI_DONE)

    tasks = [asyncio.create_task(poll_one(u)) for u in usernames]
    try:
        try:
            while True:
                item = await queue.get()
                if item is _MULTI_DONE:
                    return
                yield item
        except asyncio.CancelledError:
            pass
        finally:
            for t in tasks:
                t.cancel()
    finally:
        _sse_connections[identity] = max(0, _sse_connections.get(identity, 0) - 1)
        if not _sse_connections.get(identity):
            _sse_connections.pop(identity, None)
        for uname in lower:
            _sse_per_username[uname] = max(0, _sse_per_username.get(uname, 0) - 1)
            if not _sse_per_username.get(uname):
                _sse_per_username.pop(uname, None)
