"""`/v1/detect` — Hop 2 serving boundary (Scanner output -> Detector findings)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, status

from contracts.v1 import AcquisitionResult, DetectionResult

from app.deps import (
    AuditSink,
    Detector,
    ServiceNotImplemented,
    ScanStore,
    provide_audit_sink,
    provide_detector,
    provide_scan_store,
)
from app.errors import not_implemented

router = APIRouter(tags=["detect"])


@router.post(
    "/detect",
    response_model=DetectionResult,
    status_code=status.HTTP_200_OK,
    summary="Run the primary object detector over an acquired scan",
    responses={501: {"description": "Detector implementation not wired in yet."}},
)
async def detect(
    acquisition: AcquisitionResult,
    detector: Detector = Depends(provide_detector),
    audit: AuditSink = Depends(provide_audit_sink),
    store: ScanStore = Depends(provide_scan_store),
) -> DetectionResult:
    """Localize and classify items in a scan.

    The detector is the **primary** detector — the only component that decides
    *where* and *what*. Its output (`DetectionResult`) becomes the VLM's sole
    input on the next hop.
    """
    await store.record_acquisition(acquisition)
    await audit.record(
        "detect.received", scan_id=acquisition.scan_id,
        scanner_id=acquisition.scanner_id, n_frames=len(acquisition.frames),
    )
    try:
        result = await detector.detect(acquisition)
    except ServiceNotImplemented as exc:
        raise not_implemented(exc)
    await store.record_detection(result)
    await audit.record(
        "detect.completed", scan_id=result.scan_id,
        status=result.status.value, n_detections=len(result.detections),
    )
    return result
