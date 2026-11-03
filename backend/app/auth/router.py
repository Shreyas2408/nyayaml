"""Auth endpoints with quotas checked before request parsing or DB access."""

from ipaddress import ip_address
from typing import Annotated

from asyncpg.exceptions import PostgresError
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import service
from app.auth.schemas import (
    Credentials,
    RefreshRequest,
    TokenResponse,
    UserCreate,
    UserResponse,
)
from app.config import settings
from app.core.database import get_db
from app.core.rate_limit import auth_limit


class AuthRoute(APIRoute):
    """Enforce quotas even on malformed requests; never echo credential inputs."""

    def get_route_handler(self):
        handler = super().get_route_handler()

        async def protected(request: Request) -> Response:
            try:
                await auth_limit(request)
                response = await handler(request)
            except RequestValidationError as exc:
                response = JSONResponse(status_code=422, content={
                    "detail": [
                        {"loc": list(error["loc"]), "msg": error["msg"], "type": error["type"]}
                        for error in exc.errors()
                    ]
                })
            except (SQLAlchemyError, PostgresError, OSError):
                # SQLAlchemy exceptions may include bound password/token hashes.
                response = JSONResponse(status_code=503, content={
                    "detail": "Authentication storage unavailable"
                })
            except HTTPException as exc:
                response = JSONResponse(
                    status_code=exc.status_code, content={"detail": exc.detail},
                    headers=exc.headers,
                )
            response.headers["Cache-Control"] = "no-store"
            response.headers["Pragma"] = "no-cache"
            return response

        return protected


router = APIRouter(prefix="/api/v1/auth", tags=["auth"], route_class=AuthRoute)
DB = Annotated[AsyncSession, Depends(get_db)]


@router.post("/register", response_model=UserResponse, status_code=201)
async def register(request: UserCreate, db: DB):
    try:
        return await service.register_user(db, str(request.email), request.password)
    except ValueError:
        raise HTTPException(400, "Email already registered") from None


@router.post("/login", response_model=TokenResponse)
async def login(request: Credentials, req: Request, db: DB):
    address = req.client.host if req.client else None
    try:
        address = str(ip_address(address)) if address else None
    except ValueError:
        address = None
    try:
        access, refresh, _ = await service.login_user(
            db, str(request.email), request.password,
            ip_address=address, user_agent=req.headers.get("user-agent", "")[:1024] or None,
        )
    except ValueError:
        raise HTTPException(401, "Invalid credentials") from None
    return TokenResponse(
        access_token=access, refresh_token=refresh,
        expires_in=settings.access_token_expire_minutes * 60,
    )


@router.post("/refresh", response_model=TokenResponse)
async def refresh(request: RefreshRequest, db: DB):
    try:
        access, refresh_token, _ = await service.refresh_access_token(db, request.refresh_token)
    except ValueError:
        raise HTTPException(401, "Invalid refresh token") from None
    return TokenResponse(
        access_token=access, refresh_token=refresh_token,
        expires_in=settings.access_token_expire_minutes * 60,
    )


@router.post("/logout", status_code=204)
async def logout(request: RefreshRequest, db: DB):
    await service.logout_user(db, request.refresh_token)
    return Response(status_code=204)
