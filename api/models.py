from pydantic import BaseModel, HttpUrl


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


class WatchResponse(BaseModel):
    watch_id: str
    username: str
    callback_url: str
