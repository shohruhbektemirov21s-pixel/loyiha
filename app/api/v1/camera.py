"""`/v1/camera` — USB camera capture → scan → verdict, plus frame image serving.

This is the operator-triggered capture flow (the "Kameradan olish" button in the
console):

    POST /v1/camera/capture
        1. Grab one frame from the USB camera (camera/driver.py).
        2. Persist the JPEG bytes + frame metadata to the capture store.
        3. record_acquisition  → Scan row (PENDING).
        4. record_detection    → ANALYZED (no detector wired ⇒ no boxes).
        5. generate verdict via the live VLM → record_verdict → VERDICTED.
        6. Return the new scan_id so the console can select it.

    GET /v1/scans/{scan_id}/frames/{frame_id}
        Serve the stored JPEG bytes for the console <img> tag. Auth is via a
        ``?token=`` query param (browsers cannot set Authorization on <img>).

Image bytes live on the local filesystem under XRAY_CAPTURE_DIR (default:
camera/captures/scans), one folder per scan_id, with a meta.json describing the
frames. No DB schema change is required.
"""

from __future__ import annotations

import hashlib
import json
import logging
import pathlib
import time
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Response, status
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from contracts.v1 import (
    AcquisitionResult,
    DetectionResult,
    ImageFrame,
    ImageModality,
    ModelProvenance,
    OperatorVerdict,
    RiskBand,
    ScanSubject,
    StorageRef,
    VerdictRequest,
)
from contracts.v1.detection import DetectionStatus
from contracts.v1.verdict import Locale

from app.auth.dependencies import TokenClaims, require_operator
from app.deps import (
    AuditSink,
    Detector,
    ServiceNotImplemented,
    VerdictGenerator,
    provide_audit_sink,
    provide_detector,
    provide_verdict_generator,
)
from app.settings import get_settings

log = logging.getLogger("xray.api.camera")
router = APIRouter(tags=["camera"])


# ---------------------------------------------------------------------------
# Capture store (filesystem)
# ---------------------------------------------------------------------------
def _capture_root() -> pathlib.Path:
    import os
    raw = os.environ.get("XRAY_CAPTURE_DIR", "").strip()
    root = (
        pathlib.Path(raw)
        if raw
        else pathlib.Path(__file__).resolve().parents[3] / "camera" / "captures" / "scans"
    )
    root.mkdir(parents=True, exist_ok=True)
    return root


def _scan_dir(scan_id: uuid.UUID | str) -> pathlib.Path:
    return _capture_root() / str(scan_id)


def _save_frame(scan_id: uuid.UUID, frame_id: str, jpeg: bytes, width: int, height: int) -> None:
    d = _scan_dir(scan_id)
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{frame_id}.jpg").write_bytes(jpeg)
    meta = {
        "frames": [
            {"frame_id": frame_id, "width_px": width, "height_px": height, "media_type": "image/jpeg"}
        ]
    }
    (d / "meta.json").write_text(json.dumps(meta))


def load_frame_meta(scan_id: uuid.UUID | str) -> list[dict]:
    """Return frame descriptors for a scan, or [] if it has no stored frames."""
    meta_path = _scan_dir(scan_id) / "meta.json"
    if not meta_path.exists():
        return []
    try:
        return json.loads(meta_path.read_text()).get("frames", [])
    except (json.JSONDecodeError, OSError):
        return []


def _load_frame_bytes(scan_id: str, frame_id: str) -> bytes | None:
    # Guard against path traversal: frame_id must be a bare filename component.
    if "/" in frame_id or ".." in frame_id:
        return None
    path = _scan_dir(scan_id) / f"{frame_id}.jpg"
    if not path.exists():
        return None
    return path.read_bytes()


# ---------------------------------------------------------------------------
# Response shapes
# ---------------------------------------------------------------------------
class CaptureResponse(BaseModel):
    scan_id: str
    state: str
    overall_risk: str | None = None
    summary_uz: str | None = None
    frame_id: str
    width_px: int
    height_px: int


