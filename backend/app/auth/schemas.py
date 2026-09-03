"""Auth schemas — Pydantic models for request/response validation."""

from pydantic import BaseModel


class UserCreate(BaseModel):
    """Schema for user registration."""

    email: str
    password: str


class UserResponse(BaseModel):
    """Schema for user response."""

    id: str
    email: str

    model_config = {"from_attributes": True}


class TokenResponse(BaseModel):
    """Schema for auth token response."""

    access_token: str
    refresh_token: str
    token_type: str = "bearer"
