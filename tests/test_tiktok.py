"""
Tests for TikTok live status detection.

Unit tests mock the TikTokLive web client to avoid network calls.
Integration test (marked 'integration') hits TikTok's real API — skip with:
    pytest -m "not integration"
"""

import asyncio
import time
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from TikTokLive.client.errors import AgeRestrictedError, UserNotFoundError

from api.tiktok import (
    get_live_status,
    get_live_status_sse,
    TikTokUserNotFound,
    TikTokAPIError,
    _cache,
    _sse_cache,
    POLL_INTERVAL,
)
from api.models import LiveStatus


# ── Helpers ──────────────────────────────────────────────────────────────────


def _room_info(status: int, title: str = "", user_count: int = 0) -> dict:
    """Minimal room info dict matching TikTok's real response shape."""
    return {"status": status, "title": title, "user_count": user_count}


def _make_web_mock(room_id, room_info: dict | None = None, error=None) -> MagicMock:
    """
    Build a fake TikTokLive web client.
    - room_id=None → user has never gone live
    - room_id=<int> + room_info → happy path
    - error → fetch_room_id_from_api raises this exception
    """
    web = MagicMock()
    if error:
        web.fetch_room_id_from_api = AsyncMock(side_effect=error)
    else:
        web.fetch_room_id_from_api = AsyncMock(return_value=room_id)
    web.fetch_room_info = AsyncMock(return_value=room_info or {})
    web.httpx_client = MagicMock()
    web.httpx_client.aclose = AsyncMock()
    return web


def _patch_client(web_mock: MagicMock):
    """Patch TikTokLiveClient so it returns our fake web client."""
    mock_client = MagicMock()
    mock_client.web = web_mock
    return patch("api.tiktok.TikTokLiveClient", return_value=mock_client)


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def clear_cache():
    """Wipe all in-memory caches before every test."""
    _cache.clear()
    _sse_cache.clear()
    yield
    _cache.clear()
    _sse_cache.clear()


# ── Unit tests ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_user_is_live():
    """User with status=2 should return is_live=True with viewer count and title."""
    web = _make_web_mock(
        room_id=123456,
        room_info=_room_info(status=2, title="Gaming time!", user_count=4200),
    )
    with _patch_client(web):
        result = await get_live_status("testuser")

    assert result.is_live is True
    assert result.room_id == "123456"
    assert result.viewer_count == 4200
    assert result.title == "Gaming time!"


@pytest.mark.asyncio
async def test_user_is_offline():
    """User with status=4 should return is_live=False with null fields."""
    web = _make_web_mock(room_id=123456, room_info=_room_info(status=4))
    with _patch_client(web):
        result = await get_live_status("testuser")

    assert result.is_live is False
    assert result.room_id is None
    assert result.viewer_count is None
    assert result.title is None


@pytest.mark.asyncio
async def test_user_never_streamed():
    """User with no room ID should return is_live=False without calling fetch_room_info."""
    web = _make_web_mock(room_id=None)
    with _patch_client(web):
        result = await get_live_status("testuser")

    assert result.is_live is False
    web.fetch_room_info.assert_not_called()


@pytest.mark.asyncio
async def test_age_restricted_stream_returns_is_live_true():
    """Age-restricted stream: we know they're live (roomId exists) but can't get details."""
    web = _make_web_mock(room_id=123456)
    web.fetch_room_info = AsyncMock(side_effect=AgeRestrictedError("Age restricted"))
    with _patch_client(web):
        result = await get_live_status("adultstreamer")

    assert result.is_live is True
    assert result.room_id == "123456"
    assert result.viewer_count is None  # details unavailable
    assert result.title is None


@pytest.mark.asyncio
async def test_user_not_found():
    """UserNotFoundError from TikTokLive should be re-raised as TikTokUserNotFound."""
    web = _make_web_mock(room_id=None, error=UserNotFoundError("User not found"))
    with _patch_client(web):
        with pytest.raises(TikTokUserNotFound):
            await get_live_status("nonexistentuser12345")


@pytest.mark.asyncio
async def test_unexpected_error_becomes_api_error():
    """Any unexpected exception should be wrapped as TikTokAPIError."""
    web = _make_web_mock(room_id=None, error=RuntimeError("network failure"))
    with _patch_client(web):
        with pytest.raises(TikTokAPIError):
            await get_live_status("anyuser")


