"""JWT encoding/decoding and password hashing.

JWT (HS256):
  - Secret from XRAY_JWT_SECRET (min 32 chars). Fail-closed if missing.
  - 8-hour expiry by default (one shift). Configurable via Settings.
  - jti (JWT ID) is a random UUID — enables future revocation checking.
  - No external calls; all verification is local.
  - Algorithms: only HS256 is accepted on decode to prevent the "none" attack.

Password hashing:
  - bcrypt via passlib with cost factor 12.
  - All password verification uses a constant-time compare (passlib default).
"""

from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime, timezone, timedelta
from typing import Any

import bcrypt as _bcrypt
from jose import JWTError, jwt

from app.auth.models import TokenClaims
from app.db.models import OperatorRole

log = logging.getLogger("xray.auth")

_ALGORITHM = "HS256"
_ACCEPTED_ALGORITHMS = ["HS256"]
_BCRYPT_ROUNDS = 12


# ---------------------------------------------------------------------------
# Secret management
# ---------------------------------------------------------------------------
def _get_jwt_secret(override: str | None = None) -> str:
    secret = override or os.environ.get("XRAY_JWT_SECRET", "")
    if len(secret) < 32:
        raise RuntimeError(
            "XRAY_JWT_SECRET is not set or is shorter than 32 characters. "
            "Generate with: python -c \"import secrets; print(secrets.token_hex(32))\""
        )
    return secret


# ---------------------------------------------------------------------------
# Password hashing
# ---------------------------------------------------------------------------
def hash_password(plain: str) -> str:
    return _bcrypt.hashpw(plain.encode(), _bcrypt.gensalt(rounds=_BCRYPT_ROUNDS)).decode()


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return _bcrypt.checkpw(plain.encode(), hashed.encode())
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Token creation
# ---------------------------------------------------------------------------
def create_access_token(
    operator_id: str,
    username: str,
    role: OperatorRole,
    lane_ids: list[str],
    *,
    expires_in_seconds: int = 28800,  # 8 hours
    secret: str | None = None,
) -> str:
    now = datetime.now(timezone.utc)
    exp = now + timedelta(seconds=expires_in_seconds)
    claims: dict[str, Any] = {
        "sub":      operator_id,
        "username": username,
        "role":     role.value,
        "lane_ids": lane_ids,
        "jti":      str(uuid.uuid4()),
        "iat":      int(now.timestamp()),
        "exp":      int(exp.timestamp()),
    }
    return jwt.encode(claims, _get_jwt_secret(secret), algorithm=_ALGORITHM)


# ---------------------------------------------------------------------------
# Token verification
# ---------------------------------------------------------------------------
def decode_access_token(token: str, *, secret: str | None = None) -> TokenClaims:
    """Decode and validate a JWT. Raises ``JWTError`` on any problem."""
    raw = jwt.decode(
        token,
        _get_jwt_secret(secret),
        algorithms=_ACCEPTED_ALGORITHMS,
        options={"verify_exp": True},
    )
    return TokenClaims(
        sub=raw["sub"],
        username=raw["username"],
        role=OperatorRole(raw["role"]),
        lane_ids=raw.get("lane_ids", []),
        jti=raw["jti"],
        iat=raw["iat"],
        exp=raw["exp"],
    )


__all__ = [
    "hash_password", "verify_password",
    "create_access_token", "decode_access_token",
]
