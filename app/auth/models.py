"""Auth data models — roles, token claims, request/response shapes."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.db.models import OperatorRole


# ---------------------------------------------------------------------------
# JWT claims
# ---------------------------------------------------------------------------
class TokenClaims(BaseModel):
    """Verified JWT payload. All fields are mandatory in every token.

    Frozen (immutable) so instances are hashable: the WebSocket hub stores
    ``(connection, claims)`` tuples in a set. Claims are never mutated after
    decode, so freezing is also correct.
    """
    model_config = ConfigDict(frozen=True)

    sub: str             # operator_id (UUID as str)
    username: str
    role: OperatorRole
    lane_ids: tuple[str, ...]   # tuple (not list) so the frozen model stays hashable
    jti: str             # JWT ID — for future revocation list
    iat: int             # issued at (Unix seconds)
    exp: int             # expiry (Unix seconds)


# ---------------------------------------------------------------------------
# HTTP request/response shapes
# ---------------------------------------------------------------------------
class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1)


class TokenResponse(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int          # seconds
    operator_id: str
    username: str
    role: OperatorRole
    lane_ids: list[str] = []


class OperatorCreateRequest(BaseModel):
    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=12, description="Minimum 12 characters.")
    role: OperatorRole = OperatorRole.OPERATOR
    lane_ids: list[str] = Field(default_factory=list)


class OperatorResponse(BaseModel):
    operator_id: UUID
    username: str
    role: OperatorRole
    lane_ids: list[str]
    is_active: bool
    created_at: datetime
    last_login_at: datetime | None


__all__ = [
    "TokenClaims", "LoginRequest", "TokenResponse",
    "OperatorCreateRequest", "OperatorResponse",
]
