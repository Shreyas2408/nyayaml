"""Auth utilities — password hashing, JWT token creation."""


def hash_password(password: str) -> str:
    """Hash a plaintext password — stub."""
    raise NotImplementedError


def verify_password(plain: str, hashed: str) -> bool:
    """Verify a password against its hash — stub."""
    raise NotImplementedError


def create_access_token(data: dict) -> str:
    """Create a JWT access token — stub."""
    raise NotImplementedError
