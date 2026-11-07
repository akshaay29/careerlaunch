"""The one pydantic-settings class. Every key is listed in the repo-root .env.example."""

from functools import lru_cache

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

LOCAL_DATABASE_URL = "postgresql+psycopg://postgres:postgres@127.0.0.1:54322/postgres"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    environment: str = Field(default="local", description="local | ci | prod")
    log_level: str = "INFO"

    # Postgres via the Supavisor *session* pooler (port 5432) in prod; Supabase CLI locally.
    database_url: SecretStr = SecretStr(LOCAL_DATABASE_URL)
    # Used only by Alembic in the release workflow (GitHub secret); falls back to database_url.
    migration_database_url: SecretStr | None = None

    # CORS: the Vercel origin plus localhost (AD-14). Comma-separated in env.
    web_origin: str = "http://localhost:3000"
    cors_extra_origins: str = "http://localhost:3000,http://127.0.0.1:3000"

    @field_validator("migration_database_url", mode="before")
    @classmethod
    def _blank_is_none(cls, v: object) -> object:
        return None if isinstance(v, str) and not v.strip() else v

    @field_validator("log_level")
    @classmethod
    def _upper(cls, v: str) -> str:
        return v.upper()

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
