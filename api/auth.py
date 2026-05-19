"""
Authentication and rate limiting for NotiTFK.

Auth:
  API key via X-API-Key header (REST) or ?key= query param (SSE).
  EventSource in browsers cannot set custom headers, so SSE uses query param.

  Valid keys come from the API_KEYS environment variable (comma-separated).
  When API_KEYS is unset or empty, auth is DISABLED — useful for local dev.
  Set REQUIRE_API_KEY=true to enforce auth even when API_KEYS is empty.

  Example .env:
      API_KEYS=sk_live_abc123,sk_live_xyz789
      REQUIRE_API_KEY=true

Rate limiting:
  Sliding-window counter keyed by API key (if auth enabled) or by client IP.
  Default: 120 requests per 60-second window.
  No external dependency — implemented with a plain dict.

Public API:
  api_key_dependency — FastAPI Depends() target for protected endpoints
  RATE_LIMIT_REQUESTS, RATE_LIMIT_WINDOW — tunable constants
"""

import os
import time
from collections import defaultdict

from fastapi import Depends, HTTPException, Request
from fastapi.security import APIKeyHeader, APIKeyQuery

# ── Configuration ─────────────────────────────────────────────────────────────


def _load_keys() -> frozenset[str]:
    raw = os.getenv("API_KEYS", "")
    return frozenset(k.strip() for k in raw.split(",") if k.strip())


VALID_KEYS: frozenset[str] = _load_keys()
AUTH_ENABLED: bool = bool(VALID_KEYS) or os.getenv("REQUIRE_API_KEY", "").lower() == "true"

RATE_LIMIT_REQUESTS = int(os.getenv("RATE_LIMIT_REQUESTS", "120"))
RATE_LIMIT_WINDOW = int(os.getenv("RATE_LIMIT_WINDOW", "60"))  # seconds

# ── Rate limiter ──────────────────────────────────────────────────────────────

# key (API key or IP) → list of request timestamps in the current window
_request_log: dict[str, list[float]] = defaultdict(list)


def _check_rate_limit(identity: str) -> None:
    """
    Sliding-window rate limiter. Raises HTTP 429 if the limit is exceeded.

    Args:
        identity: API key or IP address to bucket the request under.
    """
    now = time.monotonic()
    window_start = now - RATE_LIMIT_WINDOW
    log = _request_log[identity]

    # Evict timestamps outside the window
    recent = [t for t in log if t > window_start]

    if not recent:
        # All timestamps expired — free memory for this identity
        _request_log.pop(identity, None)
        recent = []
    else:
        _request_log[identity] = recent

    if len(recent) >= RATE_LIMIT_REQUESTS:
        raise HTTPException(
            status_code=429,
            detail=f"Rate limit exceeded: {RATE_LIMIT_REQUESTS} requests per {RATE_LIMIT_WINDOW}s.",
            headers={"Retry-After": str(RATE_LIMIT_WINDOW)},
        )

    _request_log.setdefault(identity, []).append(now)


# ── FastAPI security schemes ──────────────────────────────────────────────────

_header_scheme = APIKeyHeader(name="X-API-Key", auto_error=False)
_query_scheme = APIKeyQuery(name="key", auto_error=False)


async def api_key_dependency(
    request: Request,
    key_header: str | None = Depends(_header_scheme),
    key_query: str | None = Depends(_query_scheme),
) -> str | None:
    """
    FastAPI dependency that validates the API key and applies rate limiting.

    When AUTH_ENABLED is False (no API_KEYS configured), all requests pass
    through — rate limiting is still applied per IP.

    Returns the validated key (or None if auth disabled).
    Raises HTTP 401 on invalid key, HTTP 429 on rate limit exceeded.
    """
    key = key_header or key_query

    if AUTH_ENABLED:
        from .keys import is_db_key_valid  # local import avoids circular dependency at module load

        if not key or (key not in VALID_KEYS and not is_db_key_valid(key)):
            raise HTTPException(
                status_code=401,
                detail="Missing or invalid API key. Pass X-API-Key header or ?key= query param.",
                headers={"WWW-Authenticate": "ApiKey"},
            )
        _check_rate_limit(key)
    else:
        # Auth disabled — rate limit by IP so open deployments can't be trivially abused.
        # Behind a reverse proxy (Fly.io, nginx), client.host is the proxy IP — use
        # X-Forwarded-For so each real client gets its own bucket.
        xff = request.headers.get("x-forwarded-for")
        client_ip = (
            xff.split(",")[0].strip()
            if xff
            else (request.client.host if request.client else "unknown")
        )
        _check_rate_limit(client_ip)

    return key
