"""Custom exception classes and FastAPI exception handlers."""

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse


class NyayaMLException(Exception):
    """Base exception for NyayaML."""

    def __init__(self, detail: str, status_code: int = 500):
        self.detail = detail
        self.status_code = status_code
        super().__init__(detail)


class NotFound(NyayaMLException):
    """Resource not found."""

    def __init__(self, detail: str = "Resource not found"):
        super().__init__(detail=detail, status_code=404)


class ServiceUnavailable(NyayaMLException):
    """External service is unavailable."""

    def __init__(self, detail: str = "Service temporarily unavailable"):
        super().__init__(detail=detail, status_code=503)


class Unauthorized(NyayaMLException):
    """Authentication failed."""

    def __init__(self, detail: str = "Invalid credentials"):
        super().__init__(detail=detail, status_code=401)


class RateLimited(NyayaMLException):
    """Rate limit exceeded."""

    def __init__(self, detail: str = "Rate limit exceeded"):
        super().__init__(detail=detail, status_code=429)


def register_exception_handlers(app: FastAPI) -> None:
    """Register custom exception handlers on the FastAPI app."""

    @app.exception_handler(NyayaMLException)
    async def nyayaml_exception_handler(
        request: Request, exc: NyayaMLException
    ) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": type(exc).__name__,
                "detail": exc.detail,
            },
        )
