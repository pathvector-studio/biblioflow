import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

Scope = Literal["read:papers", "write:papers", "use:ai"]


class TokenCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    scopes: list[Scope] = Field(min_length=1)
    expires_in_days: int | None = Field(default=90, ge=1, le=3650)

    @field_validator("scopes")
    @classmethod
    def _dedupe(cls, v: list[str]) -> list[str]:
        return sorted(set(v))


class TokenOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    token_prefix: str
    scopes: list[str]
    expires_at: datetime | None
    last_used_at: datetime | None
    revoked_at: datetime | None
    created_at: datetime


class TokenCreated(TokenOut):
    token: str
