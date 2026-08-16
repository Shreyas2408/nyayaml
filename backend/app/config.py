"""Application configuration using pydantic-settings."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Central configuration loaded from environment variables / .env file."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # ── Database ────────────────────────────────────────
    database_url: str = "postgresql+asyncpg://nyayaml:changeme_in_production@postgres:5432/nyayaml"

    # ── Redis ───────────────────────────────────────────
    redis_url: str = "redis://redis:6379/0"

    # ── Qdrant ──────────────────────────────────────────
    qdrant_url: str = "http://qdrant:6333"

    # ── Ollama ──────────────────────────────────────────
    ollama_base_url: str = "http://ollama:11434"

    # ── Authentication ──────────────────────────────────
    secret_key: str = "change-this-to-a-random-64-char-string-in-production"
    access_token_expire_minutes: int = 30
    refresh_token_expire_days: int = 7

    # ── Rate Limiting ──────────────────────────────────
    rate_limit_per_hour: int = 100

    # ── Logging & Environment ──────────────────────────
    log_level: str = "INFO"
    environment: str = "development"


settings = Settings()
