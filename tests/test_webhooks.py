"""
Tests for webhook registration, dispatch, persistence, and limits.

All tests mock TikTok calls and httpx — no network access needed.
DB isolation and task cleanup are handled by the clean_webhooks fixture
in conftest.py (autouse, async).
"""

import asyncio
import hashlib
import hmac
import json
import sqlite3
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

import api.webhooks as wh
from api.models import LiveStatus
from api.webhooks import (
    register_webhook,
    unregister_webhook,
    list_webhooks,
    restore_webhooks,
    _dispatch,
    _poll_loop,
    _watchers,
    _Watcher,
    WebhookLimitExceeded,
    WebhookURLError,
)


def _live_status(is_live: bool = True) -> LiveStatus:
    return LiveStatus(
        username="testuser",
        is_live=is_live,
        room_id="123" if is_live else None,
        viewer_count=100 if is_live else None,
    )


# ── Registration & unregistration ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_register_returns_watch_id():
    with patch("api.webhooks.get_live_status_sse", AsyncMock(return_value=_live_status())):
        watch_id = await register_webhook("testuser", "https://example.com/hook")
    assert watch_id
    assert watch_id in _watchers


@pytest.mark.asyncio
async def test_register_normalises_username():
    with patch("api.webhooks.get_live_status_sse", AsyncMock(return_value=_live_status())):
        watch_id = await register_webhook("TestUser", "https://example.com/hook")
    assert _watchers[watch_id].username == "testuser"


@pytest.mark.asyncio
async def test_unregister_removes_watcher():
    with patch("api.webhooks.get_live_status_sse", AsyncMock(return_value=_live_status())):
        watch_id = await register_webhook("testuser", "https://example.com/hook")
    removed = await unregister_webhook(watch_id)
    assert removed is True
    assert watch_id not in _watchers


@pytest.mark.asyncio
async def test_unregister_unknown_id_returns_false():
    removed = await unregister_webhook("does-not-exist")
    assert removed is False


# ── Webhook limits ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_per_user_limit_raises():
    """Registering more than MAX_WEBHOOKS_PER_USER hooks for one user should raise."""
    original = wh.MAX_WEBHOOKS_PER_USER
    wh.MAX_WEBHOOKS_PER_USER = 2
    try:
        with patch("api.webhooks.get_live_status_sse", AsyncMock(return_value=_live_status())):
            await register_webhook("ninja", "https://a.example.com/hook")
            await register_webhook("ninja", "https://b.example.com/hook")
            with pytest.raises(WebhookLimitExceeded, match="Limit"):
                await register_webhook("ninja", "https://c.example.com/hook")
    finally:
        wh.MAX_WEBHOOKS_PER_USER = original


@pytest.mark.asyncio
async def test_global_limit_raises():
    """Registering more than MAX_WEBHOOKS_TOTAL webhooks should raise."""
    original = wh.MAX_WEBHOOKS_TOTAL
    wh.MAX_WEBHOOKS_TOTAL = 2
    try:
        with patch("api.webhooks.get_live_status_sse", AsyncMock(return_value=_live_status())):
            await register_webhook("user1", "https://a.example.com/hook")
            await register_webhook("user2", "https://b.example.com/hook")
            with pytest.raises(WebhookLimitExceeded, match="Global"):
                await register_webhook("user3", "https://c.example.com/hook")
    finally:
        wh.MAX_WEBHOOKS_TOTAL = original


# ── SSRF protection ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_ssrf_localhost_blocked():
    with pytest.raises(WebhookURLError, match="not allowed"):
        await register_webhook("ninja", "http://localhost/hook")


@pytest.mark.asyncio
async def test_ssrf_private_ip_blocked():
    with pytest.raises(WebhookURLError, match="public"):
        await register_webhook("ninja", "http://192.168.1.1/hook")


