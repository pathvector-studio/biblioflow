from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

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
    cors_allowed_origins: Annotated[list[str], NoDecode] = Field(default_factory=list)
    allow_registration: bool = False
    enable_docs: bool = False

    max_upload_mb: int = Field(50, ge=1, le=2048)
    library_root: Path = Path("/data/library")
    watch_dir: Path = Path("/data/watch")
    export_dir: Path = Path("/data/exports")  # auto-export files: {export_dir}/{workspace_slug}/{filename}
    filename_template: str = DEFAULT_TEMPLATE
    embedding_dim: int = EMBEDDING_DIMENSION

    # Phase 2: external metadata APIs & ingest
    crossref_mailto: str | None = None
    semantic_scholar_api_key: SecretStr | None = None
    external_http_timeout: float = Field(10.0, gt=0, le=120)
    watch_poll_seconds: int = Field(10, ge=1, le=3600)
    url_import_enabled: bool = False

    # Chat / vision (Phase 3d). Off by default (`none`): chat and explain-region answer 503 `llm_disabled`.
    # The model string handed to LiteLLM is built from the provider (see `app.services.llm`).
    llm_provider: Literal["none", "anthropic", "openai", "gemini", "ollama", "openai_compatible"] = "none"
    llm_model: str | None = None
    llm_api_key: SecretStr | None = None
    llm_api_base: str | None = None  # ollama / openai_compatible only; set by the admin, so not behind safe_client
    vision_model: str | None = None  # empty: LLM_MODEL is assumed to accept images
    llm_max_output_tokens: int = Field(1024, ge=1, le=32768)
    llm_max_context_chunks: int = Field(8, ge=1, le=50)
    llm_timeout: float = Field(60.0, gt=0, le=600)
    llm_first_token_timeout: float = Field(30.0, gt=0, le=600)
    llm_daily_requests_per_user: int = Field(200, ge=1)
    llm_daily_tokens_per_user: int = Field(200_000, ge=1)
    chat_retention_days: int = Field(90, ge=0)  # conversations idle (updated_at) longer than this are deleted; 0 = keep

    # Embeddings (semantic search). Off by default (`none`): turning on `local` is the consent to download the model. `local` = the biblioflow-embedder container (bge-m3 on ONNX Runtime).
    embedding_provider: Literal["local", "voyage", "openai", "none"] = "none"
    embedding_local_precision: Literal["int8", "fp32"] = "int8"
    embedder_url: str = "http://biblioflow-embedder:8001"
    embedding_base_url: str | None = None  # openai: any OpenAI-compatible server (Ollama, vLLM, TEI, ...)
    embedding_api_key: SecretStr | None = None
    embedding_model: str | None = None  # voyage / openai only; defaults: voyage-3.5, text-embedding-3-large
    embedding_query_timeout: float = Field(2.0, gt=0, le=30)
    search_min_similarity: float = Field(0.45, ge=-1.0, le=1.0)

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

    @model_validator(mode="after")
    def _check_llm(self) -> Settings:
        # Missing model / key / base URL is not an error (see llm_config_problem): an old .env may still carry the
        # former .env.example placeholder LLM_PROVIDER=openai without a key, and an upgrade must not stop the API.
        if self.llm_provider != "none" and self.llm_first_token_timeout > self.llm_timeout:
            raise ValueError("LLM_FIRST_TOKEN_TIMEOUT must not exceed LLM_TIMEOUT")
        return self

    @property
    def llm_config_problem(self) -> str | None:
        """Why chat stays off although a provider is chosen (names settings only, never values), or None."""
        if self.llm_provider == "none":
            return None
        if not (self.llm_model or "").strip():
            return f"LLM_MODEL is required when LLM_PROVIDER={self.llm_provider}"
        if self.llm_provider in ("ollama", "openai_compatible") and not (self.llm_api_base or "").strip():
            return f"LLM_API_BASE is required when LLM_PROVIDER={self.llm_provider}"
        key = self.llm_api_key.get_secret_value().strip() if self.llm_api_key else ""
        if self.llm_provider in ("anthropic", "openai", "gemini") and not key:
            return f"LLM_API_KEY is required when LLM_PROVIDER={self.llm_provider}"
        return None

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