@pytest.mark.asyncio
async def test_timeout_becomes_api_error():
    """A hung TikTok API call should raise TikTokAPIError mentioning timeout."""
    web = _make_web_mock(room_id=None, error=asyncio.TimeoutError())
    with _patch_client(web):
        with pytest.raises(TikTokAPIError, match="timed out"):
            await get_live_status("slowuser")


@pytest.mark.asyncio
async def test_http_client_always_closed():
    """The underlying httpx client must be closed even when an error is raised."""
    web = _make_web_mock(room_id=None, error=UserNotFoundError("nope"))
    with _patch_client(web):
        with pytest.raises(TikTokUserNotFound):
            await get_live_status("anyuser")

    web.httpx_client.aclose.assert_called_once()


@pytest.mark.asyncio
async def test_cache_hit_skips_api():
    """A second call within TTL should not make any HTTP requests."""
    web = _make_web_mock(room_id=123456, room_info=_room_info(status=2, user_count=100))
    with _patch_client(web):
        first = await get_live_status("cacheduser")
        second = await get_live_status("cacheduser")

    assert first == second
    # fetch_room_id_from_api should only have been called once
    assert web.fetch_room_id_from_api.call_count == 1


@pytest.mark.asyncio
async def test_cache_expired_triggers_new_request():
    """After TTL expires, the next call should hit the API again."""
    web = _make_web_mock(room_id=123456, room_info=_room_info(status=4))
    with _patch_client(web):
        await get_live_status("expireduser")
        # Manually expire the cache entry
        _cache["expireduser"] = (_cache["expireduser"][0], time.monotonic() - 31)
        await get_live_status("expireduser")

    assert web.fetch_room_id_from_api.call_count == 2


@pytest.mark.asyncio
async def test_cache_key_is_lowercase():
    """Cache lookup should be case-insensitive (TikTok usernames are case-insensitive)."""
    web = _make_web_mock(room_id=123456, room_info=_room_info(status=4))
    with _patch_client(web):
        await get_live_status("TestUser")
        await get_live_status("testuser")

    assert web.fetch_room_id_from_api.call_count == 1


# ── SSE cache tests ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_sse_cache_hit_skips_api():
    """Multiple SSE subscribers for same username share the SSE cache."""
    web = _make_web_mock(room_id=123456, room_info=_room_info(status=2, user_count=100))
    with _patch_client(web):
        first = await get_live_status_sse("sseuser")
        second = await get_live_status_sse("sseuser")

    assert first == second
    assert web.fetch_room_id_from_api.call_count == 1


@pytest.mark.asyncio
async def test_sse_cache_expired_refetches():
    """Expired SSE cache entry triggers a new TikTok call."""
    web = _make_web_mock(room_id=123456, room_info=_room_info(status=4))
    with _patch_client(web):
        await get_live_status_sse("sseexpired")
        _sse_cache["sseexpired"] = (
            _sse_cache["sseexpired"][0],
            time.monotonic() - (POLL_INTERVAL + 1),
        )
        await get_live_status_sse("sseexpired")

    assert web.fetch_room_id_from_api.call_count == 2


@pytest.mark.asyncio
async def test_sse_cache_populates_rest_cache():
    """An SSE poll should also refresh the REST cache as a side effect."""
    web = _make_web_mock(room_id=123456, room_info=_room_info(status=2))
    with _patch_client(web):
        await get_live_status_sse("crosspopulate")

    assert "crosspopulate" in _cache
    assert _cache["crosspopulate"][0].is_live is True


# ── Integration test (requires internet + real TikTok accounts) ──────────────


@pytest.mark.integration
@pytest.mark.asyncio
async def test_real_user_status():
    """
    Hits TikTok's real API. Uses a well-known account that definitely exists.
    Does NOT assert is_live (that changes), only that we get a valid response.

    Run with: pytest -m integration
    """
    result = await get_live_status("tiktok")

    assert isinstance(result, LiveStatus)
    assert result.username == "tiktok"
    assert isinstance(result.is_live, bool)
    if result.is_live:
        assert result.room_id is not None
        assert isinstance(result.viewer_count, int)
