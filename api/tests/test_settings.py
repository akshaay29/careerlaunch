import pytest

from app.core.config import Settings


def test_database_url_normalised_to_psycopg(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MIGRATION_DATABASE_URL", raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@host:5432/db")
    s = Settings(_env_file=None)
    assert s.sqlalchemy_url == "postgresql+psycopg://u:p@host:5432/db"
    assert s.migration_sqlalchemy_url == s.sqlalchemy_url


def test_migration_url_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@a:5432/db")
    monkeypatch.setenv("MIGRATION_DATABASE_URL", "postgres://m:p@b:5432/db")
    s = Settings(_env_file=None)
    assert s.migration_sqlalchemy_url == "postgresql+psycopg://m:p@b:5432/db"


def test_secrets_are_not_in_repr(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:topsecret@a:5432/db")
    s = Settings(_env_file=None)
    assert "topsecret" not in repr(s)
    assert "topsecret" not in str(s.model_dump())


def test_cors_origins_include_vercel_and_localhost(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WEB_ORIGIN", "https://careerlaunch.vercel.app/")
    s = Settings(_env_file=None)
    assert s.cors_origins[0] == "https://careerlaunch.vercel.app"
    assert "http://localhost:3000" in s.cors_origins


def test_drain_settings_defaults_and_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    from pydantic import ValidationError

    for k in ("DRAIN_SECRET", "DRAIN_SECRET_PREVIOUS", "DRAIN_TIMEOUT_SECONDS", "DRAIN_STALE_AFTER_MINUTES"):
        monkeypatch.delenv(k, raising=False)
    s = Settings(_env_file=None)
    assert s.drain_secret is None and s.drain_secret_previous is None
    assert (s.drain_timeout_seconds, s.drain_stale_after_minutes) == (240, 15)
    monkeypatch.setenv("DRAIN_SECRET", "  ")
    monkeypatch.setenv("DRAIN_SECRET_PREVIOUS", "old-drain-value")
    s = Settings(_env_file=None)
    assert s.drain_secret is None
    assert "old-drain-value" not in repr(s)
    monkeypatch.setenv("DRAIN_TIMEOUT_SECONDS", "241")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)
