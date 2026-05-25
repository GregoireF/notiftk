"""
Tests for HTTP endpoints (REST + SSE).

REST tests use FastAPI's synchronous TestClient — simple, zero boilerplate.
SSE tests use two strategies:
  - httpx.AsyncClient + ASGITransport  → HTTP-level assertions (status, headers)
  - Direct _sse_generator testing      → payload and error-handling logic

Why the split? TestClient's synchronous mode wraps async in threads and
doesn't play well with infinite async-generator + asyncio.sleep. Async
client or direct generator access avoids that entirely.
"""

import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.responses import StreamingResponse
from fastapi.testclient import TestClient

from api.main import app, _sse_generator, live_stream, live_stream_multi, HEARTBEAT_EVERY
from api.models import LiveStatus
from api.tiktok import TikTokUserNotFound, TikTokAPIError
from api.webhooks import _watchers

# Synchronous client for REST endpoints
sync_client = TestClient(app)


# ── Helpers ───────────────────────────────────────────────────────────────────


def _mock_request(ip: str = "testclient") -> MagicMock:
    """Minimal FastAPI Request stub for direct handler calls."""
    req = MagicMock()
    req.headers.get.return_value = None  # no X-Forwarded-For
    req.client.host = ip
    return req


def _mock_status(is_live: bool = False) -> LiveStatus:
    return LiveStatus(
        username="testuser",
        is_live=is_live,
        room_id="123" if is_live else None,
        viewer_count=500 if is_live else None,
        title="Test stream" if is_live else None,
    )


# ── REST endpoint ─────────────────────────────────────────────────────────────


class TestLiveStatusEndpoint:
    def test_offline_user_returns_200(self):
        with patch("api.main.get_live_status", AsyncMock(return_value=_mock_status(is_live=False))):
            r = sync_client.get("/api/status/testuser")
        assert r.status_code == 200
        body = r.json()
        assert body["is_live"] is False
        assert body["room_id"] is None
        assert body["viewer_count"] is None

    def test_live_user_returns_all_fields(self):
        with patch("api.main.get_live_status", AsyncMock(return_value=_mock_status(is_live=True))):
            r = sync_client.get("/api/status/testuser")
        assert r.status_code == 200
        body = r.json()
        assert body["is_live"] is True
        assert body["room_id"] == "123"
        assert body["viewer_count"] == 500
        assert body["title"] == "Test stream"

    def test_user_not_found_returns_404(self):
        with patch("api.main.get_live_status", AsyncMock(side_effect=TikTokUserNotFound("nope"))):
            r = sync_client.get("/api/status/ghost")
        assert r.status_code == 404
        assert "error" in r.json()

    def test_api_error_returns_502(self):
        with patch("api.main.get_live_status", AsyncMock(side_effect=TikTokAPIError("boom"))):
            r = sync_client.get("/api/status/testuser")
        assert r.status_code == 502
        assert "error" in r.json()

    def test_cors_header_present(self):
        r = sync_client.get("/api/status/testuser", headers={"Origin": "http://example.com"})
        assert r.headers.get("access-control-allow-origin") == "*"

    def test_invalid_username_returns_422(self):
        r = sync_client.get("/api/status/" + "a" * 25)  # 25 chars — exceeds 24 max
        assert r.status_code == 422

    def test_username_with_special_chars_returns_422(self):
        r = sync_client.get("/api/status/user@name!")
        assert r.status_code == 422


# ── SSE endpoint — response object tests (headers, content-type) ─────────────
# ASGITransport buffers the full response body before returning, making it
# incompatible with infinite SSE generators. We test the StreamingResponse
# object directly instead — same guarantees, no transport involved.
# CORS is verified via the REST endpoint (same middleware, redundant to repeat).


@pytest.mark.asyncio
async def test_sse_returns_streaming_response():
    """live_stream() must return a StreamingResponse, not a JSON response."""
    response = await live_stream("testuser", request=_mock_request(), _key=None)
    assert isinstance(response, StreamingResponse)


@pytest.mark.asyncio
async def test_sse_content_type():
    """SSE endpoint must advertise text/event-stream."""
    response = await live_stream("testuser", request=_mock_request(), _key=None)
    assert response.media_type == "text/event-stream"


@pytest.mark.asyncio
async def test_sse_no_cache_control():
    """SSE response must carry Cache-Control: no-cache."""
    response = await live_stream("testuser", request=_mock_request(), _key=None)
    assert response.headers.get("cache-control") == "no-cache"


@pytest.mark.asyncio
async def test_sse_nginx_buffering_disabled():
    """X-Accel-Buffering: no prevents nginx from holding back SSE events."""
    response = await live_stream("testuser", request=_mock_request(), _key=None)
    assert response.headers.get("x-accel-buffering") == "no"