@pytest.mark.asyncio
async def test_ssrf_loopback_blocked():
    with pytest.raises(WebhookURLError, match="public"):
        await register_webhook("ninja", "http://127.0.0.1/hook")


@pytest.mark.asyncio
async def test_ssrf_aws_metadata_blocked():
    with pytest.raises(WebhookURLError, match="public"):
        await register_webhook("ninja", "http://169.254.169.254/latest/meta-data/")


@pytest.mark.asyncio
async def test_ssrf_public_url_allowed():
    with patch("api.webhooks.get_live_status_sse", AsyncMock(return_value=_live_status())):
        watch_id = await register_webhook("ninja", "https://hooks.example.com/notify")
    assert watch_id in _watchers


# ── list_webhooks ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_webhooks_returns_all():
    with patch("api.webhooks.get_live_status_sse", AsyncMock(return_value=_live_status())):
        w1 = await register_webhook("ninja", "https://a.example.com/hook")
        w2 = await register_webhook("pokimane", "https://b.example.com/hook")
    ids = {w.watch_id for w in list_webhooks()}
    assert w1 in ids
    assert w2 in ids


@pytest.mark.asyncio
async def test_list_webhooks_empty_when_none_registered():
    assert list_webhooks() == []


# ── SQLite persistence ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_register_persists_to_db():
    with patch("api.webhooks.get_live_status_sse", AsyncMock(return_value=_live_status())):
        watch_id = await register_webhook("ninja", "https://example.com/hook", secret="s3cr3t")
    with sqlite3.connect(wh._DB_PATH) as conn:
        row = conn.execute(
            "SELECT watch_id, username, callback_url, secret FROM webhooks WHERE watch_id = ?",
            (watch_id,),
        ).fetchone()
    assert row is not None
    assert row[1] == "ninja"
    assert row[2] == "https://example.com/hook"
    assert row[3] == "s3cr3t"


@pytest.mark.asyncio
async def test_unregister_deletes_from_db():
    with patch("api.webhooks.get_live_status_sse", AsyncMock(return_value=_live_status())):
        watch_id = await register_webhook("ninja", "https://example.com/hook")
    await unregister_webhook(watch_id)
    with sqlite3.connect(wh._DB_PATH) as conn:
        row = conn.execute(
            "SELECT watch_id FROM webhooks WHERE watch_id = ?", (watch_id,)
        ).fetchone()
    assert row is None


@pytest.mark.asyncio
async def test_restore_webhooks_reloads_from_db():
    """restore_webhooks() should recreate watchers from DB rows."""
    with patch("api.webhooks.get_live_status_sse", AsyncMock(return_value=_live_status())):
        watch_id = await register_webhook("ninja", "https://example.com/hook")

    # Simulate a restart: cancel tasks + clear memory, keep DB
    for w in list(_watchers.values()):
        if w.task and not w.task.done():
            w.task.cancel()
            try:
                await w.task
            except (asyncio.CancelledError, Exception):
                pass
    _watchers.clear()
    assert watch_id not in _watchers

    with patch("api.webhooks.get_live_status_sse", AsyncMock(return_value=_live_status())):
        await restore_webhooks()

    assert watch_id in _watchers
    assert _watchers[watch_id].username == "ninja"


# ── Dispatch ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_dispatch_posts_json():
    watcher = _Watcher(
        watch_id="w1", username="testuser", callback_url="https://cb.example.com/hook", secret=None
    )
    status = _live_status(is_live=True)

    mock_response = MagicMock()
    mock_response.is_success = True
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = AsyncMock(return_value=mock_response)

    with patch("api.webhooks.httpx.AsyncClient", return_value=mock_client):
        result = await _dispatch(watcher, status)

    assert result is True
    body = json.loads(mock_client.post.call_args.kwargs["content"])
    assert body["is_live"] is True
    assert body["username"] == "testuser"