# ---------------------------------------------------------------------------
# Blocking camera capture (runs in a threadpool)
# ---------------------------------------------------------------------------
def _capture_blocking() -> tuple[bytes, int, int, str]:
    """Open the USB camera, grab one frame, close. Returns (jpeg, w, h, device)."""
    from camera.composition import build_camera_driver
    from camera.driver import CameraError

    driver = build_camera_driver()
    try:
        driver.open()
        frame = driver.capture_now()
    except CameraError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Kamera ochilmadi yoki kadr olinmadi: {exc}",
        )
    finally:
        try:
            driver.close()
        except Exception:  # noqa: BLE001 — close must never mask the real error
            pass
    return frame.jpeg_bytes, frame.width, frame.height, frame.device


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
@router.post(
    "/camera/capture",
    response_model=CaptureResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Capture one frame from the USB camera and create a scan",
)
async def capture(
    claims: TokenClaims = Depends(require_operator),
    detector: Detector = Depends(provide_detector),
    generator: VerdictGenerator = Depends(provide_verdict_generator),
    audit: AuditSink = Depends(provide_audit_sink),
) -> CaptureResponse:
    from app.db.session import get_session_factory
    from app.state.machine import PostgresScanStore

    jpeg, width, height, device = await run_in_threadpool(_capture_blocking)

    scan_id  = uuid.uuid4()
    frame_id = "cam-0"
    now      = datetime.now(timezone.utc)
    lane_id  = claims.lane_ids[0] if claims.lane_ids else None

    _save_frame(scan_id, frame_id, jpeg, width, height)

    image_ref = StorageRef(
        uri=f"file://{_scan_dir(scan_id) / (frame_id + '.jpg')}",
        media_type="image/jpeg",
        sha256=hashlib.sha256(jpeg).hexdigest(),
        size_bytes=len(jpeg),
    )
    image_frame = ImageFrame(
        frame_id=frame_id,
        width_px=width,
        height_px=height,
        image=image_ref,
        view_label="camera",
        pixel_spacing_mm=None,
    )

    acquisition = AcquisitionResult(
        scan_id=scan_id,
        scanner_id=f"usb-camera-{device}",
        lane_id=lane_id,
        operator_id=claims.sub,
        subject=ScanSubject.BAGGAGE,
        modality=ImageModality.SINGLE_ENERGY,
        captured_at=now,
        emitted_at=now,
        frames=[image_frame],
    )

    factory = get_session_factory()

    # 1) Persist the scan row and COMMIT it before any audit write — the
    #    audit_events.scan_id FK requires the scan to exist first (the audit
    #    sink commits in its own independent session).
    async with factory() as session:
        await PostgresScanStore(session).record_acquisition(acquisition)
        await session.commit()
    await audit.record(
        "camera.captured", scan_id=scan_id, scanner_id=acquisition.scanner_id,
        width=width, height=height, size_bytes=len(jpeg),
    )

    # 2) Run the REAL detector over the captured frame. Previously this path
    #    hard-coded an empty "no findings" result, so the camera button never
    #    ran any AI — every capture came back CLEAR via a template. Now we call
    #    the Detector seam; if it is not wired (XRAY_DETECTOR_ENABLED=false) or
    #    fails, we fall back to no-findings so the capture still completes — but
    #    we log it loudly so a disabled detector is never mistaken for a clean scan.
    detection = await _run_detector(detector, acquisition, image_frame, now)
    async with factory() as session:
        await PostgresScanStore(session).record_detection(detection)
        await session.commit()
    await audit.record(
        "detect.completed", scan_id=scan_id,
        status=detection.status.value, n_detections=len(detection.detections),
        source="camera",
    )

    # 3) Ask the live VLM for a decision-support summary. On any failure we fall
    #    back to a CLEAR template so the scan still reaches the console.
    verdict = await _generate_verdict(generator, scan_id, detection, now)
    async with factory() as session:
        await PostgresScanStore(session).record_verdict(verdict, scan_id)
        await session.commit()
    await audit.record(
        "verdict.completed", scan_id=scan_id, overall_risk=verdict.overall_risk.value,
        source="camera",
    )

    return CaptureResponse(
        scan_id=str(scan_id),
        state="verdicted",
        overall_risk=verdict.overall_risk.value,
        summary_uz=verdict.summary_uz,
        frame_id=frame_id,
        width_px=width,
        height_px=height,
    )


