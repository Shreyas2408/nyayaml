"""Compatibility helpers backed by the shared security implementation."""

import jwt

from app.core.security import (
    AUDIENCE,
    ISSUER,
    create_token,
    hash_password,
    verify_password,
)
from app.core.security import decode_token as _decode_token
from app.core.security import (
    hash_token as token_digest,
)

__all__ = [
    "AUDIENCE", "ISSUER", "create_token", "decode_token", "hash_password",
    "token_digest", "verify_password",
]


def decode_token(token: str, purpose: str) -> dict:
    """Retain purpose validation for callers of the previous helper API."""
    claims = _decode_token(token)
    if claims["type"] != purpose:
        raise jwt.InvalidTokenError("Wrong token purpose")
    return claims
