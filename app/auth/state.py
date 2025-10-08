"""Helpers for storing and validating OAuth state tokens."""
from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import OAuthPlatform, OAuthState


@dataclass
class StateRecord:
    """Transient representation of a pending OAuth state."""

    platform: OAuthPlatform
    session_id: Optional[str]
    redirect_path: Optional[str]
    code_verifier: Optional[str]
    expires_at: datetime


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def create_state(
    *,
    db: AsyncSession,
    platform: OAuthPlatform,
    session_id: Optional[str],
    redirect_path: Optional[str] = None,
    code_verifier: Optional[str] = None,
) -> str:
    """Persist a new OAuth state entry and return the token."""

    state_token = secrets.token_urlsafe(32)
    expires_at = _now() + timedelta(seconds=settings.oauth_state_ttl_seconds)

    await db.execute(delete(OAuthState).where(OAuthState.expires_at <= _now()))

    record = OAuthState(
        token=state_token,
        platform=platform,
        session_id=session_id,
        redirect_path=redirect_path,
        code_verifier=code_verifier,
        expires_at=expires_at,
    )
    db.add(record)
    await db.commit()
    return state_token


async def consume_state(
    *, db: AsyncSession, platform: OAuthPlatform, state_token: str
) -> Optional[StateRecord]:
    """Validate and remove an OAuth state entry."""

    result = await db.execute(select(OAuthState).where(OAuthState.token == state_token))
    stored = result.scalar_one_or_none()
    if not stored:
        return None

    await db.delete(stored)
    await db.commit()

    if stored.platform is not platform:
        return None

    expires_at = stored.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at <= _now():
        return None

    return StateRecord(
        platform=stored.platform,
        session_id=stored.session_id,
        redirect_path=stored.redirect_path,
        code_verifier=stored.code_verifier,
        expires_at=expires_at,
    )
