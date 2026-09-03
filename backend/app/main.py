# """NyayaML v3.1 — FastAPI Application Entry Point."""

# from collections.abc import AsyncGenerator
# from contextlib import asynccontextmanager

# import structlog
# from fastapi import FastAPI
# from fastapi.responses import JSONResponse
# from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

# from app.config import settings
# from app.core.exceptions import register_exception_handlers
# from app.core.middleware import RequestIDMiddleware
# from app.core.redis import close_redis, init_redis

# logger = structlog.get_logger()


# def configure_logging() -> None:
#     """Configure structlog for JSON-formatted structured logging."""
#     structlog.configure(
#         processors=[
#             structlog.contextvars.merge_contextvars,
#             structlog.stdlib.add_log_level,
#             structlog.processors.TimeStamper(fmt="iso"),
#             structlog.processors.StackInfoRenderer(),
#             structlog.processors.format_exc_info,
#             structlog.processors.JSONRenderer(),
#         ],
#         wrapper_class=structlog.stdlib.BoundLogger,
#         context_class=dict,
#         logger_factory=structlog.PrintLoggerFactory(),
#         cache_logger_on_first_use=True,
#     )


# @asynccontextmanager
# async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
#     """Application lifespan: startup and shutdown hooks."""
#     configure_logging()
#     logger.info(
#         "application_starting",
#         environment=settings.environment,
#         log_level=settings.log_level,
#     )

#     # Initialize Redis
#     try:
#         await init_redis()
#         logger.info("redis_connected")
#     except Exception as exc:
#         logger.warning("redis_connection_failed", error=str(exc))

#     yield

#     # Shutdown
#     await close_redis()
#     logger.info("application_stopped")


# app = FastAPI(
#     title="NyayaML",
#     description="AI-powered Indian legal research platform",
#     version="3.1.0",
#     docs_url="/docs",
#     redoc_url="/redoc",
#     lifespan=lifespan,
# )

# # ── Middleware ──────────────────────────────────────────
# app.add_middleware(RequestIDMiddleware)

# # ── Exception Handlers ─────────────────────────────────
# register_exception_handlers(app)


# # ── Health Endpoints ───────────────────────────────────
# @app.get("/api/v1/health", tags=["health"])
# async def health_check():
#     """Liveness probe — always returns ok if the process is running."""
#     return {"status": "ok"}


# @app.get("/api/v1/health/ready", tags=["health"])
# async def readiness_check():
#     """Readiness probe — checks connectivity to all backing services."""
#     import httpx

#     from app.core.database import engine
#     from app.core.redis import redis_client

#     services = {}

#     # Check PostgreSQL
#     try:
#         async with engine.connect() as conn:
#             await conn.execute(
#                 __import__("sqlalchemy").text("SELECT 1")
#             )
#         services["postgres"] = {"status": "up"}
#     except Exception as exc:
#         services["postgres"] = {"status": "down", "error": str(exc)}

#     # Check Redis
#     try:
#         if redis_client:
#             await redis_client.ping()
#             services["redis"] = {"status": "up"}
#         else:
#             services["redis"] = {"status": "down", "error": "not initialized"}
#     except Exception as exc:
#         services["redis"] = {"status": "down", "error": str(exc)}

#     # Check Qdrant
#     try:
#         async with httpx.AsyncClient(timeout=5.0) as client:
#             resp = await client.get(f"{settings.qdrant_url}/healthz")
#             if resp.status_code == 200:
#                 services["qdrant"] = {"status": "up"}
#             else:
#                 services["qdrant"] = {"status": "down", "error": f"HTTP {resp.status_code}"}
#     except Exception as exc:
#         services["qdrant"] = {"status": "down", "error": str(exc)}

#     # Check Ollama
#     try:
#         async with httpx.AsyncClient(timeout=5.0) as client:
#             resp = await client.get(f"{settings.ollama_base_url}/api/version")
#             if resp.status_code == 200:
#                 services["ollama"] = {"status": "up"}
#             else:
#                 services["ollama"] = {"status": "down", "error": f"HTTP {resp.status_code}"}
#     except Exception as exc:
#         services["ollama"] = {"status": "down", "error": str(exc)}

#     all_up = all(s["status"] == "up" for s in services.values())
#     status_code = 200 if all_up else 503

#     return JSONResponse(
#         status_code=status_code,
#         content={
#             "status": "ready" if all_up else "degraded",
#             "services": services,
#         },
#     )


# # ── Metrics ────────────────────────────────────────────
# @app.get("/metrics", tags=["monitoring"], include_in_schema=False)
# async def metrics():
#     """Prometheus metrics endpoint."""
#     from starlette.responses import Response

#     return Response(
#         content=generate_latest(),
#         media_type=CONTENT_TYPE_LATEST,
#     )

# # 
# # ── Module Routers (stubs) ─────────────────────────────
# from app.analytics.router import router as analytics_router
# from app.auth.router import router as auth_router
# from app.query.router import router as query_router
# from app.sections.router import router as sections_router

# app.include_router(auth_router, prefix="/api/v1/auth", tags=["auth"])
# app.include_router(query_router, prefix="/api/v1/query", tags=["query"])
# app.include_router(sections_router, prefix="/api/v1/sections", tags=["sections"])
# app.include_router(analytics_router, prefix="/api/v1/analytics", tags=["analytics"])
