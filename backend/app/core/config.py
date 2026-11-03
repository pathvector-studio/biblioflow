from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Annotated

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from app.services.storage.paths import DEFAULT_TEMPLATE, validate_template

SESSION_COOKIE_NAME = "bf_session"
EMBEDDING_DIMENSION = 1024  # fixed by the DB schema (vector(1024))


class Settings(BaseSettings):
    model_config = SettingsConfigDict(case_sensitive=False, extra="ignore", env_ignore_empty=True)

    database_url: SecretStr
    test_database_url: SecretStr | None = None
    redis_url: SecretStr

    jwt_secret: SecretStr
    jwt_expire_minutes: int = Field(720, ge=1, le=60 * 24 * 30)
    cookie_secure: bool = True
    cors_allowed_origins: Annotated[list[str], NoDecode] = Field(default_factory=list)
    allow_registration: bool = False
    enable_docs: bool = False

    max_upload_mb: int = Field(50, ge=1, le=2048)
    library_root: Path = Path("/data/library")
    watch_dir: Path = Path("/data/watch")
    filename_template: str = DEFAULT_TEMPLATE
    embedding_dim: int = EMBEDDING_DIMENSION

    # Phase 2: external metadata APIs & ingest
    crossref_mailto: str | None = None
    semantic_scholar_api_key: SecretStr | None = None
    external_http_timeout: float = Field(10.0, gt=0, le=120)
    watch_poll_seconds: int = Field(10, ge=1, le=3600)
    url_import_enabled: bool = False

    # Phase 3 (unused in Phase 1)
    llm_provider: str | None = None
    llm_model: str | None = None
    llm_api_key: SecretStr | None = None
    llm_api_base: str | None = None
    vision_model: str | None = None
    embedding_model: str | None = None

    @field_validator("cors_allowed_origins", mode="before")
    @classmethod
    def _split_origins(cls, v: object) -> object:
        if isinstance(v, str):
            v = [p for p in (x.strip() for x in v.split(",")) if p]
        if isinstance(v, list):
            out = []
            for o in v:
                o = str(o).strip().rstrip("/").lower()
                if o == "*":
                    raise ValueError("wildcard CORS origin is not allowed with credentials")
                out.append(o)
            return out
        return v

    @field_validator("jwt_secret")
    @classmethod
    def _check_secret(cls, v: SecretStr) -> SecretStr:
        if len(v.get_secret_value()) < 32:
            raise ValueError("JWT_SECRET must be at least 32 characters")
        return v

    @field_validator("database_url")
    @classmethod
    def _check_db_url(cls, v: SecretStr) -> SecretStr:
        if not v.get_secret_value().startswith("postgresql+asyncpg://"):
            raise ValueError("DATABASE_URL must use the postgresql+asyncpg:// scheme")
        return v

    @field_validator("filename_template")
    @classmethod
    def _check_template(cls, v: str) -> str:
        return validate_template(v)

    @model_validator(mode="after")
    def _check_dim(self) -> Settings:
        if self.embedding_dim != EMBEDDING_DIMENSION:
            raise ValueError(f"EMBEDDING_DIM must be {EMBEDDING_DIMENSION} (schema is vector({EMBEDDING_DIMENSION}))")
        return self

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
