from pydantic import BaseModel, HttpUrl, field_validator


class LiveStatus(BaseModel):
    username: str
    is_live: bool
    room_id: str | None = None
    viewer_count: int | None = None
    title: str | None = None


class ErrorResponse(BaseModel):
    username: str
    error: str


class WatchRequest(BaseModel):
    username: str
    callback_url: HttpUrl
    secret: str | None = None

    @field_validator("secret")
    @classmethod
    def secret_min_length(cls, v: str | None) -> str | None:
        if v is not None and len(v) < 8:
            raise ValueError("secret must be at least 8 characters")
        return v


class WatchResponse(BaseModel):
    watch_id: str
    username: str
    callback_url: str


class AdminWatchResponse(BaseModel):
    watch_id: str
    username: str
    callback_url: str
    owner_key_hash: str | None = None


class KeyResponse(BaseModel):
    id: str
    key: str
    expires_at: float | None = None
    warning: str


class AdminKeyResponse(BaseModel):
    id: str
    label: str | None
    created_at: float
    is_active: bool
    expires_at: float | None = None


class DeliveryRecord(BaseModel):
    timestamp: float
    http_status: int | None = None
    attempt_count: int
    success: bool


class HealthResponse(BaseModel):
    status: str
    version: str
    timestamp: float
    uptime_seconds: float
    auth_enabled: bool
    cache_ttl_seconds: int
    poll_interval_seconds: int
    active_webhooks: int
    active_sse_connections: int = 0
    db_ok: bool
