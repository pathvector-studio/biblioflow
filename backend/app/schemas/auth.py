from typing import Literal

from pydantic import BaseModel, EmailStr, Field, field_validator

from app.core.security import validate_password
from app.schemas.user import UserOut


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=1024)


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str
    display_name: str | None = Field(default=None, min_length=1, max_length=100)

    @field_validator("password")
    @classmethod
    def _pw(cls, v: str) -> str:
        return validate_password(v)


class MeResponse(BaseModel):
    user: UserOut
    auth_method: Literal["session", "pat"]
    scopes: list[str]
