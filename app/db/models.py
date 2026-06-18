"""SQLAlchemy 2.x ORM models — single source of truth for the relational schema.

Every table maps one-to-one to the DDL in ``app/db/schema.sql``. If you add
a column here, add it there and write a migration. If you change a type here,
update both.

Design principles:
* UUID primary keys everywhere — avoids enumerable IDs on a security-sensitive system.
* ``created_at`` / ``updated_at`` are server-side (``server_default=func.now()``);
  never trust a client clock for audit timestamps.
* ``audit_events`` is append-only; no ``updated_at``, no DELETE in application code.
  The DB role used by the API must not have DELETE on this table.
* JSONB payloads for detector/verdict raw blobs — lets the contract evolve without
  schema migrations for every minor field addition, while keeping the typed columns
  for anything we filter/sort/join on.
* All enums are stored as plain VARCHAR — easier to inspect/query than Postgres
  native enums, and changing the Python enum doesn't require a DDL migration.
"""

from __future__ import annotations

import enum
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Sequence,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


# ---------------------------------------------------------------------------
# Base
# ---------------------------------------------------------------------------
class Base(DeclarativeBase):
    # All schema columns are TIMESTAMPTZ. Map Python ``datetime`` to a
    # timezone-aware column so asyncpg encodes UTC-aware values correctly
    # (a naive DateTime() makes asyncpg reject aware datetimes at bind time).
    type_annotation_map = {
        datetime: DateTime(timezone=True),
    }


# ---------------------------------------------------------------------------
# Enumerations (kept in Python only; stored as VARCHAR in the DB)
# ---------------------------------------------------------------------------
class ScanState(str, enum.Enum):
    """Lifecycle state of one scan through the processing pipeline."""
    PENDING    = "pending"     # ingested, not yet sent to detector
    ANALYZING  = "analyzing"   # detector is running
    ANALYZED   = "analyzed"    # detector done, VLM not yet started
    VERDICTED  = "verdicted"   # VLM verdict generated, awaiting operator
    REVIEWING  = "reviewing"   # operator has the console open
    DECIDED    = "decided"     # operator submitted feedback — terminal
    ERROR      = "error"       # any component failure — terminal unless retried


class OperatorRole(str, enum.Enum):
    OPERATOR   = "operator"    # reviews scans on assigned lanes
    SUPERVISOR = "supervisor"  # reviews operator decisions, sees all lanes
    ADMIN      = "admin"       # manages thresholds, operators, system config


# ---------------------------------------------------------------------------
# operators
# ---------------------------------------------------------------------------
class Operator(Base):
    """Authenticated user. One row per human who can log in."""
    __tablename__ = "operators"

    operator_id:     Mapped[UUID]          = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    username:        Mapped[str]           = mapped_column(String(64), nullable=False, unique=True)
    hashed_password: Mapped[str]           = mapped_column(String(128), nullable=False)
    role:            Mapped[str]           = mapped_column(String(32), nullable=False)     # OperatorRole.value
    lane_ids:        Mapped[list[str]]     = mapped_column(JSONB, nullable=False, server_default="[]")
    is_active:       Mapped[bool]          = mapped_column(Boolean, nullable=False, default=True)
    created_at:      Mapped[datetime]      = mapped_column(nullable=False, server_default=func.now())
    last_login_at:   Mapped[datetime|None] = mapped_column(nullable=True)

    __table_args__ = (
        Index("ix_operators_username", "username"),
        Index("ix_operators_role", "role"),
    )


