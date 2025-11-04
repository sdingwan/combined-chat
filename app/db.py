"""Database configuration and session utilities."""
from __future__ import annotations

from collections.abc import AsyncIterator
import pathlib

from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import (
    AsyncAttrs,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.config import settings


class Base(AsyncAttrs, DeclarativeBase):
    """Declarative base class for ORM models."""

    pass


def _normalize_database_url(raw_url: str) -> str:
    """Normalise URLs for the async SQLAlchemy engine."""

    url = raw_url.strip()

    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://") :]
    if url.startswith("postgresql://") and "+" not in url.split("://", 1)[0]:
        url = url.replace("postgresql://", "postgresql+asyncpg://", 1)

    if url.startswith("sqlite"):
        path_part = url.split("///", 1)[-1]
        if path_part:
            db_path = pathlib.Path(path_part)
            if not db_path.is_absolute():
                # Assume project root two levels up from this file
                base_dir = pathlib.Path(__file__).resolve().parents[1]
                db_path = base_dir / db_path
            db_path.parent.mkdir(parents=True, exist_ok=True)
            if "sqlite+" in url:
                return f"sqlite+aiosqlite:///{db_path}"
            return f"sqlite:///{db_path}"
    return url


database_url = _normalize_database_url(settings.database_url)

engine = create_async_engine(database_url, future=True, echo=False)
AsyncSessionMaker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency that yields an async session."""

    async with AsyncSessionMaker() as session:
        yield session


async def init_db() -> None:
    """Initialise database schema if necessary."""

    import app.models  # noqa: F401 ensures model metadata is imported

    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
        await connection.run_sync(_ensure_session_youtube_column)


def _ensure_session_youtube_column(sync_connection) -> None:
    """Backfill youtube_user_id column/constraint for existing deployments."""

    inspector = inspect(sync_connection)
    try:
        column_names = {column["name"] for column in inspector.get_columns("sessions")}
    except Exception:
        return

    if "youtube_user_id" not in column_names:
        sync_connection.execute(
            text("ALTER TABLE sessions ADD COLUMN youtube_user_id VARCHAR(128)")
        )

    if sync_connection.dialect.name == "sqlite":
        return

    foreign_keys = inspector.get_foreign_keys("sessions")
    has_constraint = any(
        "youtube_user_id" in (fk.get("constrained_columns") or [])
        and fk.get("referred_table") == "youtube_users"
        for fk in foreign_keys
    )
    if not has_constraint:
        sync_connection.execute(
            text(
                "ALTER TABLE sessions "
                "ADD CONSTRAINT sessions_youtube_user_id_fkey "
                "FOREIGN KEY (youtube_user_id) "
                "REFERENCES youtube_users(id) ON DELETE SET NULL"
            )
        )
