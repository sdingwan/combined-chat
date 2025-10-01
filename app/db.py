"""Database configuration and session utilities."""
from __future__ import annotations

from collections.abc import AsyncIterator

import pathlib

from sqlalchemy.ext.asyncio import AsyncAttrs, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import settings


class Base(AsyncAttrs, DeclarativeBase):
    """Declarative base class for ORM models."""

    pass


def _prepare_sqlite_url(url: str) -> str:
    if not url.startswith("sqlite"):  # includes sqlite+aiosqlite
        return url
    path_part = url.split("///", 1)[-1]
    if not path_part:
        return url
    db_path = pathlib.Path(path_part)
    if not db_path.is_absolute():
        # Assume project root two levels up from this file
        base_dir = pathlib.Path(__file__).resolve().parents[1]
        db_path = base_dir / db_path
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if "sqlite+" in url:
        return f"sqlite+aiosqlite:///{db_path}"
    return f"sqlite:///{db_path}"


database_url = _prepare_sqlite_url(settings.database_url)

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
