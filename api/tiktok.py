"""
TikTok live status detection.

All I/O goes through TikTokLive, which handles TikTok's anti-bot
measures (msToken generation, signed requests, cookie rotation).

Detection flow (2 HTTP calls per check):
  1. fetch_room_id_from_api  → resolve the user's persistent room ID
  2. fetch_room_info         → get status, title, viewer count in one shot

TikTok room status codes (confirmed from TikTokLive source):
  4  → offline / stream ended
  ≠4 → live (typically 2)

Caching strategy (two separate caches, two use cases):

  REST cache (_cache, 30s TTL):
    For one-shot bot commands and API callers. 30s is plenty — bots don't
    need sub-5s freshness for a slash command response.

  SSE cache (_sse_cache, POLL_INTERVAL TTL):
    For streaming clients polling every 5s. All SSE subscribers watching
    the same username share this cache: N clients → 1 TikTok call per 5s.
    SSE polls also update _cache so REST callers benefit for free.

  Both caches are in-process RAM — they reset on server restart.

Public API:
  get_live_status(username)     → LiveStatus  (REST, 30s cache)
  get_live_status_sse(username) → LiveStatus  (SSE, 5s shared cache)
  CACHE_TTL                     → int (seconds)
  POLL_INTERVAL                 → int (seconds)
"""

import asyncio
import time
from TikTokLive import TikTokLiveClient
from TikTokLive.client.errors import AgeRestrictedError, UserNotFoundError

from .models import LiveStatus

CACHE_TTL = 30  # REST one-shot cache — 30s is plenty for bot commands
POLL_INTERVAL = 5  # SSE polling interval — 5s gives avg 2.5s detection latency
TIKTOK_TIMEOUT = 10  # seconds before giving up on a TikTok API call

# username (lowercased) → (LiveStatus, monotonic timestamp)
_cache: dict[str, tuple[LiveStatus, float]] = {}
_sse_cache: dict[str, tuple[LiveStatus, float]] = {}


class TikTokUserNotFound(Exception):
    """User doesn't exist on TikTok or has never gone live."""


class TikTokAPIError(Exception):
    """Unexpected / transient error from TikTok's API."""


async def get_live_status(username: str) -> LiveStatus:
    """
    Return the current live status for *username* (REST cache, 30s TTL).

    Best for: one-shot Discord bot commands, single API checks.

    Raises:
        TikTokUserNotFound: User doesn't exist or has never gone live.
        TikTokAPIError: Unexpected failure communicating with TikTok.
    """
    cache_key = username.lower()
    cached = _cache.get(cache_key)
    if cached is not None:
        status, ts = cached
        if time.monotonic() - ts < CACHE_TTL:
            return status

    result = await _fetch_live_status(cache_key)
    _cache[cache_key] = (result, time.monotonic())
    return result


async def get_live_status_sse(username: str) -> LiveStatus:
    """
    Return the current live status for *username* (SSE cache, POLL_INTERVAL TTL).

    All SSE subscribers for the same username share this cache, so TikTok
    is polled at most once per POLL_INTERVAL regardless of subscriber count.
    Also updates _cache so REST callers benefit from fresh SSE data.

    Best for: SSE streaming generators.

    Raises:
        TikTokUserNotFound: User doesn't exist or has never gone live.
        TikTokAPIError: Unexpected failure communicating with TikTok.
    """
    cache_key = username.lower()
    cached = _sse_cache.get(cache_key)
    if cached is not None:
        status, ts = cached
        if time.monotonic() - ts < POLL_INTERVAL:
            return status

    result = await _fetch_live_status(cache_key)
    now = time.monotonic()
    _sse_cache[cache_key] = (result, now)
    _cache[cache_key] = (result, now)
    return result


async def _fetch_live_status(username: str) -> LiveStatus:
    """Make the actual network calls to TikTok. Always bypasses cache."""
    client = TikTokLiveClient(unique_id=username)
    web = client.web

    try:
        room_id = await asyncio.wait_for(
            web.fetch_room_id_from_api(unique_id=username),
            timeout=TIKTOK_TIMEOUT,
        )

        if room_id is None:
            # User exists but has never gone live — no room assigned yet.
            return LiveStatus(username=username, is_live=False)

        try:
            info: dict = await asyncio.wait_for(
                web.fetch_room_info(room_id=room_id),
                timeout=TIKTOK_TIMEOUT,
            )
        except AgeRestrictedError:
            # Stream is 18+ — TikTok refuses room details without a logged-in
            # session. We know the user IS live (roomId exists), so return that
            # without viewer count or title rather than surfacing a false error.
            return LiveStatus(username=username, is_live=True, room_id=str(room_id))

        is_live = info.get("status") != 4

        return LiveStatus(
            username=username,
            is_live=is_live,
            room_id=str(room_id) if is_live else None,
            viewer_count=info.get("user_count") if is_live else None,
            title=info.get("title") or None if is_live else None,
        )

    except asyncio.TimeoutError:
        raise TikTokAPIError(f"TikTok API timed out after {TIKTOK_TIMEOUT}s") from None

    except UserNotFoundError as e:
        raise TikTokUserNotFound(str(e)) from e

    except (TikTokUserNotFound, TikTokAPIError):
        raise

    except Exception as e:
        raise TikTokAPIError(f"TikTok API error: {e}") from e

    finally:
        await web.httpx_client.aclose()
