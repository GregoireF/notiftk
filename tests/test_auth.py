"""
Tests for authentication and rate limiting.

Tests run with AUTH_ENABLED=False (no API_KEYS set) unless explicitly patched.
"""

import pytest
from unittest.mock import patch, AsyncMock

from fastapi.testclient import TestClient

from api.main import app
from api.auth import _request_log
from api.models import LiveStatus

client = TestClient(app)


def _mock_status() -> LiveStatus:
    return LiveStatus(username="testuser", is_live=False)


@pytest.fixture(autouse=True)
def clear_rate_log():
    """Wipe rate-limit state before each test."""
    _request_log.clear()
    yield
    _request_log.clear()


# ── Health endpoint (always public) ──────────────────────────────────────────


def test_health_always_accessible():
    """Health endpoint must work without any API key."""
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


# ── Auth disabled (no API_KEYS set) ──────────────────────────────────────────


def test_no_auth_passes_without_key():
    """When AUTH_ENABLED is False, requests without a key should succeed."""
    with patch("api.main.get_live_status", AsyncMock(return_value=_mock_status())):
        r = client.get("/api/status/testuser")
    assert r.status_code == 200


# ── Auth enabled ──────────────────────────────────────────────────────────────


@pytest.fixture
def auth_enabled():
    """Activate auth with a test key for the duration of the test."""
    test_key = "sk_test_abc123"
    with patch("api.auth.AUTH_ENABLED", True), patch("api.auth.VALID_KEYS", frozenset({test_key})):
        yield test_key


def test_valid_key_in_header_passes(auth_enabled):
    with patch("api.main.get_live_status", AsyncMock(return_value=_mock_status())):
        r = client.get(
            "/api/status/testuser",
            headers={"X-API-Key": auth_enabled},
        )
    assert r.status_code == 200


def test_valid_key_in_query_param_passes(auth_enabled):
    with patch("api.main.get_live_status", AsyncMock(return_value=_mock_status())):
        r = client.get(f"/api/status/testuser?key={auth_enabled}")
    assert r.status_code == 200


def test_missing_key_returns_401(auth_enabled):
    r = client.get("/api/status/testuser")
    assert r.status_code == 401


def test_wrong_key_returns_401(auth_enabled):
    r = client.get("/api/status/testuser", headers={"X-API-Key": "sk_wrong"})
    assert r.status_code == 401


# ── Rate limiting ─────────────────────────────────────────────────────────────


def test_rate_limit_triggers_429():
    """After RATE_LIMIT_REQUESTS requests, the next one should get 429."""
    with (
        patch("api.auth.RATE_LIMIT_REQUESTS", 3),
        patch("api.main.get_live_status", AsyncMock(return_value=_mock_status())),
    ):
        for _ in range(3):
            r = client.get("/api/status/testuser")
            assert r.status_code == 200
        r = client.get("/api/status/testuser")
    assert r.status_code == 429
    assert "Retry-After" in r.headers


def test_rate_limit_resets_after_window():
    """Requests older than the window should not count toward the limit."""
    with (
        patch("api.auth.RATE_LIMIT_REQUESTS", 2),
        patch("api.auth.RATE_LIMIT_WINDOW", 60),
        patch("api.main.get_live_status", AsyncMock(return_value=_mock_status())),
    ):
        # Fill the window
        client.get("/api/status/testuser")
        client.get("/api/status/testuser")

        # Fake that those requests happened 61s ago
        ip = "testclient"
        _request_log[ip] = [t - 61 for t in _request_log.get(ip, [])]

        # Next request should succeed
        r = client.get("/api/status/testuser")
    assert r.status_code == 200
