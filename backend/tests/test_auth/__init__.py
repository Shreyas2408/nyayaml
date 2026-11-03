"""Offline auth tests: deterministic configuration, no external connections."""

import os
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
# Load settings with test-only values before infrastructure modules import them.
with patch.dict(os.environ, {
    "SECRET_KEY": "offline-auth-test-key-not-for-deployment-" * 2,
    "DATABASE_URL": "postgresql+asyncpg://unused:unused@localhost/unused",
    "REDIS_URL": "redis://localhost:6379/15",
    "ACCESS_TOKEN_EXPIRE_MINUTES": "30",
    "REFRESH_TOKEN_EXPIRE_DAYS": "7",
    "RATE_LIMIT_PER_HOUR": "100",
}):
    from app.config import settings  # noqa: F401
