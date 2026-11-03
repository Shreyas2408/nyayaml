"""Purpose-bound JWTs, bcrypt passwords, and refresh-token digests."""

import hashlib
import hmac
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID, uuid4

import bcrypt
import jwt

from app.config import settings

ISSUER = "nyayaml"
AUDIENCE = "nyayaml-api"


def hash_password(plain_password: str) -> str:
    """Hash with bcrypt cost 12; never silently truncate UTF-8 passwords."""
    encoded = plain_password.encode("utf-8")
    if not encoded or len(encoded) > 72:
        raise ValueError("Password must contain 1 to 72 UTF-8 bytes")
    return bcrypt.hashpw(encoded, bcrypt.gensalt(rounds=12)).decode("ascii")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Reject oversized passwords and corrupt stored hashes safely."""
    encoded = plain_password.encode("utf-8")
    if not encoded or len(encoded) > 72:
        return False
    try:
        return bcrypt.checkpw(encoded, hashed_password.encode("ascii"))
    except (ValueError, UnicodeError):
        return False


def create_token(user_id: UUID, purpose: str, lifetime: timedelta) -> str:
    """Shared implementation, retaining the existing issuer/audience contract."""
    if purpose not in {"access", "refresh"}:
        raise ValueError("Unknown token purpose")
    now = datetime.now(timezone.utc)
    return jwt.encode(
        {
            "sub": str(user_id), "type": purpose, "jti": str(uuid4()),
            "iat": now, "exp": now + lifetime, "iss": ISSUER, "aud": AUDIENCE,
        },
        settings.secret_key,
        algorithm="HS256",
    )


def create_access_token(user_id: UUID) -> str:
    """Create an access JWT with the configured lifetime (30 minutes)."""
    return create_token(
        user_id, "access", timedelta(minutes=settings.access_token_expire_minutes)
    )


def create_refresh_token(user_id: UUID) -> tuple[str, datetime]:
    """Create a unique refresh JWT and return its exact UTC expiry."""
    token = create_token(
        user_id, "refresh", timedelta(days=settings.refresh_token_expire_days)
    )
    expiry = datetime.fromtimestamp(decode_token(token)["exp"], timezone.utc)
    return token, expiry


def decode_token(token: str) -> dict[str, Any]:
    """Validate signature, purpose, required claims, identity and timestamps."""
    if not isinstance(token, str) or not 1 <= len(token) <= 2048:
        raise jwt.InvalidTokenError("Invalid token")
    try:
        claims = jwt.decode(
            token, settings.secret_key, algorithms=["HS256"],
            issuer=ISSUER, audience=AUDIENCE,
            options={"require": ["sub", "type", "jti", "iat", "exp", "iss", "aud"]},
        )
        if claims["type"] not in {"access", "refresh"}:
            raise ValueError
        UUID(claims["sub"])
        UUID(claims["jti"])
        if (
            type(claims["iat"]) is not int
            or type(claims["exp"]) is not int
            or claims["exp"] <= claims["iat"]
        ):
            raise ValueError
        return claims
    except (jwt.InvalidTokenError, ValueError, TypeError, AttributeError):
        # Never attach untrusted claim values or credentials to errors.
        raise jwt.InvalidTokenError("Invalid or expired token") from None


def hash_token(token: str) -> str:
    """Digest the entire high-entropy JWT for indexed, single-use lookup."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def verify_token_hash(token: str, token_hash: str) -> bool:
    """Compare SHA-256 digests in constant time."""
    return hmac.compare_digest(hash_token(token), token_hash)
