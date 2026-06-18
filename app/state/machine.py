"""Verdict state machine and ScanStore seam.

State machine:
    PENDING → ANALYZING  (detect.received)
    ANALYZING → ANALYZED (detect.completed)
    ANALYZING → ERROR    (detect.failed)
    ANALYZED  → VERDICTED (verdict.completed)
    ANALYZED  → ERROR    (verdict.failed)
    VERDICTED → REVIEWING (scan.opened)
    REVIEWING → DECIDED  (feedback.banked)
    ANALYZED  → DECIDED  (feedback.banked when no findings — auto-advance)

Any transition not listed above is rejected with an ``InvalidTransitionError``.
Terminal states (DECIDED, ERROR) may only be re-entered by a supervisor-level
retry operation (not implemented here; guard it at the API layer).

ScanStore seam:
    A thin Protocol that the three endpoint handlers depend on.  The
    PostgresScanStore implementation writes to the DB.  The
    _NullScanStore stub returns silently (used when DB is not wired).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol, runtime_checkable
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Scan, ScanState, StoredDetection, StoredFeedback, StoredVerdict
from contracts.v1 import AcquisitionResult, DetectionResult, OperatorVerdict
from contracts.v1.feedback import FeedbackReceipt, OperatorFeedback

log = logging.getLogger("xray.state")


# ---------------------------------------------------------------------------
# Transition table
# ---------------------------------------------------------------------------
_ALLOWED: dict[tuple[str, str], str] = {
    (ScanState.PENDING,   "detect.received"):   ScanState.ANALYZING,
    (ScanState.ANALYZING, "detect.completed"):  ScanState.ANALYZED,
    (ScanState.ANALYZING, "detect.failed"):     ScanState.ERROR,
    (ScanState.ANALYZED,  "verdict.completed"): ScanState.VERDICTED,
    (ScanState.ANALYZED,  "verdict.failed"):    ScanState.ERROR,
    (ScanState.ANALYZED,  "feedback.banked"):   ScanState.DECIDED,   # no-finding auto-advance
    (ScanState.VERDICTED, "scan.opened"):       ScanState.REVIEWING,
    (ScanState.REVIEWING, "feedback.banked"):   ScanState.DECIDED,
}

_TERMINAL = {ScanState.DECIDED, ScanState.ERROR}


class InvalidTransitionError(ValueError):
    pass


class ConcurrentTransitionError(InvalidTransitionError):
    """Raised when an atomic transition's CAS fails — another writer advanced the
    scan out of the expected state between our read and our write (TOCTOU race).

    Subclasses InvalidTransitionError so existing callers that already guard
    against InvalidTransitionError treat a lost race as a rejected transition.
    """


def allowed_transition(from_state: str, event: str) -> str:
    """Return the target state or raise ``InvalidTransitionError``."""
    key = (ScanState(from_state), event)
    if key not in _ALLOWED:
        raise InvalidTransitionError(
            f"State '{from_state}' + event '{event}' is not a valid transition. "
            f"Valid events from this state: "
            f"{[e for (s, e) in _ALLOWED if s.value == from_state]}"
        )
    return _ALLOWED[key].value


# ---------------------------------------------------------------------------
# ScanStore Protocol
# ---------------------------------------------------------------------------
@runtime_checkable
class ScanStore(Protocol):
    """Persists scan lifecycle data and enforces state transitions.

    Each ``record_*`` method:
      1. Upserts the corresponding row(s) in the DB.
      2. Advances the scan state via ``_transition()``.
      3. Updates the scan's timestamp column (analyzed_at, etc.).
    """

    async def record_acquisition(self, acquisition: AcquisitionResult) -> None: ...
    async def record_detection(self, result: DetectionResult) -> None: ...
    async def record_verdict(self, verdict: OperatorVerdict, scan_id: UUID) -> None: ...
    async def record_feedback(self, feedback: OperatorFeedback, receipt: FeedbackReceipt) -> None: ...
    async def get_scan(self, scan_id: UUID) -> "ScanRow | None": ...
    async def mark_reviewing(self, scan_id: UUID) -> bool: ...


# ---------------------------------------------------------------------------
# Lightweight read model
# ---------------------------------------------------------------------------
@dataclass
class ScanRow:
    scan_id: UUID
    state: str
    scanner_id: str
    lane_id: str | None
    subject: str
    modality: str
    overall_risk: str | None
    acquired_at: datetime
    analyzed_at: datetime | None
    verdicted_at: datetime | None
    decided_at: datetime | None


# ---------------------------------------------------------------------------
# PostgreSQL implementation
# ---------------------------------------------------------------------------
class PostgresScanStore:
    """Writes to the database inside the caller's request-scoped session."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # -- helpers -----------------------------------------------------------
    async def _get_state(self, scan_id: UUID) -> str:
        row = await self._session.get(Scan, scan_id)
        if row is None:
            raise ValueError(f"scan_id={scan_id} not found")
        return row.state

    async def _transition(self, scan_id: UUID, event: str, **ts_fields) -> str:
        """Validate + apply a state transition atomically (compare-and-swap).

        The UPDATE carries ``WHERE state = :expected`` so the read of the current
        state and the write of the next one are a single atomic operation at the
        database. If a concurrent writer (e.g. a second operator decision, or a
        late detector callback) advanced the scan out of the expected state
        between our read and our write, ``rowcount`` is 0 and we raise
        ``ConcurrentTransitionError`` rather than clobbering the other writer's
        result. This closes the TOCTOU race that a plain get→check→update had.
        """
        row = await self._session.get(Scan, scan_id)
        if row is None:
            raise ValueError(f"Scan {scan_id} not found in DB")
        expected = row.state
        new_state = allowed_transition(expected, event)
        stmt = (
            update(Scan)
            .where(Scan.scan_id == scan_id, Scan.state == expected)
            .values(state=new_state, **ts_fields)
        )
        result = await self._session.execute(stmt)
        if result.rowcount == 0:
            # The CAS lost: the row no longer holds `expected`. Refusing to
            # overwrite is fail-closed — the concurrent decision stands.
            raise ConcurrentTransitionError(
                f"Concurrent state change on scan {scan_id}: expected state "
                f"'{expected}' for event '{event}' but the row was modified by "
                f"another writer. Transition rejected."
            )
        # Keep the identity-mapped object consistent with the DB write so any
        # subsequent read in this session sees the new state.
        row.state = new_state
        log.info("scan %s: %s → %s (via %s)", scan_id, expected, new_state, event)
        return new_state

    # -- public interface --------------------------------------------------
    async def record_acquisition(self, acquisition: AcquisitionResult) -> None:
        """Insert the scan row (idempotent on conflict)."""
        from sqlalchemy.dialects.postgresql import insert as pg_insert
        stmt = pg_insert(Scan).values(
            scan_id=acquisition.scan_id,
            scanner_id=acquisition.scanner_id,
            lane_id=acquisition.lane_id,
            subject=acquisition.subject.value,
            modality=acquisition.modality.value,
            state=ScanState.PENDING.value,
            acquired_at=acquisition.captured_at,
        ).on_conflict_do_nothing(index_elements=["scan_id"])
        await self._session.execute(stmt)

    async def record_detection(self, result: DetectionResult) -> None:
        scan_id = result.scan_id
        now = datetime.now(timezone.utc)

        # Upsert detections (idempotent: skip on conflict).
        from sqlalchemy.dialects.postgresql import insert as pg_insert
        for det in result.detections:
            stmt = pg_insert(StoredDetection).values(
                detection_id=det.detection_id,
                scan_id=scan_id,
                frame_id=det.frame_id,
                category=det.category.value,
                native_label=det.native_label,
                score=float(det.score),
                box_x=det.box.x,
                box_y=det.box.y,
                box_width=det.box.width,
                box_height=det.box.height,
                # `calibrated` is now a first-class typed field on Detection.
                # Backward-compatible: an older producer that only set the legacy
                # attributes["calibrated"]="true" string still works — we OR the
                # typed field with the attributes fallback.
                calibrated=bool(det.calibrated) or (det.attributes.get("calibrated") == "true"),
            ).on_conflict_do_nothing(index_elements=["detection_id"])
            await self._session.execute(stmt)

        # Transition ANALYZING → ANALYZED or (if PENDING → ANALYZING first)
        state = await self._get_state(scan_id)
        if state == ScanState.PENDING.value:
            await self._transition(scan_id, "detect.received")
            state = ScanState.ANALYZING.value
        if state == ScanState.ANALYZING.value:
            await self._transition(scan_id, "detect.completed", analyzed_at=now)

    async def record_verdict(self, verdict: OperatorVerdict, scan_id: UUID) -> None:
        now = datetime.now(timezone.utc)
        from sqlalchemy.dialects.postgresql import insert as pg_insert
        per_det = [
            {
                "detection_id": str(dv.detection_id),
                "category": dv.category.value,
                "rationale_uz": dv.rationale_uz,
                "confidence": float(dv.confidence),
            }
            for dv in verdict.per_detection
        ]
        stmt = pg_insert(StoredVerdict).values(
            verdict_id=verdict.verdict_id,
            scan_id=scan_id,
            overall_risk=verdict.overall_risk.value,
            summary_uz=verdict.summary_uz,
            model_name=verdict.model.name,
            model_version=verdict.model.version,
            model_weights_sha256=verdict.model.weights_sha256,
            per_detection_json=per_det,
            generated_at=verdict.generated_at,
        ).on_conflict_do_nothing(index_elements=["scan_id"])
        await self._session.execute(stmt)
        # Transition: ANALYZED → VERDICTED
        state = await self._get_state(scan_id)
        if state == ScanState.ANALYZED.value:
            await self._transition(
                scan_id, "verdict.completed",
                verdicted_at=now,
                overall_risk=verdict.overall_risk.value,
            )

    async def record_feedback(
        self, feedback: OperatorFeedback, receipt: FeedbackReceipt
    ) -> None:
        now = datetime.now(timezone.utc)
        from sqlalchemy.dialects.postgresql import insert as pg_insert
        stmt = pg_insert(StoredFeedback).values(
            feedback_id=feedback.feedback_id,
            scan_id=feedback.scan_id,
            operator_id=str(feedback.operator_id),
            outcome=feedback.outcome.value,
            n_gold_labels=receipt.labels_queued,
            n_hard_negatives=receipt.hard_negatives_queued,
            reviews_json=[r.model_dump(mode="json") for r in feedback.reviews],
            missed_json=[m.model_dump(mode="json") for m in feedback.missed],
            decided_at=feedback.decided_at,
            emitted_at=feedback.emitted_at,
        ).on_conflict_do_nothing(index_elements=["scan_id"])
        await self._session.execute(stmt)
        # Transition → DECIDED
        state = await self._get_state(feedback.scan_id)
        if state in (ScanState.VERDICTED.value, ScanState.REVIEWING.value, ScanState.ANALYZED.value):
            await self._transition(feedback.scan_id, "feedback.banked", decided_at=now)

    async def mark_reviewing(self, scan_id: UUID) -> bool:
        """Transition VERDICTED → REVIEWING. Returns True iff a transition was
        applied (so the caller only writes an audit event for a real change)."""
        state = await self._get_state(scan_id)
        if state == ScanState.VERDICTED.value:
            await self._transition(scan_id, "scan.opened")
            return True
        return False

    async def get_scan(self, scan_id: UUID) -> ScanRow | None:
        row = await self._session.get(Scan, scan_id)
        if row is None:
            return None
        return ScanRow(
            scan_id=row.scan_id, state=row.state,
            scanner_id=row.scanner_id, lane_id=row.lane_id,
            subject=row.subject, modality=row.modality,
            overall_risk=row.overall_risk,
            acquired_at=row.acquired_at, analyzed_at=row.analyzed_at,
            verdicted_at=row.verdicted_at, decided_at=row.decided_at,
        )


# ---------------------------------------------------------------------------
# Null stub — when DB is not wired
# ---------------------------------------------------------------------------
class _NullScanStore:
    async def record_acquisition(self, acquisition):  pass
    async def record_detection(self, result):          pass
    async def record_verdict(self, verdict, scan_id):  pass
    async def record_feedback(self, feedback, receipt): pass
    async def get_scan(self, scan_id):                 return None
    async def mark_reviewing(self, scan_id):           return False


__all__ = [
    "ScanState", "InvalidTransitionError", "ConcurrentTransitionError",
    "allowed_transition",
    "ScanStore", "ScanRow", "PostgresScanStore", "_NullScanStore",
]
