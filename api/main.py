"""
NotiTK — TikTok live status API.

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
      Register a webhook: NotiTK POSTs to your URL whenever is_live changes.
      No persistent connection needed. Optional HMAC-SHA256 signing.

  DELETE /api/watch/{watch_id}
      Unregister a previously registered webhook.

  GET /api/watches
      List all active webhooks (watch_id, username, callback_url).

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
    WEBHOOKS_DB           SQLite path for webhook persistence (default: data/webhooks.db)
    MAX_WEBHOOKS          Global webhook cap (default: 100)
    MAX_WEBHOOKS_PER_USER Per-username webhook cap (default: 5)
"""

import asyncio
import json
import re
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse

from .auth import api_key_dependency, AUTH_ENABLED
from .keys import generate_key, list_keys, revoke_key, ADMIN_SECRET
from .models import LiveStatus, ErrorResponse, WatchRequest, WatchResponse
from .webhooks import (
    register_webhook,
    unregister_webhook,
    list_webhooks,
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


@asynccontextmanager
async def lifespan(_app: FastAPI):
    await restore_webhooks()
    yield
    await shutdown_webhooks()


app = FastAPI(
    title="NotiTK",
    description=__doc__,
    version="0.3.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["*"],
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


@app.get("/health", summary="Liveness check", tags=["Meta"])
async def health():
    """
    Returns 200 when the server process is alive.

    Does **not** hit TikTok's API — this is a liveness probe for load
    balancers (Fly.io, Docker, k8s). Use `/api/status/{username}` to verify
    TikTok connectivity.
    """
    return {
        "status": "ok",
        "version": app.version,
        "auth_enabled": AUTH_ENABLED,
        "cache_ttl_seconds": CACHE_TTL,
        "poll_interval_seconds": POLL_INTERVAL,
        "active_webhooks": len(list_webhooks()),
    }


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
        429: {"description": "Rate limit exceeded"},
    },
    dependencies=[_auth],
)
async def live_stream(username: str):
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
    return StreamingResponse(
        _sse_generator(username), media_type="text/event-stream", headers=_SSE_HEADERS
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
        429: {"description": "Rate limit exceeded"},
    },
    dependencies=[_auth],
)
async def live_stream_multi(
    users: Annotated[
        str,
        Query(description=f"Comma-separated TikTok usernames (max {_MAX_USERS})"),
    ],
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
    return StreamingResponse(
        _sse_generator_multi(usernames), media_type="text/event-stream", headers=_SSE_HEADERS
    )


# ── Self-service key endpoints ────────────────────────────────────────────────


def _client_ip(request: Request) -> str:
    xff = request.headers.get("x-forwarded-for")
    return (
        xff.split(",")[0].strip()
        if xff
        else (request.client.host if request.client else "unknown")
    )


def _require_admin(x_admin_secret: Annotated[str | None, Header(alias="X-Admin-Secret")] = None):
    if not ADMIN_SECRET or x_admin_secret != ADMIN_SECRET:
        raise HTTPException(status_code=404)


@app.post(
    "/api/keys",
    status_code=201,
    summary="Generate a self-service API key",
    tags=["Keys"],
)
async def create_key(request: Request, label: str | None = None):
    """
    Generate a new API key. **The key is shown only once — save it immediately.**

    Rate-limited: max 3 keys per IP per 24 hours.

    Pass an optional `label` query param to identify the key (e.g. `?label=my-bot`).
    """
    ip = _client_ip(request)
    try:
        key_id, raw_key = generate_key(ip, label)
    except ValueError as e:
        raise HTTPException(status_code=429, detail=str(e))
    return {
        "id": key_id,
        "key": raw_key,
        "warning": "Copiez cette clé maintenant — elle ne sera plus affichée.",
    }


@app.get(
    "/api/admin/keys",
    summary="List all API keys (admin)",
    tags=["Admin"],
    dependencies=[Depends(_require_admin)],
)
async def admin_list_keys():
    """List all generated keys with their metadata. Requires `X-Admin-Secret` header."""
    return list_keys()


@app.delete(
    "/api/admin/keys/{key_id}",
    status_code=204,
    summary="Revoke an API key (admin)",
    tags=["Admin"],
    dependencies=[Depends(_require_admin)],
)
async def admin_revoke_key(key_id: str):
    """Revoke a key by its ID. The key immediately stops working."""
    if not revoke_key(key_id):
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
    dependencies=[_auth],
)
async def watch(body: WatchRequest):
    """
    Register a callback URL POSTed whenever *username* goes live or offline.

    ```json
    { "username": "ninja", "callback_url": "https://your-bot.example.com/hook" }
    ```

    The POST body is identical to the REST status endpoint response.
    Pass `secret` to enable HMAC-SHA256 signing (`X-NotiTK-Signature: sha256=<hex>`).

    Returns a `watch_id` — keep it to unregister later with `DELETE /api/watch/{watch_id}`.
    """
    _validate_username(body.username)
    try:
        watch_id = await register_webhook(body.username, str(body.callback_url), body.secret)
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
        404: {"description": "Watch ID not found"},
        429: {"description": "Rate limit exceeded"},
    },
    summary="Unregister a webhook",
    tags=["Webhooks"],
    dependencies=[_auth],
)
async def unwatch(watch_id: str):
    """Remove the webhook registered under *watch_id*."""
    removed = await unregister_webhook(watch_id)
    if not removed:
        raise HTTPException(status_code=404, detail=f"watch_id '{watch_id}' not found.")


@app.get(
    "/api/watches",
    response_model=list[WatchResponse],
    summary="List active webhooks",
    tags=["Webhooks"],
    dependencies=[_auth],
)
async def watches():
    """Return all currently registered webhooks. Secrets are never included."""
    return [
        WatchResponse(watch_id=w.watch_id, username=w.username, callback_url=w.callback_url)
        for w in list_webhooks()
    ]


# ── SSE generators ────────────────────────────────────────────────────────────


async def _sse_generator(username: str):
    """
    Yields SSE events only when is_live changes (plus periodic heartbeats).

    Protocol:
      - `data: <JSON>`    on first poll, on every is_live flip, and after a transient error clears
      - `: heartbeat`     comment every HEARTBEAT_EVERY polls (keeps proxies from timing out)
      - Fatal error:      `data: {"error": ...}` then generator stops
      - Transient error:  `data: {"error": ..., "transient": true}` then keep going
    """
    last_live: bool | None = None
    had_error = False
    poll_count = 0

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


async def _sse_generator_multi(usernames: list[str]):
    """
    Yields SSE events for multiple usernames over a single connection.

    Each event carries `username` so the client knows which streamer changed.
    Change-only per username — identical semantics to _sse_generator.

    One asyncio.Queue is shared across per-user tasks. The generator exits
    automatically when all tasks have finished (all users permanently errored).
    Uses put_nowait() to remain safe inside task finally-blocks after cancellation.
    """
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
