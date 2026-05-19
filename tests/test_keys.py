"""
Tests for self-service key generation and admin endpoints.

POST /api/keys    — generate key (rate limit, label, response shape)
GET  /api/admin/keys    — list keys (auth required, response shape)
DELETE /api/admin/keys/{id} — revoke key (not found, success)
"""

import pytest
from unittest.mock import patch

from fastapi.testclient import TestClient

from api.main import app
from api.auth import _request_log

client = TestClient(app)

_ADMIN_SECRET = "test-admin-secret-32chars-padded!!"


@pytest.fixture(autouse=True)
def clear_rate_log():
    _request_log.clear()
    yield
    _request_log.clear()


@pytest.fixture
def admin():
    with (
        patch("api.main.ADMIN_SECRET", _ADMIN_SECRET),
        patch("api.keys.ADMIN_SECRET", _ADMIN_SECRET),
    ):
        yield _ADMIN_SECRET


# ── POST /api/keys ────────────────────────────────────────────────────────────


class TestCreateKey:
    def test_returns_201_with_key_fields(self):
        r = client.post("/api/keys")
        assert r.status_code == 201
        body = r.json()
        assert body["key"].startswith("sk_live_")
        assert len(body["id"]) == 16  # secrets.token_hex(8)
        assert "warning" in body

    def test_key_has_correct_format(self):
        r = client.post("/api/keys")
        key = r.json()["key"]
        # sk_live_ + 48 hex chars
        assert key.startswith("sk_live_")
        assert len(key) == len("sk_live_") + 48

    def test_label_stored(self):
        r = client.post("/api/keys?label=my-bot")
        assert r.status_code == 201

    def test_rate_limit_429_after_max_per_ip(self):
        with patch("api.keys.MAX_KEYS_PER_IP", 2):
            r1 = client.post("/api/keys")
            r2 = client.post("/api/keys")
            assert r1.status_code == 201
            assert r2.status_code == 201

            r3 = client.post("/api/keys")
            assert r3.status_code == 429
            assert "Limite atteinte" in r3.json()["detail"]

    def test_global_cap_returns_429(self):
        with patch("api.keys.MAX_KEYS", 1):
            client.post("/api/keys")  # fills the cap
            r = client.post("/api/keys")
        assert r.status_code == 429

    def test_generated_key_is_valid_for_auth(self):
        """A generated key must actually work for authenticated endpoints."""
        r = client.post("/api/keys")
        key = r.json()["key"]

        from unittest.mock import AsyncMock
        from api.models import LiveStatus

        with (
            patch("api.auth.AUTH_ENABLED", True),
            patch("api.auth.VALID_KEYS", frozenset()),
            patch(
                "api.main.get_live_status",
                AsyncMock(return_value=LiveStatus(username="ninja", is_live=False)),
            ),
        ):
            r2 = client.get("/api/status/ninja", headers={"X-API-Key": key})
        assert r2.status_code == 200


# ── GET /api/admin/keys ───────────────────────────────────────────────────────


class TestAdminListKeys:
    def test_no_secret_configured_returns_404(self):
        with patch("api.main.ADMIN_SECRET", ""):
            r = client.get("/api/admin/keys", headers={"X-Admin-Secret": "whatever"})
        assert r.status_code == 404

    def test_wrong_secret_returns_404(self, admin):
        r = client.get("/api/admin/keys", headers={"X-Admin-Secret": "wrong-secret"})
        assert r.status_code == 404

    def test_no_header_returns_404(self, admin):
        r = client.get("/api/admin/keys")
        assert r.status_code == 404

    def test_correct_secret_returns_200(self, admin):
        client.post("/api/keys?label=test-key")
        r = client.get("/api/admin/keys", headers={"X-Admin-Secret": admin})
        assert r.status_code == 200
        keys = r.json()
        assert len(keys) == 1
        assert keys[0]["label"] == "test-key"
        assert keys[0]["is_active"] is True
        assert "id" in keys[0]
        assert "created_at" in keys[0]

    def test_empty_list_when_no_keys(self, admin):
        r = client.get("/api/admin/keys", headers={"X-Admin-Secret": admin})
        assert r.status_code == 200
        assert r.json() == []


# ── DELETE /api/admin/keys/{key_id} ──────────────────────────────────────────


class TestAdminRevokeKey:
    def test_revoke_existing_key_returns_204(self, admin):
        key_id = client.post("/api/keys").json()["id"]
        r = client.delete(
            f"/api/admin/keys/{key_id}",
            headers={"X-Admin-Secret": admin},
        )
        assert r.status_code == 204

    def test_revoke_non_existent_key_returns_404(self, admin):
        r = client.delete(
            "/api/admin/keys/doesnotexist",
            headers={"X-Admin-Secret": admin},
        )
        assert r.status_code == 404

    def test_revoked_key_no_longer_valid(self, admin):
        """After revocation, the key must be rejected for auth."""
        create_r = client.post("/api/keys")
        key_id = create_r.json()["id"]
        key = create_r.json()["key"]

        client.delete(f"/api/admin/keys/{key_id}", headers={"X-Admin-Secret": admin})

        from unittest.mock import AsyncMock
        from api.models import LiveStatus

        with (
            patch("api.auth.AUTH_ENABLED", True),
            patch("api.auth.VALID_KEYS", frozenset()),
            patch(
                "api.main.get_live_status",
                AsyncMock(return_value=LiveStatus(username="ninja", is_live=False)),
            ),
        ):
            r = client.get("/api/status/ninja", headers={"X-API-Key": key})
        assert r.status_code == 401

    def test_wrong_secret_returns_404(self, admin):
        key_id = client.post("/api/keys").json()["id"]
        r = client.delete(
            f"/api/admin/keys/{key_id}",
            headers={"X-Admin-Secret": "bad-secret"},
        )
        assert r.status_code == 404