# ── SSE generator — logic (payload shape, error handling) ────────────────────
# Tests _sse_generator directly to verify JSON shape and error surfacing
# without spinning up HTTP connections or fighting with asyncio.sleep.


@pytest.mark.asyncio
async def test_sse_generator_first_event_is_valid_json():
    """First event must be parseable JSON with the expected fields."""
    with patch("api.main.get_live_status_sse", AsyncMock(return_value=_mock_status(is_live=True))):
        gen = _sse_generator("testuser", "testclient")
        line = await gen.__anext__()  # read first yield, generator pauses BEFORE sleep
        await gen.aclose()  # GeneratorExit — clean shutdown

    assert line.startswith("data: ")
    data = json.loads(line[6:])
    assert data["is_live"] is True
    assert data["viewer_count"] == 500
    assert data["title"] == "Test stream"


@pytest.mark.asyncio
async def test_sse_generator_user_not_found_surfaces_error_and_stops():
    """TikTokUserNotFound should be emitted as a JSON error event, then the generator exits."""
    with patch(
        "api.main.get_live_status_sse",
        AsyncMock(side_effect=TikTokUserNotFound("User does not exist")),
    ):
        gen = _sse_generator("ghost", "testclient")
        line = await gen.__anext__()  # error event

        # Generator should be exhausted (returned) after a fatal error
        with pytest.raises(StopAsyncIteration):
            await gen.__anext__()

    data = json.loads(line[6:])
    assert "error" in data
    assert "transient" not in data  # fatal, not transient


@pytest.mark.asyncio
async def test_sse_generator_transient_error_continues():
    """TikTokAPIError should emit a transient error event and keep the generator alive."""
    statuses = [
        TikTokAPIError("flap"),
        _mock_status(is_live=True),
    ]
    call_count = 0

    async def side_effect(_):
        nonlocal call_count
        result = statuses[call_count]
        call_count += 1
        if isinstance(result, Exception):
            raise result
        return result

    with patch("api.main.get_live_status_sse", side_effect):
        with patch("api.main.asyncio.sleep", AsyncMock()):
            gen = _sse_generator("testuser", "testclient")
            error_line = await gen.__anext__()  # transient error event
            status_line = await gen.__anext__()  # recovery event
            await gen.aclose()

    error_data = json.loads(error_line[6:])
    assert "error" in error_data
    assert error_data.get("transient") is True

    status_data = json.loads(status_line[6:])
    assert status_data["is_live"] is True


@pytest.mark.asyncio
async def test_sse_generator_offline_user():
    """Offline user should emit is_live=false with null fields."""
    with patch("api.main.get_live_status_sse", AsyncMock(return_value=_mock_status(is_live=False))):
        gen = _sse_generator("testuser", "testclient")
        line = await gen.__anext__()
        await gen.aclose()

    data = json.loads(line[6:])
    assert data["is_live"] is False
    assert data["room_id"] is None


@pytest.mark.asyncio
async def test_sse_generator_no_duplicate_events():
    """Same is_live value on consecutive polls should not produce a second data event."""
    call_count = 0

    async def same_status(_):
        nonlocal call_count
        call_count += 1
        return _mock_status(is_live=True)

    events = []
    with (
        patch("api.main.get_live_status_sse", same_status),
        patch("api.main.asyncio.sleep", AsyncMock()),
    ):
        gen = _sse_generator("testuser", "testclient")
        # Read enough iterations to cover HEARTBEAT_EVERY polls without a change
        for _ in range(HEARTBEAT_EVERY + 1):
            try:
                events.append(await gen.__anext__())
            except StopAsyncIteration:
                break
        await gen.aclose()

    data_events = [e for e in events if e.startswith("data: ")]
    heartbeats = [e for e in events if e.startswith(": heartbeat")]
    # Only one data event (the first), the rest are heartbeats
    assert len(data_events) == 1
    assert len(heartbeats) >= 1


@pytest.mark.asyncio
async def test_sse_generator_heartbeat_format():
    """Heartbeat events must use SSE comment format: ': heartbeat\\n\\n'."""
    call_count = 0

    async def same_status(_):
        nonlocal call_count
        call_count += 1
        return _mock_status(is_live=False)

    events = []
    with (
        patch("api.main.get_live_status_sse", same_status),
        patch("api.main.asyncio.sleep", AsyncMock()),
    ):
        gen = _sse_generator("testuser", "testclient")
        for _ in range(HEARTBEAT_EVERY + 1):
            try:
                events.append(await gen.__anext__())
            except StopAsyncIteration:
                break
        await gen.aclose()

    heartbeats = [e for e in events if e.startswith(": heartbeat")]
    assert heartbeats[0] == ": heartbeat\n\n"


