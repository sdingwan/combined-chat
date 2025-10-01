"""Helpers for storing and validating OAuth state tokens."""
from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional

from app.config import settings
from app.models import OAuthPlatform


@dataclass
class StateRecord:
    """In-memory representation of a pending OAuth state."""

    platform: OAuthPlatform
    session_id: Optional[str]
    redirect_path: Optional[str]
    code_verifier: Optional[str]
    expires_at: datetime


_STATE_STORE: Dict[str, StateRecord] = {}


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def create_state(
    *,
    platform: OAuthPlatform,
    session_id: Optional[str],
    redirect_path: Optional[str] = None,
    code_verifier: Optional[str] = None,
) -> str:
    """Persist a new OAuth state entry and return the token."""

    state_token = secrets.token_urlsafe(32)
    expires_at = _now() + timedelta(seconds=settings.oauth_state_ttl_seconds)
    _STATE_STORE[state_token] = StateRecord(
        platform=platform,
        session_id=session_id,
        redirect_path=redirect_path,
        code_verifier=code_verifier,
        expires_at=expires_at,
    )
    return state_token


async def consume_state(
    *, platform: OAuthPlatform, state_token: str
) -> Optional[StateRecord]:
    """Validate and remove an OAuth state entry."""

    record = _STATE_STORE.pop(state_token, None)
    if not record:
        return None
    if record.platform is not platform:
        return None
    expires_at = record.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at <= _now():
        return None
    return record
