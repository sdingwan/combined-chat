"""Session management utilities."""
from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import Request, Response
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import KickUser, Session, TwitchUser


@dataclass
class SessionContext:
    """Aggregate of the current session and linked platform accounts."""

    session: Session
    twitch_user: Optional[TwitchUser]
    kick_user: Optional[KickUser]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _cookie_secure() -> bool:
    return settings.frontend_base_url.startswith("https")


def _set_cookie(response: Response, session_id: str) -> None:
    response.set_cookie(
        key=settings.session_cookie_name,
        value=session_id,
        httponly=True,
        secure=_cookie_secure(),
        samesite="lax",
        max_age=settings.session_ttl_seconds,
        path="/",
    )


async def create_session(
    db: AsyncSession,
    response: Response,
    *,
    twitch_user: Optional[TwitchUser] = None,
    kick_user: Optional[KickUser] = None,
) -> Session:
    """Create a new persistent session and set the cookie."""

    session_id = secrets.token_urlsafe(32)
    expires_at = _now() + timedelta(seconds=settings.session_ttl_seconds)

    record = Session(
        id=session_id,
        twitch_user_id=twitch_user.id if twitch_user else None,
        kick_user_id=kick_user.id if kick_user else None,
        expires_at=expires_at,
    )
    db.add(record)
    await db.commit()
    await db.refresh(record)

    _set_cookie(response, session_id)
    return record


async def destroy_session(db: AsyncSession, response: Response, request: Request) -> None:
    """Delete the active session cookie and database row."""

    session_id = request.cookies.get(settings.session_cookie_name)
    if not session_id:
        response.delete_cookie(settings.session_cookie_name, path="/")
        return

    await db.execute(delete(Session).where(Session.id == session_id))
    await db.commit()
    response.delete_cookie(settings.session_cookie_name, path="/")


async def _load_session(
    db: AsyncSession, session_id: str
) -> Optional[SessionContext]:
    record = await db.get(Session, session_id)
    if not record:
        return None

    expires_at = record.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at <= _now():
        await db.execute(delete(Session).where(Session.id == session_id))
        await db.commit()
        return None

    twitch_user = (
        await db.get(TwitchUser, record.twitch_user_id)
        if record.twitch_user_id
        else None
    )
    kick_user = (
        await db.get(KickUser, record.kick_user_id)
        if record.kick_user_id
        else None
    )
    return SessionContext(session=record, twitch_user=twitch_user, kick_user=kick_user)


async def get_current_user(
    db: AsyncSession, request: Request
) -> Optional[SessionContext]:
    """Return the session context associated with the cookie, if any."""

    session_id = request.cookies.get(settings.session_cookie_name)
    if not session_id:
        return None
    return await _load_session(db, session_id)


async def ensure_session(
    db: AsyncSession,
    request: Request,
    response: Response,
    *,
    twitch_user: Optional[TwitchUser] = None,
    kick_user: Optional[KickUser] = None,
) -> SessionContext:
    """Ensure the browser has an active session linked to the supplied identities."""

    current = await get_current_user(db, request)
    expires_at = _now() + timedelta(seconds=settings.session_ttl_seconds)

    if current:
        session = current.session
        updated = False

        if twitch_user and session.twitch_user_id != twitch_user.id:
            session.twitch_user_id = twitch_user.id
            updated = True
            current.twitch_user = twitch_user
        elif twitch_user and current.twitch_user is None:
            current.twitch_user = twitch_user

        if kick_user and session.kick_user_id != kick_user.id:
            session.kick_user_id = kick_user.id
            updated = True
            current.kick_user = kick_user
        elif kick_user and current.kick_user is None:
            current.kick_user = kick_user

        if session.expires_at != expires_at:
            session.expires_at = expires_at
            updated = True

        if updated:
            await db.commit()
            await db.refresh(session)

        _set_cookie(response, session.id)
        return SessionContext(
            session=session,
            twitch_user=current.twitch_user,
            kick_user=current.kick_user,
        )

    session = await create_session(
        db,
        response,
        twitch_user=twitch_user,
        kick_user=kick_user,
    )
    return SessionContext(session=session, twitch_user=twitch_user, kick_user=kick_user)
