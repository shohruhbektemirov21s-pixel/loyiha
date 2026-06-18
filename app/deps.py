"""Service seams (the boundaries each specialist track implements) + DI wiring.

The routers depend on *Protocols*, not concrete classes. Until a track ships its
implementation, the default provider returns a stub that raises
``ServiceNotImplemented`` -> HTTP 501. This makes the API runnable today (live
OpenAPI, integration stubs) while the seams stay honest: an unimplemented layer
fails loudly, it never returns a fake verdict.

To plug in a real implementation, override the provider with FastAPI's
``app.dependency_overrides`` (or swap the default in one place here).
"""

from __future__ import annotations

import logging
from typing import AsyncGenerator, Protocol, runtime_checkable
from uuid import UUID

from contracts.v1 import (
    AcquisitionResult,
    DetectionResult,
    OperatorVerdict,
    VerdictRequest,
)
from contracts.v1.feedback import FeedbackReceipt, OperatorFeedback
from app.state.machine import ScanStore, _NullScanStore

log = logging.getLogger("xray.app")


class ServiceNotImplemented(RuntimeError):
    """A seam has no implementation yet. Mapped to HTTP 501 — never faked."""


# ---------------------------------------------------------------------------
# Seams
# ---------------------------------------------------------------------------
@runtime_checkable
class Detector(Protocol):
    """Hop 2 owner. The primary object-detection model."""

    async def detect(self, acquisition: AcquisitionResult) -> DetectionResult: ...


@runtime_checkable
class VerdictGenerator(Protocol):
    """Hop 3 owner. The local Qwen3-VL verbalizer. Reasons only over detections."""

    async def generate(self, request: VerdictRequest) -> OperatorVerdict: ...


@runtime_checkable
class Screener(Protocol):
    """Yuk rentgen skrining seam'i (operator rasm yuklash oqimi).

    Operator kamera o'rniga rentgen rasm(lar)ni yuklaydi; Qwen VLM vagon/yuk
    ichida nima borligini tasvirlaydi va konservativ bayroq beradi. Bu QAROR
    emas, operatorga SKRINING YORDAMI. Implementatsiya ``vlm.screen.CargoScreener``.
    """

    async def screen_one(self, image_bytes: bytes, filename: str): ...

    async def screen_many(self, items: list[tuple[bytes, str]]): ...


@runtime_checkable
class AuditSink(Protocol):
    """Append-only audit trail. Every hop crossing is recorded here."""

    async def record(self, event: str, *, scan_id, **fields) -> None: ...


@runtime_checkable
class FeedbackSink(Protocol):
    """Hop 4 owner. Closes the active-learning loop.

    Receives ``OperatorFeedback``, runs the quality gate, enqueues labels,
    and returns a ``FeedbackReceipt``. Implementation lives in the data layer.
    """

    async def record(self, feedback: OperatorFeedback) -> FeedbackReceipt: ...


# ---------------------------------------------------------------------------
# Default (stub) implementations
# ---------------------------------------------------------------------------
class _UnimplementedDetector:
    async def detect(self, acquisition: AcquisitionResult) -> DetectionResult:
        raise ServiceNotImplemented("Detector not wired in. Override `provide_detector`.")


class _UnimplementedVerdictGenerator:
    async def generate(self, request: VerdictRequest) -> OperatorVerdict:
        raise ServiceNotImplemented("VerdictGenerator not wired in. Override `provide_verdict_generator`.")


class _UnimplementedScreener:
    """Default: VLM ulanmagan -> 501 (fail-closed). Jim soxta natija emas.

    ``provide_screener`` ni ``app.main._wire_screener`` haqiqiy
    ``CargoScreener`` bilan override qiladi (XRAY_VLM_ENABLED bo'lsa).
    """

    async def screen_one(self, image_bytes: bytes, filename: str):
        raise ServiceNotImplemented(
            "Screener ulanmagan. XRAY_VLM_ENABLED ni yoqing yoki `provide_screener` ni override qiling."
        )

    async def screen_many(self, items: list[tuple[bytes, str]]):
        raise ServiceNotImplemented(
            "Screener ulanmagan. XRAY_VLM_ENABLED ni yoqing yoki `provide_screener` ni override qiling."
        )


class _LoggingAuditSink:
    """Default audit sink: structured log line. Replace with the Postgres sink."""

    async def record(self, event: str, *, scan_id, **fields) -> None:
        log.info("audit", extra={"event": event, "scan_id": str(scan_id), **fields})


class _UnimplementedFeedbackSink:
    async def record(self, feedback: OperatorFeedback) -> FeedbackReceipt:
        raise ServiceNotImplemented("FeedbackSink not wired in. Override `provide_feedback_sink`.")


# Module-level singletons; swap these (or use dependency_overrides) to wire reality.
_detector: Detector = _UnimplementedDetector()
_verdict_generator: VerdictGenerator = _UnimplementedVerdictGenerator()
_screener: Screener = _UnimplementedScreener()
_audit_sink: AuditSink = _LoggingAuditSink()
_feedback_sink: FeedbackSink = _UnimplementedFeedbackSink()


# FastAPI dependency providers ------------------------------------------------
def provide_detector() -> Detector:
    return _detector


def provide_verdict_generator() -> VerdictGenerator:
    return _verdict_generator


def provide_screener() -> Screener:
    return _screener


def provide_audit_sink() -> AuditSink:
    return _audit_sink


def provide_feedback_sink() -> FeedbackSink:
    return _feedback_sink


# ---------------------------------------------------------------------------
# ScanStore seam (request-scoped: built per request when DB is wired)
# ---------------------------------------------------------------------------
_db_enabled: bool = False

async def provide_scan_store() -> AsyncGenerator[ScanStore, None]:
    """Yield a per-request ScanStore backed by the DB if wired, null stub otherwise."""
    if not _db_enabled:
        yield _NullScanStore()
        return
    from app.db.session import get_session_factory
    from app.state.machine import PostgresScanStore
    factory = get_session_factory()
    async with factory() as session:
        try:
            yield PostgresScanStore(session)
            await session.commit()
        except Exception:
            await session.rollback()
            raise


def enable_db() -> None:
    """Called during lifespan when the DB is confirmed ready."""
    global _db_enabled
    _db_enabled = True


# ThresholdCache module-level ref (set by lifespan, used by admin router).
_threshold_cache = None
