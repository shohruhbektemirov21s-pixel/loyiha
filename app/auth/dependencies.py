"""FastAPI auth dependencies — extract and enforce roles on every endpoint.

Usage:
    @router.post("/something")
    async def endpoint(claims: TokenClaims = Depends(require_operator)):
        ...

    # Or compose with other deps:
    async def endpoint(
        claims: TokenClaims = Depends(require_supervisor),
        db: AsyncSession = Depends(get_db),
    ):
        ...

Token transport:
    Authorization: Bearer <jwt>

    WebSocket (query param, since browser WS doesn't support headers):
    ws://host/v1/ws?token=<jwt>

Role hierarchy:
    operator < supervisor < admin
    Each ``require_*`` function accepts its level AND all levels above it.
"""

from __future__ import annotations

import logging

from fastapi import Depends, HTTPException, Query, WebSocket, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError

from app.auth.backend import decode_access_token
from app.auth.models import TokenClaims
from app.db.models import OperatorRole
from app.settings import get_settings

log = logging.getLogger("xray.auth")

_bearer = HTTPBearer(auto_error=False)

# Role ordering for ≥ checks.
_ROLE_LEVEL = {
    OperatorRole.OPERATOR:   1,
    OperatorRole.SUPERVISOR: 2,
    OperatorRole.ADMIN:      3,
}

_BYPASS_CLAIMS = TokenClaims(
    sub="e18dd952-0e93-4bef-8dbe-2694ccd6d66c",
    username="admin",
    role=OperatorRole.ADMIN,
    lane_ids=["lane-1", "lane-2"],
    jti="bypass",
    iat=0,
    exp=9999999999,
)


def _verify(token: str) -> TokenClaims:
    try:
        return decode_access_token(token)
    except JWTError as exc:
        log.warning("JWT verification failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token.",
            headers={"WWW-Authenticate": "Bearer"},
        )


def _require(min_role: OperatorRole):
    """Return a FastAPI dependency that enforces a minimum role."""
    def _dep(creds: HTTPAuthorizationCredentials | None = Depends(_bearer)) -> TokenClaims:
        if get_settings().auth_bypass:
            return _BYPASS_CLAIMS
        if not creds:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Not authenticated.",
                headers={"WWW-Authenticate": "Bearer"},
            )
        claims = _verify(creds.credentials)
        if _ROLE_LEVEL[claims.role] < _ROLE_LEVEL[min_role]:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Role '{claims.role.value}' is insufficient. Required: {min_role.value}.",
            )
        return claims
    return _dep


# Public dependencies — import and use in Depends().
require_operator   = _require(OperatorRole.OPERATOR)
require_supervisor = _require(OperatorRole.SUPERVISOR)
require_admin      = _require(OperatorRole.ADMIN)


# ---------------------------------------------------------------------------
# WebSocket token extraction (query param)
# ---------------------------------------------------------------------------
async def ws_claims(
    websocket: WebSocket,
    token: str = Query(default="bypass", description="JWT access token"),
) -> TokenClaims:
    """Extract and validate JWT from the ``?token=`` query param for WebSocket."""
    if get_settings().auth_bypass:
        return _BYPASS_CLAIMS
    try:
        claims = decode_access_token(token)
    except JWTError:
        await websocket.close(code=4001)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token.")
    return claims


__all__ = [
    "require_operator", "require_supervisor", "require_admin",
    "ws_claims", "TokenClaims",
]
