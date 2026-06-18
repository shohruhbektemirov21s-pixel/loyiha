"""`/v1/scans` — scan lifecycle query endpoints.

Operators query scan history, retrieve verdicts, and see per-scan audit
trails here. All writes go through /v1/detect, /v1/verdict, /v1/feedback;
these endpoints are read-only (GET only).

Access:
  * operator   — own-lane scans only
  * supervisor — all scans
  * admin      — all scans + audit trail
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.auth.dependencies import TokenClaims, require_operator, require_supervisor
from app.auth.models import OperatorResponse
from app.db.models import (
    AuditEvent,
    OperatorRole,
    Scan,
    ScanState,
    StoredDetection,
    StoredFeedback,
    StoredVerdict,
)
from app.db.session import get_db

log = logging.getLogger("xray.api.scans")
router = APIRouter(tags=["scans"], prefix="/scans")


# ---------------------------------------------------------------------------
# Response shapes
# ---------------------------------------------------------------------------
class DetectionOut(BaseModel):
    detection_id: UUID
    frame_id: str
    category: str
    native_label: str
    score: float
    box_x: int; box_y: int; box_width: int; box_height: int
    calibrated: bool


class VerdictOut(BaseModel):
    verdict_id: UUID
    overall_risk: str
    summary_uz: str
    model_name: str
    model_version: str
    per_detection: list[dict]
    generated_at: datetime


class FeedbackOut(BaseModel):
    feedback_id: UUID
    operator_id: str
    outcome: str
    n_gold_labels: int
    n_hard_negatives: int
    decided_at: datetime


class ScanOut(BaseModel):
    scan_id: UUID
    scanner_id: str
    lane_id: Optional[str]
    subject: str
    modality: str
    state: str
    overall_risk: Optional[str]
    acquired_at: datetime
    analyzed_at: Optional[datetime]
    verdicted_at: Optional[datetime]
    decided_at: Optional[datetime]


class FrameOut(BaseModel):
    frame_id: str
    width_px: int
    height_px: int
    media_type: str = "image/jpeg"


class ScanDetailOut(ScanOut):
    frames: list[FrameOut] = []
    detections: list[DetectionOut] = []
    verdict: Optional[VerdictOut] = None
    feedback: Optional[FeedbackOut] = None


class AuditEventOut(BaseModel):
    event_id: UUID
    seq: int
    event_type: str
    operator_id: Optional[str]
    payload: dict
    created_at: datetime


class ScanListOut(BaseModel):
    items: list[ScanOut]
    total: int
    page: int
    page_size: int


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _check_lane_access(scan: Scan, claims: TokenClaims) -> None:
    """Operators can only see scans from their assigned lanes."""
    if claims.role == OperatorRole.OPERATOR:
        if claims.lane_ids and scan.lane_id not in claims.lane_ids:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                                detail="You are not assigned to this lane.")


def _to_scan_out(scan: Scan) -> ScanOut:
    return ScanOut(
        scan_id=scan.scan_id, scanner_id=scan.scanner_id,
        lane_id=scan.lane_id, subject=scan.subject, modality=scan.modality,
        state=scan.state, overall_risk=scan.overall_risk,
        acquired_at=scan.acquired_at, analyzed_at=scan.analyzed_at,
        verdicted_at=scan.verdicted_at, decided_at=scan.decided_at,
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
@router.get("", response_model=ScanListOut, summary="List scans (paginated)")
async def list_scans(
    state:     Optional[str]  = Query(None, description="Filter by ScanState value"),
    lane_id:   Optional[str]  = Query(None),
    since:     Optional[datetime] = Query(None, description="acquired_at ≥ since"),
    page:      int            = Query(1, ge=1),
    page_size: int            = Query(50, ge=1, le=200),
    claims:    TokenClaims    = Depends(require_operator),
    db:        AsyncSession   = Depends(get_db),
) -> ScanListOut:
    stmt = select(Scan).order_by(Scan.acquired_at.desc())

    if state:
        stmt = stmt.where(Scan.state == state)
    if since:
        stmt = stmt.where(Scan.acquired_at >= since)

    # Operators only see their own lanes.
    if claims.role == OperatorRole.OPERATOR and claims.lane_ids:
        stmt = stmt.where(Scan.lane_id.in_(claims.lane_ids))
    elif lane_id:
        stmt = stmt.where(Scan.lane_id == lane_id)

    total_result = await db.execute(select(func.count()).select_from(stmt.subquery()))
    total = total_result.scalar_one()

    stmt = stmt.offset((page - 1) * page_size).limit(page_size)
    rows = (await db.execute(stmt)).scalars().all()

    return ScanListOut(
        items=[_to_scan_out(r) for r in rows],
        total=total, page=page, page_size=page_size,
    )


@router.get("/{scan_id}", response_model=ScanDetailOut, summary="Get full scan detail")
async def get_scan(
    scan_id: UUID          = Path(...),
    claims:  TokenClaims   = Depends(require_operator),
    db:      AsyncSession  = Depends(get_db),
) -> ScanDetailOut:
    stmt = (
        select(Scan)
        .where(Scan.scan_id == scan_id)
        .options(
            selectinload(Scan.detections),
            selectinload(Scan.verdict),
            selectinload(Scan.feedback),
        )
    )
    row = (await db.execute(stmt)).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Scan not found.")
    _check_lane_access(row, claims)

    detections = [
        DetectionOut(
            detection_id=d.detection_id, frame_id=d.frame_id,
            category=d.category, native_label=d.native_label,
            score=d.score, box_x=d.box_x, box_y=d.box_y,
            box_width=d.box_width, box_height=d.box_height,
            calibrated=d.calibrated,
        )
        for d in row.detections
    ]

    verdict_out: VerdictOut | None = None
    if row.verdict:
        v = row.verdict
        verdict_out = VerdictOut(
            verdict_id=v.verdict_id, overall_risk=v.overall_risk,
            summary_uz=v.summary_uz, model_name=v.model_name,
            model_version=v.model_version, per_detection=v.per_detection_json,
            generated_at=v.generated_at,
        )

    feedback_out: FeedbackOut | None = None
    if row.feedback:
        f = row.feedback
        feedback_out = FeedbackOut(
            feedback_id=f.feedback_id, operator_id=f.operator_id,
            outcome=f.outcome, n_gold_labels=f.n_gold_labels,
            n_hard_negatives=f.n_hard_negatives, decided_at=f.decided_at,
        )

    from app.api.v1.camera import load_frame_meta
    frames = [FrameOut(**f) for f in load_frame_meta(scan_id)]

    return ScanDetailOut(
        **_to_scan_out(row).model_dump(),
        frames=frames,
        detections=detections,
        verdict=verdict_out,
        feedback=feedback_out,
    )


@router.get(
    "/{scan_id}/audit",
    response_model=list[AuditEventOut],
    summary="Audit trail for one scan (admin/supervisor only)",
)
async def get_scan_audit(
    scan_id: UUID         = Path(...),
    claims:  TokenClaims  = Depends(require_supervisor),
    db:      AsyncSession = Depends(get_db),
) -> list[AuditEventOut]:
    # Verify scan exists and caller can see it.
    scan = await db.get(Scan, scan_id)
    if scan is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Scan not found.")
    _check_lane_access(scan, claims)

    rows = (await db.execute(
        select(AuditEvent)
        .where(AuditEvent.scan_id == scan_id)
        .order_by(AuditEvent.seq)
    )).scalars().all()

    return [
        AuditEventOut(
            event_id=ev.event_id, seq=ev.seq,
            event_type=ev.event_type, operator_id=ev.operator_id,
            payload=ev.payload, created_at=ev.created_at,
        )
        for ev in rows
    ]


@router.post(
    "/{scan_id}/review",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Mark scan as being reviewed by this operator",
)
async def mark_reviewing(
    scan_id: UUID         = Path(...),
    claims:  TokenClaims  = Depends(require_operator),
    db:      AsyncSession = Depends(get_db),
) -> None:
    """Transition VERDICTED → REVIEWING and record in audit."""
    from app.state.machine import PostgresScanStore
    from app.deps import provide_audit_sink
    store = PostgresScanStore(db)
    await store.mark_reviewing(scan_id)


# ---------------------------------------------------------------------------
# Operator decision (confirm / reject) → archive
# ---------------------------------------------------------------------------
class DecisionRequest(BaseModel):
    decision: str   # "confirmed" (safe, passed) | "rejected" (suspicious, stopped)
    note: Optional[str] = None


class DecisionResponse(BaseModel):
    scan_id: UUID
    state: str
    outcome: str
    decided_at: datetime


# confirmed → cleared (operator passed it); rejected → seized (operator stopped it)
_DECISION_OUTCOME = {"confirmed": "cleared", "rejected": "seized"}


@router.post(
    "/{scan_id}/decision",
    response_model=DecisionResponse,
    summary="Confirm or reject a scan; moves it to DECIDED (archive)",
)
async def decide_scan(
    scan_id: UUID            = Path(...),
    body:    DecisionRequest = ...,
    claims:  TokenClaims     = Depends(require_operator),
    db:      AsyncSession    = Depends(get_db),
) -> DecisionResponse:
    """Lightweight archive decision — no active-learning loop required.

    'confirmed' = operator deems the bag safe (cleared).
    'rejected'  = operator flags the bag as suspicious (seized).
    Either way the scan transitions to DECIDED and leaves the open queue.
    """
    from uuid import uuid4
    from app.state.machine import PostgresScanStore
    from app.deps import provide_audit_sink

    if body.decision not in _DECISION_OUTCOME:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="decision must be 'confirmed' or 'rejected'.",
        )
    outcome = _DECISION_OUTCOME[body.decision]

    scan = await db.get(Scan, scan_id)
    if scan is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Scan not found.")
    _check_lane_access(scan, claims)

    if scan.state == ScanState.DECIDED.value:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Scan already decided.")
    if scan.state not in (
        ScanState.ANALYZED.value, ScanState.VERDICTED.value, ScanState.REVIEWING.value,
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot decide a scan in state '{scan.state}'.",
        )

    now = datetime.now(timezone.utc)
    store = PostgresScanStore(db)

    # Persist the operator's judgement (idempotent on scan_id).
    from sqlalchemy.dialects.postgresql import insert as pg_insert
    await db.execute(
        pg_insert(StoredFeedback).values(
            feedback_id=uuid4(),
            scan_id=scan_id,
            operator_id=claims.sub,
            outcome=outcome,
            n_gold_labels=0,
            n_hard_negatives=0,
            reviews_json=[],
            missed_json=[],
            decided_at=now,
            emitted_at=now,
        ).on_conflict_do_nothing(index_elements=["scan_id"])
    )

    # Drive the state machine to DECIDED (VERDICTED must pass through REVIEWING).
    if scan.state == ScanState.VERDICTED.value:
        await store._transition(scan_id, "scan.opened")
    await store._transition(scan_id, "feedback.banked", decided_at=now)

    audit = provide_audit_sink()
    await audit.record(
        "scan.decided", scan_id=scan_id, operator_id=claims.sub,
        decision=body.decision, outcome=outcome, note=body.note,
    )

    # Notify supervisors in real time.
    from app.api.v1.ws import notify_scan_decided
    await notify_scan_decided(
        scan_id=str(scan_id), lane_id=scan.lane_id,
        outcome=outcome, operator_id=claims.sub,
    )

    return DecisionResponse(scan_id=scan_id, state=ScanState.DECIDED.value, outcome=outcome, decided_at=now)
