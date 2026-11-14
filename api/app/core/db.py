"""Async engine and sessions (SQLAlchemy 2.1 + psycopg 3).

Every DB connection runs ``SET search_path TO app`` through a SQLAlchemy ``connect``
event. A libpq startup ``options`` parameter is deliberately NOT used: the Supavisor
session pooler drops it (AD-1, spine data-access convention).
"""

from collections.abc import AsyncIterator
from typing import Any

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.core.config import get_settings

APP_SCHEMA = "app"



class Base(DeclarativeBase):
    """Declarative base for module models. Tables resolve through ``search_path=app``;
    Alembic migrations (not ``metadata.create_all``) own the DDL (AD-13)."""


_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def install_search_path_hook(engine: AsyncEngine, schema: str = APP_SCHEMA) -> None:
    """Pin ``search_path`` on every new DBAPI connection of ``engine``."""

    @event.listens_for(engine.sync_engine, "connect", insert=True)
    def _set_search_path(dbapi_connection: Any, _record: Any) -> None:
        existing = dbapi_connection.autocommit
        dbapi_connection.autocommit = True
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute(f"SET search_path TO {schema}")
        finally:
            cursor.close()
        dbapi_connection.autocommit = existing


def make_engine(url: str | None = None, **kwargs: Any) -> AsyncEngine:
    engine = create_async_engine(
        url or get_settings().sqlalchemy_url,
        pool_pre_ping=True,
        **kwargs,
    )
    install_search_path_hook(engine)
    return engine


def get_engine() -> AsyncEngine:
    global _engine, _sessionmaker
    if _engine is None:
        _engine = make_engine()
        _sessionmaker = async_sessionmaker(_engine, expire_on_commit=False)
    return _engine


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    get_engine()
    assert _sessionmaker is not None
    return _sessionmaker


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency: one session per request. Routers commit; services never do."""
    async with get_sessionmaker()() as session:
        yield session


async def dispose_engine() -> None:
    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _sessionmaker = None