@pytest.mark.asyncio
async def test_dispatch_includes_hmac_signature_when_secret_set():
    watcher = _Watcher(
        watch_id="w2",
        username="testuser",
        callback_url="https://cb.example.com/hook",
        secret="mysecret",
    )
    status = _live_status(is_live=True)
    payload = status.model_dump_json().encode()
    expected_sig = "sha256=" + hmac.new(b"mysecret", payload, hashlib.sha256).hexdigest()

    mock_response = MagicMock()
    mock_response.is_success = True
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = AsyncMock(return_value=mock_response)

    with patch("api.webhooks.httpx.AsyncClient", return_value=mock_client):
        await _dispatch(watcher, status)

    headers = mock_client.post.call_args.kwargs["headers"]
    assert headers.get("X-NotiTFK-Signature") == expected_sig


@pytest.mark.asyncio
async def test_dispatch_returns_false_on_exception():
    watcher = _Watcher(
        watch_id="w3", username="testuser", callback_url="https://cb.example.com/hook", secret=None
    )
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = AsyncMock(side_effect=Exception("connection refused"))

    with patch("api.webhooks.httpx.AsyncClient", return_value=mock_client):
        result = await _dispatch(watcher, _live_status())

    assert result is False


@pytest.mark.asyncio
async def test_dispatch_returns_false_on_non_2xx():
    watcher = _Watcher(
        watch_id="w4", username="testuser", callback_url="https://cb.example.com/hook", secret=None
    )
    mock_response = MagicMock()
    mock_response.is_success = False
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = AsyncMock(return_value=mock_response)

    with patch("api.webhooks.httpx.AsyncClient", return_value=mock_client):
        result = await _dispatch(watcher, _live_status())

    assert result is False


# ── Poll loop ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_poll_loop_dispatches_on_change():
    statuses = [_live_status(False), _live_status(True)]
    call_count = 0

    async def fake_get_status(_):
        nonlocal call_count
        s = statuses[min(call_count, len(statuses) - 1)]
        call_count += 1
        return s

    watcher = _Watcher(
        watch_id="w5", username="testuser", callback_url="https://cb.example.com/hook", secret=None
    )
    dispatch_calls = []

    async def fake_dispatch(w, s):
        dispatch_calls.append(s.is_live)
        return True

    with (
        patch("api.webhooks.get_live_status_sse", fake_get_status),
        patch("api.webhooks._dispatch", fake_dispatch),
        patch(
            "api.webhooks.asyncio.sleep", AsyncMock(side_effect=[None, asyncio.CancelledError()])
        ),
    ):
        try:
            await _poll_loop(watcher)
        except asyncio.CancelledError:
            pass

    assert len(dispatch_calls) >= 1


@pytest.mark.asyncio
async def test_poll_loop_stops_after_max_failures():
    """After MAX_DISPATCH_FAILURES consecutive failures, watcher is removed."""
    _watchers["w6"] = _Watcher(
        watch_id="w6", username="testuser", callback_url="https://cb.example.com/hook", secret=None
    )
    watcher = _watchers["w6"]

    call_count = 0

    async def alternating_status(_):
        nonlocal call_count
        call_count += 1
        return _live_status(call_count % 2 == 1)

    with (
        patch("api.webhooks.get_live_status_sse", alternating_status),
        patch("api.webhooks._dispatch", AsyncMock(return_value=False)),
        patch("api.webhooks.asyncio.sleep", AsyncMock()),
    ):
        await _poll_loop(watcher)

    assert "w6" not in _watchers


@pytest.mark.asyncio
async def test_poll_loop_removes_watcher_on_user_not_found():
    from api.tiktok import TikTokUserNotFound

    _watchers["w7"] = _Watcher(
        watch_id="w7", username="gone", callback_url="https://cb.example.com/hook", secret=None
    )
    watcher = _watchers["w7"]

    with patch(
        "api.webhooks.get_live_status_sse", AsyncMock(side_effect=TikTokUserNotFound("gone"))
    ):
        await _poll_loop(watcher)

    assert "w7" not in _watchers
