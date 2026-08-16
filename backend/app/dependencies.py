"""Common dependency injection providers."""

from app.core.database import get_db
from app.core.redis import get_redis
from app.core.qdrant import get_qdrant

__all__ = ["get_db", "get_redis", "get_qdrant"]
