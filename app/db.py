"""Database configuration and session utilities."""
from __future__ import annotations

from collections.abc import AsyncIterator
import pathlib

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
