"""Qdrant vector database client stub."""

from qdrant_client import QdrantClient

from app.config import settings

qdrant_client: QdrantClient | None = None


def init_qdrant() -> QdrantClient:
    """Initialize the Qdrant client."""
    global qdrant_client
    qdrant_client = QdrantClient(url=settings.qdrant_url)
    return qdrant_client


def close_qdrant() -> None:
    """Close the Qdrant client."""
    global qdrant_client
    if qdrant_client:
        qdrant_client.close()
        qdrant_client = None


def get_qdrant() -> QdrantClient:
    """Dependency that provides the Qdrant client."""
    if qdrant_client is None:
        raise RuntimeError("Qdrant client not initialized")
    return qdrant_client
