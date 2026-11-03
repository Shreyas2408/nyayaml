"""Auth service with durable, atomic refresh-token rotation."""

from datetime import datetime, timezone
from typing import Annotated
from uuid import UUID

import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from app.auth.models import RefreshSession, User
from app.core.database import get_db
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    hash_token,
    verify_password,
)

bearer = HTTPBearer(auto_error=False)
# Fixed dummy hash prevents skipping expensive verification for unknown accounts.
DUMMY_HASH = "$2b$12$LQv3c1yqBWVHxkd0LHAkCOYz6TtxBi5F4eVqI4IxCZKZnZ0vB.a6u"


async def _commit(session: AsyncSession) -> None:
    """Never leave a failed rotation's revocation pending in the caller session."""
    try:
        await session.commit()
    except Exception:
        await session.rollback()
        raise


def _refresh_identity(token: str) -> UUID:
    try:
        claims = decode_token(token)
        if claims["type"] != "refresh":
            raise ValueError
        return UUID(claims["sub"])
    except (jwt.InvalidTokenError, ValueError, TypeError, KeyError):
        raise ValueError("Invalid refresh token") from None


async def register_user(session: AsyncSession, email: str, password: str) -> User:
    """Register a user; reject duplicates, including concurrent registrations."""
    email = email.strip().lower()
    result = await session.execute(select(User).where(User.email == email))
    if result.scalar_one_or_none() is not None:
        raise ValueError("Email already registered")
    hashed = await run_in_threadpool(hash_password, password)
    user = User(email=email, hashed_password=hashed, is_active=True)
    session.add(user)
    try:
        await _commit(session)
    except IntegrityError as exc:
        if getattr(exc.orig, "sqlstate", None) == "23505" or getattr(exc.orig, "pgcode", None) == "23505":
            raise ValueError("Email already registered") from None
        raise
    return user


async def login_user(
    session: AsyncSession, email: str, password: str,
    ip_address: str | None = None, user_agent: str | None = None,
) -> tuple[str, str, datetime]:
    """Return access/refresh tokens only after the refresh session commits."""
    result = await session.execute(select(User).where(User.email == email.strip().lower()))
    user = result.scalar_one_or_none()
    valid = await run_in_threadpool(
        verify_password, password, user.hashed_password if user else DUMMY_HASH
    )
    if user is None or not valid or not user.is_active:
        raise ValueError("Invalid credentials")

    access_token = create_access_token(user.id)
    refresh_token, refresh_expires = create_refresh_token(user.id)
    session.add(RefreshSession(
        user_id=user.id, token_hash=hash_token(refresh_token),
        expires_at=refresh_expires, ip_address=ip_address, user_agent=user_agent,
    ))
    await _commit(session)
    return access_token, refresh_token, refresh_expires


async def refresh_access_token(
    session: AsyncSession, refresh_token: str,
) -> tuple[str, str, datetime]:
    """Consume once using UPDATE ... RETURNING; commit replacement atomically."""
    user_id = _refresh_identity(refresh_token)
    now = datetime.now(timezone.utc)
    # PostgreSQL rechecks this predicate after a concurrent row-lock wait.
    # Both revocation and replacement insertion share this transaction.
    result = await session.execute(
        update(RefreshSession)
        .where(
            RefreshSession.user_id == user_id,
            RefreshSession.token_hash == hash_token(refresh_token),
            RefreshSession.revoked.is_(False),
            RefreshSession.expires_at > now,
            RefreshSession.user_id.in_(select(User.id).where(User.is_active.is_(True))),
        )
        .values(revoked=True, revoked_at=now)
        .returning(RefreshSession.id, RefreshSession.ip_address, RefreshSession.user_agent)
        .execution_options(synchronize_session=False)
    )
    row = result.first()
    if row is None:
        raise ValueError("Refresh token not found, already used, or expired")
    try:
        access_token = create_access_token(user_id)
        new_refresh_token, expires = create_refresh_token(user_id)
        session.add(RefreshSession(
            user_id=user_id, token_hash=hash_token(new_refresh_token),
            expires_at=expires, ip_address=row.ip_address, user_agent=row.user_agent,
        ))
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    return access_token, new_refresh_token, expires


async def logout_user(session: AsyncSession, refresh_token: str) -> None:
    """Revoke refresh token idempotently; invalid tokens silently succeed.

    Access tokens remain valid until expiry (max 30 min with current settings).
    Immediate access revocation via a Redis blacklist is deferred to post-v1.
    """
    try:
        user_id = _refresh_identity(refresh_token)
    except ValueError:
        return
    await session.execute(
        update(RefreshSession)
        .where(
            RefreshSession.user_id == user_id,
            RefreshSession.token_hash == hash_token(refresh_token),
            RefreshSession.revoked.is_(False),
        )
        .values(revoked=True, revoked_at=datetime.now(timezone.utc))
        .execution_options(synchronize_session=False)
    )
    await _commit(session)


async def current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> User:
    """Resolve only active users from validated access tokens."""
    try:
        if credentials is None or credentials.scheme.lower() != "bearer":
            raise ValueError
        claims = decode_token(credentials.credentials)
        if claims["type"] != "access":
            raise ValueError
        user = await db.get(User, UUID(claims["sub"]))
        if user is None or not user.is_active:
            raise ValueError
        return user
    except (jwt.InvalidTokenError, ValueError, TypeError, KeyError):
        raise HTTPException(
            401, "Invalid or expired credentials", headers={"WWW-Authenticate": "Bearer"}
        ) from None
