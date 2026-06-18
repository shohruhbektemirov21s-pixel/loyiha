"""`/v1/verdict` — Hop 3 serving boundary (Detector findings -> Uzbek verdict).

The request *is* a `VerdictRequest`, which embeds the full `DetectionResult`, so
the VLM cannot be invoked without the detector's findings. After generation we
run `validate_referential_integrity`: a verdict that references a detection it
was not given (a hallucination) is rejected here — it never reaches the console.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, status

from contracts.v1 import OperatorVerdict, VerdictRequest, validate_referential_integrity

from app.deps import (
    AuditSink,
    ServiceNotImplemented,
    ScanStore,
    VerdictGenerator,
    provide_audit_sink,
    provide_scan_store,
    provide_verdict_generator,
)
from app.errors import invalid_verdict, not_implemented

router = APIRouter(tags=["verdict"])


@router.post(
    "/verdict",
    response_model=OperatorVerdict,
    status_code=status.HTTP_200_OK,
    summary="Generate a plain-Uzbek, decision-support verdict from detections",
    responses={
        501: {"description": "VLM implementation not wired in yet."},
        502: {"description": "VLM produced an invalid verdict (referential integrity failed)."},
    },
)
async def verdict(
    request: VerdictRequest,
    generator: VerdictGenerator = Depends(provide_verdict_generator),
    audit: AuditSink = Depends(provide_audit_sink),
    store: ScanStore = Depends(provide_scan_store),
) -> OperatorVerdict:
    """Turn detector findings into an operator-facing verdict. Operator decides."""
    await audit.record(
        "verdict.received", scan_id=request.scan_id,
        n_detections=len(request.detection.detections), locale=request.locale.value,
    )
    try:
        result = await generator.generate(request)
    except ServiceNotImplemented as exc:
        raise not_implemented(exc)

    # Fail-closed guardrail at the serving boundary: the VLM cannot invent a
    # detection or cross scans. A violation is the VLM's fault -> 502, not 200.
    try:
        validate_referential_integrity(request, result)
    except ValueError as exc:
        await audit.record("verdict.rejected", scan_id=request.scan_id, reason=str(exc))
        raise invalid_verdict(exc)

    await store.record_verdict(result, result.scan_id)
    await audit.record("verdict.completed", scan_id=result.scan_id, overall_risk=result.overall_risk.value)
    # Push real-time alert to operator console for HIGH/MEDIUM risk scans.
    from app.api.v1.ws import notify_scan_flagged
    from contracts.v1 import RiskBand
    if result.overall_risk in (RiskBand.HIGH, RiskBand.MEDIUM):
        scan_row = await store.get_scan(result.scan_id)
        lane_id = scan_row.lane_id if scan_row else None
        await notify_scan_flagged(
            scan_id=str(result.scan_id),
            lane_id=lane_id,
            risk_band=result.overall_risk.value,
            n_detections=len(result.per_detection),
        )
    return result
