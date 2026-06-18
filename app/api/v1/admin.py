"""`/v1/admin` — system administration endpoints (admin role only).

Covers:
  * Operator management (create, deactivate, list)
  * Confidence threshold management (read, update)
  * Audit chain verification
  * Auth: POST /v1/auth/login (returns JWT)
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.backend import create_access_token, hash_password, verify_password
from app.auth.dependencies import TokenClaims, require_admin, require_operator
from app.auth.models import (
    LoginRequest, OperatorCreateRequest, OperatorResponse, TokenResponse,
)
from app.db.models import Operator, OperatorRole, ThresholdConfig
from app.db.session import get_db
from app.settings import get_settings

log = logging.getLogger("xray.api.admin")

router = APIRouter(tags=["admin"])

# ---------------------------------------------------------------------------
# Auth — login (public)
# ---------------------------------------------------------------------------
auth_router = APIRouter(tags=["auth"], prefix="/auth")


@auth_router.post("/login", response_model=TokenResponse, summary="Exchange credentials for JWT")
async def login(
    body: LoginRequest,
    db:   AsyncSession = Depends(get_db),
) -> TokenResponse:
    row = (await db.execute(
        select(Operator).where(Operator.username == body.username, Operator.is_active.is_(True))
    )).scalar_one_or_none()

    if row is None or not verify_password(body.password, row.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password.",
        )

    # Update last_login_at.
    await db.execute(
        update(Operator)
        .where(Operator.operator_id == row.operator_id)
        .values(last_login_at=datetime.now(timezone.utc))
    )

    settings = get_settings()
    token = create_access_token(
        operator_id=str(row.operator_id),
        username=row.username,
        role=OperatorRole(row.role),
        lane_ids=row.lane_ids or [],
        expires_in_seconds=settings.jwt_expires_seconds,
    )
    return TokenResponse(
        access_token=token,
        expires_in=settings.jwt_expires_seconds,
        operator_id=str(row.operator_id),
        username=row.username,
        role=OperatorRole(row.role),
        lane_ids=row.lane_ids or [],
    )


# ---------------------------------------------------------------------------
# Operator management (admin only)
# ---------------------------------------------------------------------------
admin_router = APIRouter(tags=["admin"], prefix="/admin")


@admin_router.get("/operators", response_model=list[OperatorResponse])
async def list_operators(
    claims: TokenClaims  = Depends(require_admin),
    db:     AsyncSession = Depends(get_db),
) -> list[OperatorResponse]:
    rows = (await db.execute(select(Operator).order_by(Operator.username))).scalars().all()
    return [
        OperatorResponse(
            operator_id=r.operator_id, username=r.username,
            role=OperatorRole(r.role), lane_ids=r.lane_ids or [],
            is_active=r.is_active, created_at=r.created_at,
            last_login_at=r.last_login_at,
        )
        for r in rows
    ]


@admin_router.post("/operators", response_model=OperatorResponse, status_code=status.HTTP_201_CREATED)
async def create_operator(
    body:   OperatorCreateRequest,
    claims: TokenClaims  = Depends(require_admin),
    db:     AsyncSession = Depends(get_db),
) -> OperatorResponse:
    existing = (await db.execute(
        select(Operator).where(Operator.username == body.username)
    )).scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Username already exists.")

    op = Operator(
        username=body.username,
        hashed_password=hash_password(body.password),
        role=body.role.value,
        lane_ids=body.lane_ids,
    )
    db.add(op)
    await db.flush()
    return OperatorResponse(
        operator_id=op.operator_id, username=op.username,
        role=OperatorRole(op.role), lane_ids=op.lane_ids or [],
        is_active=op.is_active, created_at=op.created_at,
        last_login_at=op.last_login_at,
    )


@admin_router.delete("/operators/{operator_id}", status_code=status.HTTP_204_NO_CONTENT)
async def deactivate_operator(
    operator_id: UUID,
    claims: TokenClaims  = Depends(require_admin),
    db:     AsyncSession = Depends(get_db),
) -> None:
    """Soft-delete: set is_active=False. Preserves audit history."""
    await db.execute(
        update(Operator)
        .where(Operator.operator_id == operator_id)
        .values(is_active=False)
    )


# ---------------------------------------------------------------------------
# Threshold management
# ---------------------------------------------------------------------------
class ThresholdOut(BaseModel):
    config_id: UUID
    category: str
    alert_threshold: float
    auto_clear_threshold: float
    updated_by: str
    updated_at: datetime
    note: Optional[str]


class ThresholdUpdate(BaseModel):
    alert_threshold: float
    auto_clear_threshold: float
    note: Optional[str] = None


@admin_router.get("/thresholds", response_model=list[ThresholdOut])
async def list_thresholds(
    claims: TokenClaims  = Depends(require_admin),
    db:     AsyncSession = Depends(get_db),
) -> list[ThresholdOut]:
    rows = (await db.execute(
        select(ThresholdConfig).where(ThresholdConfig.is_active.is_(True))
        .order_by(ThresholdConfig.category)
    )).scalars().all()
    return [
        ThresholdOut(
            config_id=r.config_id, category=r.category,
            alert_threshold=r.alert_threshold,
            auto_clear_threshold=r.auto_clear_threshold,
            updated_by=r.updated_by, updated_at=r.updated_at,
            note=r.note,
        )
        for r in rows
    ]


@admin_router.put("/thresholds/{category}", response_model=ThresholdOut)
async def update_threshold(
    category: str,
    body:   ThresholdUpdate,
    claims: TokenClaims  = Depends(require_admin),
    db:     AsyncSession = Depends(get_db),
) -> ThresholdOut:
    if body.auto_clear_threshold > body.alert_threshold:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="auto_clear_threshold must be ≤ alert_threshold.",
        )

    # Deactivate the current active row (audit history preserved).
    await db.execute(
        update(ThresholdConfig)
        .where(ThresholdConfig.category == category, ThresholdConfig.is_active.is_(True))
        .values(is_active=False)
    )
    # Insert the new active row.
    new = ThresholdConfig(
        category=category,
        alert_threshold=body.alert_threshold,
        auto_clear_threshold=body.auto_clear_threshold,
        updated_by=claims.username,
        note=body.note,
    )
    db.add(new)
    await db.flush()

    # Invalidate the threshold cache if it exists in app state.
    from app.deps import _threshold_cache
    if _threshold_cache is not None:
        _threshold_cache.invalidate()

    return ThresholdOut(
        config_id=new.config_id, category=new.category,
        alert_threshold=new.alert_threshold,
        auto_clear_threshold=new.auto_clear_threshold,
        updated_by=new.updated_by, updated_at=new.updated_at,
        note=new.note,
    )


# ---------------------------------------------------------------------------
# Audit chain verification (admin only)
# ---------------------------------------------------------------------------
class ChainVerifyOut(BaseModel):
    ok: bool
    message: str


@admin_router.get("/audit/verify", response_model=ChainVerifyOut)
async def verify_audit_chain(
    limit: int = 0,
    claims: TokenClaims  = Depends(require_admin),
    db:     AsyncSession = Depends(get_db),
) -> ChainVerifyOut:
    from app.audit.sink import verify_chain
    from app.settings import get_settings
    import os
    key_bytes = bytes.fromhex(os.environ.get("XRAY_AUDIT_HMAC_KEY", "00" * 32))
    ok, msg = await verify_chain(db, key_bytes, limit=limit)
    return ChainVerifyOut(ok=ok, message=msg)