# ── Webhook HTTP endpoints ────────────────────────────────────────────────────


class TestWatchEndpoints:
    def test_post_watch_creates_watcher(self):
        with patch("api.main.register_webhook", AsyncMock(return_value="test-watch-id")):
            r = sync_client.post(
                "/api/watch",
                json={"username": "ninja", "callback_url": "https://example.com/hook"},
            )
        assert r.status_code == 201
        body = r.json()
        assert body["watch_id"] == "test-watch-id"
        assert body["username"] == "ninja"

    def test_post_watch_invalid_username_returns_422(self):
        r = sync_client.post(
            "/api/watch",
            json={"username": "bad user!", "callback_url": "https://example.com/hook"},
        )
        assert r.status_code == 422

    def test_post_watch_invalid_url_returns_422(self):
        r = sync_client.post(
            "/api/watch",
            json={"username": "ninja", "callback_url": "not-a-url"},
        )
        assert r.status_code == 422

    def test_delete_watch_removes_watcher(self):
        with patch("api.main.unregister_webhook", AsyncMock(return_value=True)):
            r = sync_client.delete("/api/watch/test-watch-id")
        assert r.status_code == 204

    def test_delete_watch_unknown_id_returns_404(self):
        with patch("api.main.unregister_webhook", AsyncMock(return_value=False)):
            r = sync_client.delete("/api/watch/unknown")
        assert r.status_code == 404

    def test_post_watch_limit_exceeded_returns_429(self):
        from api.webhooks import WebhookLimitExceeded

        with patch(
            "api.main.register_webhook", AsyncMock(side_effect=WebhookLimitExceeded("Limit of 5"))
        ):
            r = sync_client.post(
                "/api/watch",
                json={"username": "ninja", "callback_url": "https://example.com/hook"},
            )
        assert r.status_code == 429

    def test_post_watch_ssrf_returns_422(self):
        from api.webhooks import WebhookURLError

        with patch(
            "api.main.register_webhook", AsyncMock(side_effect=WebhookURLError("private IP"))
        ):
            r = sync_client.post(
                "/api/watch",
                json={"username": "ninja", "callback_url": "https://example.com/hook"},
            )
        assert r.status_code == 422

    def test_get_watches_returns_list(self):
        from api.webhooks import _Watcher

        _watchers["test-id"] = _Watcher(
            watch_id="test-id",
            username="ninja",
            callback_url="https://example.com/hook",
            secret=None,
        )
        try:
            r = sync_client.get("/api/watches")
        finally:
            _watchers.pop("test-id", None)
        assert r.status_code == 200
        body = r.json()
        assert any(w["watch_id"] == "test-id" for w in body)

    def test_get_watches_empty_list(self):
        _watchers.clear()
        r = sync_client.get("/api/watches")
        assert r.status_code == 200
        assert r.json() == []

    def test_health_includes_active_webhooks_count(self):
        r = sync_client.get("/health")
        assert r.status_code == 200
        assert "active_webhooks" in r.json()

    def test_post_watch_short_secret_returns_422(self):
        """Secrets shorter than 8 chars should be rejected by the model validator."""
        r = sync_client.post(
            "/api/watch",
            json={
                "username": "ninja",
                "callback_url": "https://example.com/hook",
                "secret": "short",
            },
        )
        assert r.status_code == 422


# ── SSE multi-username edge cases ─────────────────────────────────────────────


class TestMultiSSE:
    def test_empty_users_param_returns_422(self):
        r = sync_client.get("/api/stream?users=")
        assert r.status_code == 422

    def test_too_many_users_returns_422(self):
        users = ",".join([f"user{i}" for i in range(11)])
        r = sync_client.get(f"/api/stream?users={users}")
        assert r.status_code == 422

    def test_invalid_username_in_list_returns_422(self):
        r = sync_client.get("/api/stream?users=ninja,bad user!")
        assert r.status_code == 422

    @pytest.mark.asyncio
    async def test_valid_multi_returns_streaming_response(self):
        response = await live_stream_multi(
            users="ninja,pokimane", request=_mock_request(), _key=None
        )
        assert isinstance(response, StreamingResponse)
        assert response.media_type == "text/event-stream"


# ── Health endpoint fields ────────────────────────────────────────────────────


class TestHealthFields:
    def test_health_has_timestamp(self):
        """Health must include a Unix timestamp for detecting stale proxy responses."""
        r = sync_client.get("/health")
        body = r.json()
        assert "timestamp" in body
        assert isinstance(body["timestamp"], float)
        assert body["timestamp"] > 0

    def test_health_has_active_sse_connections(self):
        """Health must report total open SSE connections."""
        r = sync_client.get("/health")
        body = r.json()
        assert "active_sse_connections" in body
        assert isinstance(body["active_sse_connections"], int)