# ---------------------------------------------------------------------------
# scans
# ---------------------------------------------------------------------------
class Scan(Base):
    """One row per physical scan — the primary entity everything else joins on."""
    __tablename__ = "scans"

    scan_id:      Mapped[UUID]          = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    scanner_id:   Mapped[str]           = mapped_column(String(64), nullable=False)
    lane_id:      Mapped[str|None]      = mapped_column(String(64), nullable=True)
    subject:      Mapped[str]           = mapped_column(String(32), nullable=False)   # ScanSubject.value
    modality:     Mapped[str]           = mapped_column(String(32), nullable=False)   # ImageModality.value
    state:        Mapped[str]           = mapped_column(String(32), nullable=False, default=ScanState.PENDING.value)
    overall_risk: Mapped[str|None]      = mapped_column(String(16), nullable=True)    # RiskBand.value once verdicted

    acquired_at:  Mapped[datetime]      = mapped_column(nullable=False)
    analyzed_at:  Mapped[datetime|None] = mapped_column(nullable=True)
    verdicted_at: Mapped[datetime|None] = mapped_column(nullable=True)
    decided_at:   Mapped[datetime|None] = mapped_column(nullable=True)

    # Relationships (lazy="raise" prevents N+1 on list endpoints)
    detections:   Mapped[list["StoredDetection"]] = relationship("StoredDetection", back_populates="scan", lazy="raise")
    verdict:      Mapped["StoredVerdict | None"]   = relationship("StoredVerdict",   back_populates="scan", lazy="raise", uselist=False)
    feedback:     Mapped["StoredFeedback | None"]  = relationship("StoredFeedback",  back_populates="scan", lazy="raise", uselist=False)
    audit_events: Mapped[list["AuditEvent"]]       = relationship("AuditEvent",      back_populates="scan", lazy="raise", foreign_keys="AuditEvent.scan_id")

    __table_args__ = (
        Index("ix_scans_state_acquired", "state", "acquired_at"),
        Index("ix_scans_scanner_id", "scanner_id"),
        Index("ix_scans_lane_id", "lane_id"),
        Index("ix_scans_acquired_at", "acquired_at"),
    )


# ---------------------------------------------------------------------------
# scan_detections
# ---------------------------------------------------------------------------
class StoredDetection(Base):
    """One row per bounding-box detection within a scan."""
    __tablename__ = "scan_detections"

    detection_id: Mapped[UUID]     = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    scan_id:      Mapped[UUID]     = mapped_column(PG_UUID(as_uuid=True), ForeignKey("scans.scan_id", ondelete="CASCADE"), nullable=False)
    frame_id:     Mapped[str]      = mapped_column(String(64), nullable=False)
    category:     Mapped[str]      = mapped_column(String(32), nullable=False)     # ThreatCategory.value
    native_label: Mapped[str]      = mapped_column(String(64), nullable=False)
    score:        Mapped[float]    = mapped_column(Float, nullable=False)
    box_x:        Mapped[int]      = mapped_column(Integer, nullable=False)
    box_y:        Mapped[int]      = mapped_column(Integer, nullable=False)
    box_width:    Mapped[int]      = mapped_column(Integer, nullable=False)
    box_height:   Mapped[int]      = mapped_column(Integer, nullable=False)
    calibrated:   Mapped[bool]     = mapped_column(Boolean, nullable=False, default=False)

    scan: Mapped["Scan"] = relationship("Scan", back_populates="detections")

    __table_args__ = (
        Index("ix_scan_detections_scan_id", "scan_id"),
        Index("ix_scan_detections_category", "category"),
    )


# ---------------------------------------------------------------------------
# scan_verdicts
# ---------------------------------------------------------------------------
class StoredVerdict(Base):
    """One VLM-generated verdict per scan. UNIQUE on scan_id."""
    __tablename__ = "scan_verdicts"

    verdict_id:           Mapped[UUID]      = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    scan_id:              Mapped[UUID]      = mapped_column(PG_UUID(as_uuid=True), ForeignKey("scans.scan_id", ondelete="CASCADE"), nullable=False, unique=True)
    overall_risk:         Mapped[str]       = mapped_column(String(16), nullable=False)
    summary_uz:           Mapped[str]       = mapped_column(Text, nullable=False)
    model_name:           Mapped[str]       = mapped_column(String(64), nullable=False)
    model_version:        Mapped[str]       = mapped_column(String(32), nullable=False)
    model_weights_sha256: Mapped[str|None]  = mapped_column(String(64), nullable=True)
    per_detection_json:   Mapped[dict]      = mapped_column(JSONB, nullable=False, server_default="[]")
    generated_at:         Mapped[datetime]  = mapped_column(nullable=False)

    scan: Mapped["Scan"] = relationship("Scan", back_populates="verdict")

    __table_args__ = (
        Index("ix_scan_verdicts_scan_id", "scan_id"),
        Index("ix_scan_verdicts_overall_risk", "overall_risk"),
    )


