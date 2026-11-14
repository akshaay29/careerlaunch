"""The one pydantic-settings class. Every key is listed in the repo-root .env.example."""

import ipaddress
from functools import lru_cache
from pathlib import Path
from typing import Literal, Self
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

API_DIR = Path(__file__).resolve().parents[2]
REPO_DIR = API_DIR.parent

LOCAL_DATABASE_URL = "postgresql+psycopg://postgres:postgres@127.0.0.1:54322/postgres"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        # Later files win: api/.env overrides the repo-root .env. Real env vars beat both.
        env_file=(REPO_DIR / ".env", API_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    environment: Literal["local", "ci", "prod"] = "local"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"

    # Postgres via the Supavisor *session* pooler (port 5432) in prod; Supabase CLI locally.
    database_url: SecretStr = SecretStr(LOCAL_DATABASE_URL)
    # Used only by Alembic in the release workflow (GitHub secret); falls back to database_url.
    migration_database_url: SecretStr | None = None

    # CORS: the Vercel origin plus localhost (AD-14). Comma-separated in env.
    web_origin: str = "http://localhost:3000"
    cors_extra_origins: str = "http://localhost:3000,http://127.0.0.1:3000"

    # Background-job drain (AD-4). /internal/* requires `Authorization: Bearer <secret>`.
    # Both values are accepted so the secret can be rotated without downtime.
    drain_secret: SecretStr | None = None
    drain_secret_previous: SecretStr | None = None
    # Hard cap on one drain (asyncio.timeout); the spine allows at most 4 minutes.
    drain_timeout_seconds: int = Field(default=240, ge=1, le=240)
    # GET /internal/health returns 503 when the last drain is older than this.
    drain_stale_after_minutes: int = Field(default=15, ge=1)

    # Supabase Auth (AD-5). Tokens must have iss == <supabase_url>/auth/v1; asymmetric keys come
    # from <supabase_url>/auth/v1/.well-known/jwks.json. Local default: the Supabase CLI stack.
    supabase_url: str = "http://127.0.0.1:54321"
    # Legacy HS256 secret (the local CLI signs with it). Unset = HS256 tokens are rejected.
    supabase_jwt_secret: SecretStr | None = None

    # Supabase Storage (FileStore adapter, Files convention). The service-role key is used only
    # by the api, server side; required whenever the real Storage adapter is used.
    supabase_service_role_key: SecretStr | None = None
    # The PRIVATE bucket holding every Student file at <owner_id>/<kind>/<uuid>.
    storage_bucket: str = Field(default="private", pattern=r"^[a-z0-9][a-z0-9_-]{1,62}$")
    # Resume versions a Student may keep (FR-3); one more upload is a 409.
    resume_max_versions: int = Field(default=10, ge=1, le=100)

    @field_validator(
        "migration_database_url",
        "drain_secret",
        "drain_secret_previous",
        "supabase_jwt_secret",
        "supabase_service_role_key",
        mode="before",
    )
    @classmethod
    def _blank_is_none(cls, v: object) -> object:
        return None if isinstance(v, str) and not v.strip() else v

    @field_validator("log_level", mode="before")
    @classmethod
    def _upper(cls, v: object) -> object:
        return v.upper() if isinstance(v, str) else v

    @field_validator("supabase_url", mode="after")
    @classmethod
    def _strip_slash(cls, v: str) -> str:
        return v.strip().rstrip("/")

    @model_validator(mode="after")
    def _prod_needs_real_supabase(self) -> Self:
        # The default is the local CLI URL; in prod it would silently reject every real token.
        if self.environment == "prod" and _is_loopback(self.supabase_url):
            raise ValueError("SUPABASE_URL must be set to the project URL when ENVIRONMENT=prod")
        return self

    @property
    def supabase_issuer(self) -> str:
        return f"{self.supabase_url}/auth/v1"

    @property
    def supabase_jwks_url(self) -> str:
        return f"{self.supabase_issuer}/.well-known/jwks.json"

    @property
    def supabase_storage_url(self) -> str:
        return f"{self.supabase_url}/storage/v1"

    @property
    def cors_origins(self) -> list[str]:
        raw = [self.web_origin, *self.cors_extra_origins.split(",")]
        seen: list[str] = []
        for origin in (o.strip().rstrip("/") for o in raw):
            if origin and origin not in seen:
                seen.append(origin)
        return seen

    @property
    def sqlalchemy_url(self) -> str:
        """Runtime database URL normalised to the psycopg 3 driver."""
        return to_sqlalchemy_url(self.database_url.get_secret_value())

    @property
    def migration_sqlalchemy_url(self) -> str:
        secret = self.migration_database_url or self.database_url
        return to_sqlalchemy_url(secret.get_secret_value())


def _is_loopback(url: str) -> bool:
    host = (urlsplit(url).hostname or "").lower()
    if not host or host == "localhost" or host.endswith(".localhost"):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def to_sqlalchemy_url(url: str) -> str:
    for prefix in ("postgresql://", "postgres://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix) :]
    return url


def to_libpq_url(url: str) -> str:
    """The same URL in plain libpq form (for psycopg.connect)."""
    return url.replace("postgresql+psycopg://", "postgresql://", 1)


@lru_cache
def get_settings() -> Settings:
    return Settings()