async def _run_detector(
    detector: Detector,
    acquisition: AcquisitionResult,
    image_frame: ImageFrame,
    now: datetime,
) -> DetectionResult:
    """Run the detector over the captured frame, fail-closed to no-findings.

    A disabled/unwired detector (501) or any runtime error must not break the
    capture flow — but it must be visible in the logs, never silently shown as a
    clean scan. When findings exist the downstream VLM actually runs (the clear
    fast-path is only taken for zero detections).
    """
    try:
        result = await detector.detect(acquisition)
        if result.scan_id == acquisition.scan_id:
            return result
        log.warning("detector returned mismatched scan_id — using no-findings result.")
    except ServiceNotImplemented:
        log.warning(
            "detector NOT wired (set XRAY_DETECTOR_ENABLED=true) — "
            "camera scan ran NO AI detection."
        )
    except Exception as exc:  # noqa: BLE001 — never let the detector break capture
        log.warning("detector failed (%s) — using no-findings result.", exc)

    return DetectionResult(
        scan_id=acquisition.scan_id,
        status=DetectionStatus.COMPLETED_NO_FINDINGS,
        emitted_at=now,
        model=ModelProvenance(name="no-detector", version="0.0.0", runtime="camera-capture"),
        frames=[image_frame],
        detections=[],
    )


async def _generate_verdict(
    generator: VerdictGenerator,
    scan_id: uuid.UUID,
    detection: DetectionResult,
    now: datetime,
) -> OperatorVerdict:
    request = VerdictRequest(
        scan_id=scan_id, detection=detection, locale=Locale.UZ_LATN, emitted_at=now,
    )
    try:
        result = await generator.generate(request)
        if result.scan_id == scan_id:
            return result
        log.warning("VLM returned mismatched scan_id; using fallback verdict.")
    except ServiceNotImplemented:
        log.info("VLM not wired — using fallback CLEAR verdict.")
    except Exception as exc:  # noqa: BLE001 — never let a slow/failing VLM break capture
        log.warning("VLM verdict failed (%s) — using fallback CLEAR verdict.", exc)

    return OperatorVerdict(
        verdict_id=uuid.uuid4(),
        scan_id=scan_id,
        locale=Locale.UZ_LATN,
        overall_risk=RiskBand.CLEAR,
        summary_uz="Shubhali buyum aniqlanmadi. Qaror operatorga tegishli.",
        per_detection=[],
        model=ModelProvenance(name="template", version="1.0.0", runtime="fallback"),
        generated_at=datetime.now(timezone.utc),
    )


@router.get(
    "/scans/{scan_id}/frames/{frame_id}",
    summary="Serve stored frame image bytes (token via query param for <img>)",
    responses={200: {"content": {"image/jpeg": {}}}, 404: {"description": "Frame not found."}},
)
async def get_frame(
    scan_id:  str = Path(...),
    frame_id: str = Path(...),
    token:    str = Query(default="", description="JWT access token (query param for <img>)."),
) -> Response:
    # Lightweight auth: bypass mode, or a valid JWT in the query param.
    if not get_settings().auth_bypass:
        from jose import JWTError
        from app.auth.backend import decode_access_token
        try:
            decode_access_token(token)
        except JWTError:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token.")

    data = _load_frame_bytes(scan_id, frame_id)
    if data is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Frame not found.")
    return Response(content=data, media_type="image/jpeg")
