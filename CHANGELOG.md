# Changelog

All notable changes to NotiTFK are documented here.  
Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) · Versioning: [SemVer](https://semver.org/).

---

## [0.4.4] — 2026-05-25

### Added

- **Weekly integration test** — `.github/workflows/integration.yml` runs `pytest -m integration` every Monday at 08:00 UTC (same day as Dependabot) and on manual trigger. Detects TikTok API breakage before it reaches users. Canary deploys were considered but ruled out: no staging infrastructure exists and deployment risk is low relative to the overhead.
- **Release workflow** — `.github/workflows/release.yml` creates a GitHub Release automatically when a `v*` tag is pushed. Trigger: bump `pyproject.toml` version → commit → `git tag vX.Y.Z && git push --tags`.
- **`Commitlint` CI job** — `wagoid/commitlint-github-action@v6` now runs on every PR/push so the GitHub required-check actually executes. Previously `.commitlintrc.yml` was configured but no CI job ran it, forcing every merge to bypass the check.

### Changed

- **Version source of truth** — `api/main.py` reads the version from `pyproject.toml` via `importlib.metadata` (when installed) or `tomllib` fallback (git-clone / dev). No more manual sync between `pyproject.toml` and `FastAPI(version=...)`.
- **Dockerfile** — now runs `pip install --no-deps .` after copying source so `importlib.metadata.version("notiftk")` resolves inside the container. Dependency layer is still cached from `requirements.txt`. Added `org.opencontainers.image.source` label.
- **`actions/setup-python`** — downgraded from non-existent `@v6` to `@v5` across all CI jobs.
- **Deploy `needs`** — deploy job now also waits for `commitlint` in addition to `test`, `typecheck`, `audit`.
- **README** — updated health response example (version, timestamp, active_sse_connections, uptime_seconds, db_ok), added `GET /api/watch/{id}/deliveries` and `GET /api/admin/watches` sections, fixed `GET /api/watches` description (now ownership-filtered), fixed `DELETE` 404 semantics, updated env-var table (added CORS_ORIGINS), replaced duplicate "Option 3 Oracle" with Render documentation, checked off completed roadmap items (SSE cap, delivery history, webhook ownership), updated project structure.
- **pyproject.toml / `pyproject.toml` packaging** — declared as the authoritative version source. Considered full PyPI packaging (`pip install notiftk` → `notiftk serve`) but ruled out: this is a self-hosted server, not a library. Docker/git clone are the correct deployment paths.
- **Monorepo** — considered and rejected. The frontend is a single static HTML file served by FastAPI with no separate build step. Monorepo tooling (Turborepo, Nx) is for projects with multiple independently-buildable artifacts.
- **Version bump** — `pyproject.toml` + `app.version` → `0.4.4`.

---

## [0.4.3] — 2026-05-25

### Added

- **Webhook ownership** — webhooks are now bound to the API key that created them via `owner_key_hash` (SHA-256 of the raw key, stored in SQLite). `GET /api/watches` and `DELETE /api/watch/{id}` are filtered to the caller's webhooks. Legacy webhooks created before this change (NULL `owner_key_hash`) remain accessible to all authenticated callers — no data migration needed.
- **`GET /api/admin/watches`** — new admin endpoint that returns all webhooks regardless of ownership. Includes `owner_key_hash` for cross-referencing with the key list. Requires `X-Admin-Secret` header. Secrets are never included.
- **`AdminWatchResponse` Pydantic model** — `watch_id`, `username`, `callback_url`, `owner_key_hash` — added to `api/models.py`.

### Changed

- **`register_webhook`** now accepts `owner_key=None` — the raw API key is hashed (SHA-256) and stored. Pass `None` when auth is disabled (anonymous / legacy registration).
- **`unregister_webhook`** now accepts `owner_key=None` — returns `False` (→ 404) if the watch exists but belongs to a different key. Admin path passes `owner_key=None` to bypass the check.
- **`list_webhooks`** now accepts `owner_key=None` — filters to matching hash + NULL legacy entries. `list_all_webhooks()` (new) returns all entries for the admin path.
- **`watch`, `unwatch`, `watches` handlers** — now inject `_key: str | None = _auth` directly (no `dependencies=[_auth]`) so the key can be forwarded to ownership checks.
- **SQLite `webhooks` table** — non-destructive `ALTER TABLE … ADD COLUMN owner_key_hash TEXT` migration runs on first startup. Existing rows get `NULL` (legacy, accessible to all).
- **`GET /health` `active_webhooks`** — now counts via `list_all_webhooks()` so the total is always the true global count, not filtered by any key.
- **`DELETE /api/watch/{id}` 404 description** — updated to say "not found or not owned by this key" to accurately reflect the dual-purpose 404.
- **Version bump** — `app.version` → `0.4.3`.

---

## [0.4.2] — 2026-05-25

### Added

- **Webhook delivery history** — `GET /api/watch/{watch_id}/deliveries` returns the last 20 delivery attempts for a webhook, most-recent first. Each record includes `timestamp`, `http_status` (HTTP status code or `null` on network error), `attempt_count` (1–3, counting retries), and `success`. History is in-memory: it resets on server restart and is cleared when the webhook is unregistered.
- **`DeliveryRecord` Pydantic model** — added to `api/models.py` for the new endpoint's response schema.

### Changed

- **`_dispatch` return type** — now returns `tuple[bool, int | None]` instead of `bool`, exposing the HTTP status code for history recording.
- **`_dispatch_with_retry` records outcome** — calls `_record_delivery` on every final outcome (success on first attempt, success after retry, or all attempts exhausted), so the history always reflects the real delivery effort.
- **`GET /health` gains `timestamp`** — Unix float of when the probe was evaluated. Field order is now: `status`, `version`, `timestamp`, `uptime_seconds`, then config fields, then counters, then `db_ok`. Makes it consistent with `DeliveryRecord.timestamp` and easy to detect stale cached health responses.
- **Module docstring env-var table** — now lists all variables including `KEY_INVITE_CODE`, `SSE_MAX_PER_KEY`, `SSE_MAX_PER_USERNAME`, `LOG_FORMAT`, `LOG_LEVEL`, `CORS_ORIGINS`.
- **Version bump** — `app.version` → `0.4.2`.

---

## [0.4.1] — 2026-05-25

### Added

- **SSE connection cap** — `SSE_MAX_PER_KEY` (default 20) and `SSE_MAX_PER_USERNAME` (default 50) env vars. Opening more simultaneous SSE connections than the cap returns HTTP 429. Enforced per API key (or per IP when auth is disabled). Counters are tracked in `_sse_connections` and `_sse_per_username`; decremented atomically in the generator's `try/finally` so a client disconnect always frees the slot.
- **`active_sse_connections`** added to `GET /health` — total open SSE connections across all keys, at zero cost (sum of the in-memory counter dict).
- **Structured JSON logging** — set `LOG_FORMAT=json` to switch to newline-delimited JSON (`ts`, `level`, `logger`, `msg`, optional `exc` and any `extra=` fields). Implemented as a zero-dependency custom `logging.Formatter`. Default remains human-readable text. Works whether uvicorn has pre-installed handlers or not (reconfigures existing handlers rather than duplicating them).

### Changed

- `_sse_generator` and `_sse_generator_multi` now accept an `identity` parameter (API key or client IP) and wrap their bodies in `try/finally` for connection accounting.
- `live_stream` and `live_stream_multi` route handlers no longer use `dependencies=[_auth]` in the decorator; the key is now injected directly as `_key: str | None = _auth` so the handler can use it for connection tracking.
- `_configure_logging` now explicitly reconfigures existing log handlers (e.g. uvicorn's) instead of relying on `logging.basicConfig`'s idempotent no-op.

---

## [0.4.0] — 2026-05-25

### Added

- **`GET /api/keys/verify`** — dedicated key-validation endpoint. Returns `{"valid": true}` on success, 401 otherwise. Does not touch TikTok. Replaces the `__probe` hack in the frontend.
- **Key expiration** — `POST /api/keys` now accepts an optional `expires_in` query parameter (seconds, max 31 536 000 = 1 year). The response includes `expires_at` (Unix timestamp or `null`). Expired keys are rejected by `is_db_key_valid` without admin intervention.
- **Invite code** — new `KEY_INVITE_CODE` env var. When set, `POST /api/keys` requires `?invite=<code>`. Allows public deployments to restrict who can generate keys without disabling self-service entirely.
- **Label validation** — `POST /api/keys` now enforces a 100-character cap on the `label` parameter. Previously, arbitrarily long labels were stored verbatim in SQLite.
- **`expires_at` in admin key listing** — `GET /api/admin/keys` now includes `expires_at` per key.

### Changed

- **Non-blocking SQLite auth** — `is_db_key_valid`, `list_keys`, `revoke_key`, and the `_db_ok` health check are now called via `asyncio.to_thread`. SQLite reads no longer block the asyncio event loop during authentication.
- **Thundering herd fix in SSE cache** — `get_live_status_sse` now uses a per-username `asyncio.Lock` (double-checked locking). When N clients for the same username all find the cache stale simultaneously, only one fires a TikTok call; the others wait on the lock and return the cached result.
- **Webhook delivery retry** — `_dispatch_with_retry` wraps `_dispatch` with up to 3 attempts and exponential backoff (0 s → 1 s → 2 s). A receiver that is briefly down no longer causes a permanent event loss. The `MAX_DISPATCH_FAILURES` counter only increments when all retry attempts fail.
- **Typed exception hierarchy in `keys.py`** — `ValueError` splits into `KeyValidationError` (→ HTTP 422) and `KeyRateLimitError` (→ HTTP 429) so callers can map errors to the correct HTTP status without inspecting the message string.
- **Frontend probe URL** — the `startMonitoring` pre-flight now calls `/api/keys/verify` instead of `/api/status/__probe`.
- **Version bump** — `app.version` → `0.4.0`.

### Fixed

- `generate_key` was not thread-safe when called via `asyncio.to_thread`: the `_gen_log` dict could be corrupted under concurrent access. A `threading.Lock` (`_gen_log_lock`) now serialises all mutations.
- The SQLite `api_keys` table is automatically migrated to add the `expires_at` column (`ALTER TABLE … ADD COLUMN`) on first startup after upgrade. Existing keys are unaffected (column defaults to `NULL` = never expires).

---

## [0.3.0] — 2026-05-13

### Added

- **Self-service API keys** — `POST /api/keys` for key generation without server restart. Keys are stored as SHA-256 hashes. Admin endpoints (`GET /api/admin/keys`, `DELETE /api/admin/keys/{id}`) behind `X-Admin-Secret`.
- **Frontend key banner** — generate, copy, and persist an API key from the web UI.
- **Stale key detection** — the frontend now validates the stored key before opening an EventSource. A 401 in `onerror` with `readyState === CLOSED` clears the key and shows a clear error.

---

## [0.2.0] — 2026-04-28

### Added

- **Webhooks** — `POST /api/watch`, `DELETE /api/watch/{id}`, `GET /api/watches`. SQLite persistence, per-username and global caps, optional HMAC-SHA256 signing (`X-NotiTFK-Signature`). Auto-removed after 5 consecutive delivery failures or user not found.
- **Multi-username SSE** — `GET /api/stream?users=u1,u2,u3` — one connection for up to 10 usernames. Each event carries `username`.
- **Change-only SSE events** — event emitted only on `is_live` flip + `: heartbeat` comment every 30 s.
- **SSRF guard** — webhook callback URLs validated against RFC-1918, loopback, link-local, and IPv6 private ranges.

---

## [0.1.0] — 2026-04-10

### Added

- **`GET /api/status/{username}`** — one-shot live status, cached 30 s.
- **`GET /api/stream/{username}`** — SSE stream, change-only + heartbeat.
- **`GET /health`** — liveness probe with version, auth state, uptime.
- API key auth via `X-API-Key` header (REST) and `?key=` query param (SSE).
- Sliding-window rate limiting per key or IP.
- Web UI at `/`.
