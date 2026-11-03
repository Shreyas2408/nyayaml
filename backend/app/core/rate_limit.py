"""Atomic Redis quotas, failing closed on outages."""

from typing import Annotated

import redis.asyncio as aioredis
from fastapi import Depends, HTTPException, Request
from redis.exceptions import RedisError

from app.auth.service import current_user
from app.config import settings
from app.core.redis import get_redis
from app.db.models import User

# SET NX EX creates the first counter with its TTL in one operation. Lua also
# makes increment/TTL handling indivisible at window rollover and across workers.
QUOTA_SCRIPT = """
local first = redis.call('SET', KEYS[1], 1, 'EX', ARGV[1], 'NX')
if first then return {1, tonumber(ARGV[1])} end
local ttl = redis.call('TTL', KEYS[1])
if ttl < 0 then return redis.error_reply('quota TTL missing') end
local count = redis.call('INCR', KEYS[1])
return {count, ttl}
"""


async def _consume(redis, key: str, limit: int, seconds: int) -> None:
    try:
        count, ttl = await redis.eval(QUOTA_SCRIPT, 1, key, seconds)
    except (RedisError, OSError):
        raise HTTPException(503, "Rate limiting unavailable") from None
    if count > limit:
        raise HTTPException(
            429, "Rate limit exceeded", headers={"Retry-After": str(max(1, ttl))}
        )


async def check_rate_limit(ip_address: str, endpoint: str) -> None:
    """Apply the configured hourly quota per peer IP and auth endpoint."""
    # Request-scoped lifetime works with the existing main.py lifespan, which
    # does not initialize core.redis. Connections are closed even on failure.
    try:
        async with aioredis.from_url(
            settings.redis_url, decode_responses=True,
            socket_connect_timeout=2, socket_timeout=2,
        ) as client:
            await _consume(
                client, f"rate_limit:{endpoint}:{ip_address}", settings.rate_limit_per_hour, 3600
            )
    except (RedisError, OSError):
        raise HTTPException(503, "Rate limiting unavailable") from None


async def consume(redis, key, limit, seconds):
    """Retain the existing non-auth quota interface."""
    await _consume(redis, "nyayaml:quota:" + key, limit, seconds)


async def query_limit(
    user: Annotated[User, Depends(current_user)],
    redis: Annotated[aioredis.Redis, Depends(get_redis)],
):
    await consume(redis, "query:" + str(user.id), settings.rate_limit_per_hour, 3600)
    return user


async def auth_limit(request: Request):
    # Never trust arbitrary X-Forwarded-For headers.
    address = request.client.host if request.client else "unknown"
    await check_rate_limit(address, request.url.path.rsplit("/", 1)[-1])