# ── SSE connection cap ────────────────────────────────────────────────────────


class TestSSECap:
    def test_sse_per_key_cap_returns_429(self):
        """Exceeding SSE_MAX_PER_KEY for the same identity returns 429."""
        import api.main as main_module

        identity = "cap-test-key"
        main_module._sse_connections[identity] = 999  # saturate the counter
        try:
            with patch("api.main.SSE_MAX_PER_KEY", 1):
                r = sync_client.get(
                    "/api/stream/ninja", headers={"X-Forwarded-For": identity}
                )
        finally:
            main_module._sse_connections.pop(identity, None)
        assert r.status_code == 429

    def test_sse_per_username_cap_returns_429(self):
        """Exceeding SSE_MAX_PER_USERNAME for a username returns 429."""
        import api.main as main_module

        main_module._sse_per_username["ninja"] = 999
        try:
            with patch("api.main.SSE_MAX_PER_USERNAME", 1):
                r = sync_client.get("/api/stream/ninja")
        finally:
            main_module._sse_per_username.pop("ninja", None)
        assert r.status_code == 429


# ── Delivery history endpoint ─────────────────────────────────────────────────


class TestDeliveryHistoryEndpoint:
    def test_unknown_watch_id_returns_404(self):
        r = sync_client.get("/api/watch/does-not-exist/deliveries")
        assert r.status_code == 404

    def test_known_watch_id_returns_list(self):
        from api.webhooks import _watchers, _delivery_history, _Watcher
        from collections import deque

        watch_id = "test-delivery-id"
        _watchers[watch_id] = _Watcher(
            watch_id=watch_id,
            username="ninja",
            callback_url="https://example.com/hook",
            secret=None,
        )
        buf = deque(maxlen=20)
        import time as _time
        buf.append(
            __import__("api.webhooks", fromlist=["_DeliveryRecord"])._DeliveryRecord(
                timestamp=_time.time(), http_status=200, attempt_count=1, success=True
            )
        )
        _delivery_history[watch_id] = buf
        try:
            r = sync_client.get(f"/api/watch/{watch_id}/deliveries")
        finally:
            _watchers.pop(watch_id, None)
            _delivery_history.pop(watch_id, None)

        assert r.status_code == 200
        records = r.json()
        assert len(records) == 1
        assert records[0]["success"] is True
        assert records[0]["http_status"] == 200
        assert records[0]["attempt_count"] == 1


# ── Admin watches endpoint ────────────────────────────────────────────────────


class TestAdminWatchesEndpoint:
    _ADMIN = "test-admin-secret-32chars-padded!!"

    def test_no_secret_returns_404(self):
        r = sync_client.get("/api/admin/watches")
        assert r.status_code == 404

    def test_wrong_secret_returns_404(self):
        with patch("api.main.ADMIN_SECRET", self._ADMIN):
            r = sync_client.get(
                "/api/admin/watches", headers={"X-Admin-Secret": "wrong"}
            )
        assert r.status_code == 404

    def test_correct_secret_returns_all_watches(self):
        from api.webhooks import _watchers, _Watcher

        watch_id = "admin-watch-test"
        _watchers[watch_id] = _Watcher(
            watch_id=watch_id,
            username="ninja",
            callback_url="https://example.com/hook",
            secret=None,
            owner_key_hash="deadbeef",
        )
        try:
            with patch("api.main.ADMIN_SECRET", self._ADMIN):
                r = sync_client.get(
                    "/api/admin/watches", headers={"X-Admin-Secret": self._ADMIN}
                )
        finally:
            _watchers.pop(watch_id, None)

        assert r.status_code == 200
        body = r.json()
        entry = next((w for w in body if w["watch_id"] == watch_id), None)
        assert entry is not None
        assert entry["owner_key_hash"] == "deadbeef"

    def test_secrets_never_exposed(self):
        """owner secret must not appear in admin/watches response."""
        from api.webhooks import _watchers, _Watcher

        watch_id = "secret-watch-test"
        _watchers[watch_id] = _Watcher(
            watch_id=watch_id,
            username="ninja",
            callback_url="https://example.com/hook",
            secret="super-secret-value",
        )
        try:
            with patch("api.main.ADMIN_SECRET", self._ADMIN):
                r = sync_client.get(
                    "/api/admin/watches", headers={"X-Admin-Secret": self._ADMIN}
                )
        finally:
            _watchers.pop(watch_id, None)

        assert r.status_code == 200
        raw = r.text
        assert "super-secret-value" not in raw
