"""The one pydantic-settings class. Every key is listed in the repo-root .env.example."""

import base64
import ipaddress
import re
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Literal, Self
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

API_DIR = Path(__file__).resolve().parents[2]
REPO_DIR = API_DIR.parent

LOCAL_DATABASE_URL = "postgresql+psycopg://postgres:postgres@127.0.0.1:54322/postgres"

# The per-run cap a Student may set (USD); the platform default must be inside it.
PER_RUN_CAP_MIN_USD = Decimal("0.05")
PER_RUN_CAP_MAX_USD = Decimal("2.00")

# An Apify actor name, ``owner/name`` (shared with the Apify adapter).
ACTOR_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}/[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")


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
    # Documents a Student may keep (FR-26); one more upload is a 409.
    document_max_count: int = Field(default=20, ge=1, le=100)

    # Provider Key encryption (AD-6, core.crypto): "1:<base64 of 32 bytes>[,2:<...>]".
    # Encryption uses the highest version; decryption the version in the blob. Unset: storing
    # or decrypting a credential fails with 503 credentials-unavailable (startup is unaffected).
    credentials_master_keys: SecretStr | None = None
    # Apify (ProviderRunner adapter, AD-12). Sent as x-apify-integration-platform.
    apify_integration_platform: str = Field(
        default="careerlaunch", pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$"
    )
    apify_api_base: str = "https://api.apify.com"

    # Collection (Story 2.3, AD-12). Allowlisted actors, comma-separated, in order of use: the
    # first one runs; reorder to switch to the fallback. ``owner/name`` each.
    apify_actor_allowlist: str = (
        "curious_coder/linkedin-jobs-scraper,harvestapi/linkedin-job-search"
    )
    # Hours between a Student's scheduled collection runs.
    collect_interval_hours: int = Field(default=24, ge=24, le=24 * 7)
    # Scheduled runs happen at this hour in the Student's time zone.
    collect_local_hour: int = Field(default=6, ge=0, le=23)
    # Every run sets these caps on Apify (maxItems, maxTotalChargeUsd).
    collect_max_items: int = Field(default=100, ge=1, le=1000)
    collect_max_charge_usd: float = Field(default=0.25, gt=0, le=50)
    # Platform kill switch: false refuses every new provider run.
    provider_runs_enabled: bool = True
    # Spend caps (Story 2.4). A Student without their own caps gets COLLECT_MAX_CHARGE_USD per
    # run and this monthly cap (under Apify's USD 5 of free monthly credit).
    default_monthly_cap_usd: float = Field(default=4.00, ge=0.25, le=100)
    # The estimate of one run is max_items x this price (the primary actor's price per item).
    apify_price_per_item_usd: float = Field(default=0.001, gt=0, le=1)
    # Optional platform-wide monthly cap over every Student's committed spend; unset = off.
    platform_monthly_cap_usd: float | None = Field(default=None, gt=0)
    # Expiry, eviction and the quota check (Story 2.5). A Listing not seen for this many days
    # AND this many succeeded provider runs since is marked expired; an expired Listing is
    # deleted after the grace period. Active Listings above the pool cap are evicted.
    listing_unseen_days: int = Field(default=14, ge=7)
    listing_unseen_runs: int = Field(default=3, ge=1)
    listing_purge_grace_days: int = Field(default=30, ge=1)
    listing_pool_cap: int = Field(default=30000, ge=1)
    # The free database quota and the percentage of it that raises ``listings.quota_alert``.
    db_quota_bytes: int = Field(default=500 * 1024 * 1024, ge=1)
    db_quota_alert_percent: int = Field(default=80, ge=1, le=100)
    # Keyword search (Story 2.6): the least trigram word similarity (title + company) that still
    # counts as a match when full-text search finds nothing, so a typo still matches.
    search_trigram_threshold: float = Field(default=0.35, ge=0.1, le=0.9)
    # Stored Match Scores (Story 3.2): at most this many pool Listings per Student are scored
    # and stored (newest first); their own manual Listings are added beyond the cap (a Student
    # with no Preferences filter and no skill has no candidates at all).
    match_candidate_cap: int = Field(default=500, ge=1, le=5000)
    # The ranked feed (Story 3.3): before a Student has stored scores, at most this many of their
    # candidates are scored on request (never stored) so the first feed is ranked at once.
    feed_on_request_cap: int = Field(default=60, ge=1, le=500)
    # Manual Listings (Story 4.4): the most jobs from other sites one Student may hold; one
    # more is a 422. Re-adding a link already added is never blocked.
    manual_listings_max: int = Field(default=200, ge=1)
    # Retention (Story 4.4, AD-17): the daily ``core.purge_history`` job deletes finished
    # Procrastinate jobs (and their events) and leaked outbox rows older than
    # JOB_HISTORY_RETENTION_DAYS, and ``ops_events`` older than OPS_EVENTS_RETENTION_DAYS.
    job_history_retention_days: int = Field(default=7, ge=1)
    ops_events_retention_days: int = Field(default=90, ge=1)
    # The owner's Apify token, used ONLY by scripts/seed_pool.py.
    seed_apify_token: SecretStr | None = None

    @field_validator(
        "migration_database_url",
        "drain_secret",
        "drain_secret_previous",
        "supabase_jwt_secret",
        "supabase_service_role_key",
        "credentials_master_keys",
        "seed_apify_token",
        "platform_monthly_cap_usd",
        mode="before",
    )
    @classmethod
    def _blank_is_none(cls, v: object) -> object:
        return None if isinstance(v, str) and not v.strip() else v

    @field_validator("log_level", mode="before")
    @classmethod
    def _upper(cls, v: object) -> object:
        return v.upper() if isinstance(v, str) else v

    @field_validator("supabase_url", "apify_api_base", mode="after")
    @classmethod
    def _strip_slash(cls, v: str) -> str:
        return v.strip().rstrip("/")

    @model_validator(mode="after")
    def _master_keys_format(self) -> Self:
        # A model-level check: a field-level error would echo the raw value (the key) in the
        # ValidationError's input_value; here the input is the model, whose repr masks it.
        if self.credentials_master_keys is not None:
            parse_master_keys(self.credentials_master_keys.get_secret_value())
        return self

    @model_validator(mode="after")
    def _apify_base_is_https(self) -> Self:
        # The token travels as a bearer header: never in cleartext outside local / CI.
        parts = urlsplit(self.apify_api_base)
        allowed = ("https",) if self.environment not in ("local", "ci") else ("https", "http")
        if parts.scheme not in allowed or not parts.hostname:
            raise ValueError(
                "APIFY_API_BASE must be an https:// URL (http:// only when ENVIRONMENT is "
                "local or ci)"
            )
        return self

    @model_validator(mode="after")
    def _spend_defaults_are_consistent(self) -> Self:
        """A default run must fit its own charge cap, the charge cap must be a per-run cap a
        Student could set, and the default monthly cap must allow at least one such run.
        Otherwise every scheduled run would be refused or fail."""
        charge = Decimal(str(self.collect_max_charge_usd))
        estimate = Decimal(str(self.apify_price_per_item_usd)) * self.collect_max_items
        if estimate > charge:
            raise ValueError(
                f"COLLECT_MAX_ITEMS x APIFY_PRICE_PER_ITEM_USD ({estimate}) must not exceed "
                f"COLLECT_MAX_CHARGE_USD ({charge}): every default run would be refused"
            )
        if not PER_RUN_CAP_MIN_USD <= charge <= PER_RUN_CAP_MAX_USD:
            raise ValueError(
                f"COLLECT_MAX_CHARGE_USD ({charge}) must be between {PER_RUN_CAP_MIN_USD} and "
                f"{PER_RUN_CAP_MAX_USD}, the per-run caps a Student can set"
            )
        if charge > Decimal(str(self.default_monthly_cap_usd)):
            raise ValueError(
                f"COLLECT_MAX_CHARGE_USD ({charge}) must not exceed DEFAULT_MONTHLY_CAP_USD "
                f"({self.default_monthly_cap_usd}): the default monthly cap must allow one run"
            )
        return self

    @field_validator("apify_actor_allowlist", mode="after")
    @classmethod
    def _actor_names(cls, v: str) -> str:
        for actor in (a.strip() for a in v.split(",")):
            if actor and not ACTOR_RE.fullmatch(actor):
                raise ValueError("APIFY_ACTOR_ALLOWLIST entries must look like 'owner/name'")
        return v

    @property
    def apify_actors(self) -> tuple[str, ...]:
        """The allowlisted actors, in order (empty: no run can start)."""
        return tuple(a.strip() for a in self.apify_actor_allowlist.split(",") if a.strip())

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
    def master_keys(self) -> dict[int, bytes]:
        """``{version: 32-byte key}``; empty when CREDENTIALS_MASTER_KEYS is unset."""
        if self.credentials_master_keys is None:
            return {}
        return parse_master_keys(self.credentials_master_keys.get_secret_value())

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


MAX_KEY_VERSION = 32767  # provider_credentials.key_version is a smallint


def parse_master_keys(raw: str) -> dict[int, bytes]:
    """Parse ``"1:<base64-32-bytes>[,2:<...>]"`` into ``{version: key}``.

    Raises ``ValueError`` naming only the problem (never the key material)."""
    keys: dict[int, bytes] = {}
    for part in (p.strip() for p in raw.split(",")):
        if not part:
            continue
        version_text, sep, encoded = part.partition(":")
        version_text = version_text.strip()
        if not sep or not (version_text.isascii() and version_text.isdecimal()):
            raise ValueError("CREDENTIALS_MASTER_KEYS entries must look like '<version>:<base64>'")
        version = int(version_text)
        if not 1 <= version <= MAX_KEY_VERSION:
            raise ValueError(f"CREDENTIALS_MASTER_KEYS versions must be 1..{MAX_KEY_VERSION}")
        if version in keys:
            raise ValueError(f"CREDENTIALS_MASTER_KEYS has version {version} twice")
        try:
            key = base64.b64decode(encoded.strip(), validate=True)
        except ValueError:  # binascii.Error is a ValueError
            raise ValueError(
                f"CREDENTIALS_MASTER_KEYS version {version} is not valid base64"
            ) from None
        if len(key) != 32:
            raise ValueError(f"CREDENTIALS_MASTER_KEYS version {version} must decode to 32 bytes")
        keys[version] = key
    if not keys:
        raise ValueError("CREDENTIALS_MASTER_KEYS has no keys")
    return keys


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
