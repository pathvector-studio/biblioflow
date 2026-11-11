from pydantic import BaseModel


class UpdateStatus(BaseModel):
    enabled: bool
    current: str
    latest: str | None = None
    update_available: bool = False
    security: bool = False
    url: str | None = None
    checked_at: str | None = None