# ---------------------------------------------------------------------------
# scan_feedback
# ---------------------------------------------------------------------------
class StoredFeedback(Base):
    """Operator's ground-truth judgement. One row per feedback submission."""
    __tablename__ = "scan_feedback"

    feedback_id:       Mapped[UUID]      = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    scan_id:           Mapped[UUID]      = mapped_column(PG_UUID(as_uuid=True), ForeignKey("scans.scan_id", ondelete="CASCADE"), nullable=False, unique=True)
    operator_id:       Mapped[str]       = mapped_column(String(64), nullable=False)
    outcome:           Mapped[str]       = mapped_column(String(32), nullable=False)   # OperatorOutcome.value
    n_gold_labels:     Mapped[int]       = mapped_column(Integer, nullable=False, default=0)
    n_hard_negatives:  Mapped[int]       = mapped_column(Integer, nullable=False, default=0)
    reviews_json:      Mapped[dict]      = mapped_column(JSONB, nullable=False, server_default="[]")
    missed_json:       Mapped[dict]      = mapped_column(JSONB, nullable=False, server_default="[]")
    decided_at:        Mapped[datetime]  = mapped_column(nullable=False)
    emitted_at:        Mapped[datetime]  = mapped_column(nullable=False)

    scan: Mapped["Scan"] = relationship("Scan", back_populates="feedback")

    __table_args__ = (
        Index("ix_scan_feedback_scan_id", "scan_id"),
        Index("ix_scan_feedback_operator_id", "operator_id"),
        Index("ix_scan_feedback_decided_at", "decided_at"),
    )


# ---------------------------------------------------------------------------
# audit_events  — append-only; the application role must not have DELETE/UPDATE
# ---------------------------------------------------------------------------
_audit_seq = Sequence("audit_event_seq", metadata=Base.metadata)


class AuditEvent(Base):
    """Tamper-evident audit record. Every meaningful action produces one row.

    Chain integrity:
      ``event_hmac`` = HMAC-SHA256(
          key  = XRAY_AUDIT_HMAC_KEY (env),
          data = prev_hmac_hex || event_id.hex || event_type
                 || scan_id_or_empty || operator_id_or_empty
                 || created_at.isoformat()
                 || json.dumps(payload, sort_keys=True, separators=(',',':'))
      )
    Verification: replay the chain from seq=1, re-compute each HMAC, compare.
    Any insertion, deletion, or modification breaks the chain from that point.
    """
    __tablename__ = "audit_events"

    event_id:       Mapped[UUID]      = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    seq:            Mapped[int]       = mapped_column(BigInteger, nullable=False, server_default=_audit_seq.next_value())
    prev_event_id:  Mapped[UUID|None] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("audit_events.event_id"), nullable=True)
    scan_id:        Mapped[UUID|None] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("scans.scan_id"), nullable=True)
    operator_id:    Mapped[str|None]  = mapped_column(String(64), nullable=True)
    event_type:     Mapped[str]       = mapped_column(String(64), nullable=False)
    payload:        Mapped[dict]      = mapped_column(JSONB, nullable=False, server_default="{}")
    created_at:     Mapped[datetime]  = mapped_column(nullable=False, server_default=func.now())
    event_hmac:     Mapped[str]       = mapped_column(String(64), nullable=False)  # 64 hex chars

    scan: Mapped["Scan | None"] = relationship("Scan", back_populates="audit_events", foreign_keys=[scan_id])

    __table_args__ = (
        Index("ix_audit_events_seq", "seq", unique=True),
        Index("ix_audit_events_scan_id", "scan_id"),
        Index("ix_audit_events_operator_id", "operator_id"),
        Index("ix_audit_events_event_type", "event_type"),
        Index("ix_audit_events_created_at", "created_at"),
    )


# ---------------------------------------------------------------------------
# threshold_configs  — per-category confidence thresholds, admin-managed
# ---------------------------------------------------------------------------
class ThresholdConfig(Base):
    """Active confidence thresholds per threat category.

    Only ONE row per category should be ``is_active=True`` at any time.
    The unique partial index (see schema.sql) enforces this.
    Old rows are retained (``is_active=False``) for audit history.
    """
    __tablename__ = "threshold_configs"

    config_id:          Mapped[UUID]     = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    category:           Mapped[str]      = mapped_column(String(32), nullable=False)  # ThreatCategory.value
    alert_threshold:    Mapped[float]    = mapped_column(Float, nullable=False)        # score >= this → operator alert
    auto_clear_threshold: Mapped[float]  = mapped_column(Float, nullable=False)        # score < this → no alert
    updated_by:         Mapped[str]      = mapped_column(String(64), nullable=False)
    updated_at:         Mapped[datetime] = mapped_column(nullable=False, server_default=func.now())
    is_active:          Mapped[bool]     = mapped_column(Boolean, nullable=False, default=True)
    note:               Mapped[str|None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        Index("ix_threshold_configs_category_active", "category", "is_active"),
    )


__all__ = [
    "Base",
    "ScanState", "OperatorRole",
    "Operator", "Scan",
    "StoredDetection", "StoredVerdict", "StoredFeedback",
    "AuditEvent", "ThresholdConfig",
]
