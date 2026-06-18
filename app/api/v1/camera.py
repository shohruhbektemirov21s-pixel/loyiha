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

import asyncio
import os

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Response, status
from fastapi.responses import StreamingResponse
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


# ===========================================================================
# UZLUKSIZ VIDEO OQIMI + QWEN DOIMIY TAHLIL
# ===========================================================================
# Mavjud bitta-snapshot `/camera/capture` flow'i o'zgarmaydi. Quyidagilar uning
# YONIDA yangi uzluksiz oqimni qo'shadi:
#   GET  /v1/camera/live.mjpg     — MJPEG jonli preview (?token= bilan)
#   POST /v1/camera/stream/start  — uzluksiz capture + tahlil loop'ini boshlaydi
#   POST /v1/camera/stream/stop   — loop va kamerani to'xtatadi, resurslarni tozalaydi
#   GET  /v1/camera/stream/status — joriy holat
#
# Bitta jarayonda bitta faol oqim. Manager modul darajasida singleton — start/
# stop/status/live.mjpg shu obyektni ulashadi. Asyncio bilan himoyalangan.


class _StreamManager:
    """Faol video oqimi va doimiy tahlil loop'ini boshqaradigan singleton.

    Bitta jarayonda bitta kamera oqimi bo'ladi. ``start`` idempotent. ``stop``
    capture thread va VideoCapture/VideoWriter ni to'g'ri release qiladi
    (thread leak bo'lmasin).
    """

    def __init__(self) -> None:
        self._capture = None      # camera.stream.VideoStreamCapture
        self._analyzer = None     # camera.stream.ContinuousAnalyzer
        self._lock = asyncio.Lock()

    @property
    def running(self) -> bool:
        return self._analyzer is not None and self._analyzer.state.running

    async def start(
        self,
        *,
        device: int | str | None,
        cadence_s: float | None,
        detector,
        generator,
        lane_id: str | None,
    ) -> dict:
        from camera.composition import build_camera_config
        from camera.stream import ContinuousAnalyzer, VideoStreamCapture
        from app.deps import ServiceNotImplemented

        async with self._lock:
            if self.running:
                return self._status_dict()  # idempotent

            cfg = build_camera_config()
            if device is not None:
                cfg.device = int(device) if (isinstance(device, str) and device.isdigit()) else device

            cad = cadence_s if cadence_s is not None else float(
                os.environ.get("XRAY_CAM_CADENCE_S", "2.0")
            )
            record = os.environ.get("XRAY_CAM_RECORD", "").strip().lower() in ("1", "true", "yes", "on")

            capture = VideoStreamCapture(cfg, record=record)
            # cv2 ochish bloklovchi — threadpool'da.
            try:
                await run_in_threadpool(capture.open)
            except Exception as exc:  # noqa: BLE001 — kamera ochilmasa 503
                try:
                    await run_in_threadpool(capture.close)
                except Exception:
                    pass
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail=f"Kamera oqimi ochilmadi: {exc}",
                )

            from app.api.v1.ws import get_hub
            try:
                hub = get_hub()
                broadcast = hub.broadcast_lane
            except RuntimeError:
                # Hub hali init bo'lmagan (masalan testlarда) — jim no-op.
                async def broadcast(_lane, _msg):  # type: ignore[misc]
                    return None

            analyzer = ContinuousAnalyzer(
                capture,
                detector=detector,
                generator=generator,
                broadcast=broadcast,
                lane_id=lane_id,
                cadence_s=cad,
                not_implemented_exc=ServiceNotImplemented,
            )
            analyzer.start()

            self._capture = capture
            self._analyzer = analyzer
            log.info("Kamera oqimi boshlandi (device=%s cadence=%.2fs record=%s).", cfg.device, cad, record)
            return self._status_dict()

    async def stop(self) -> dict:
        async with self._lock:
            if self._analyzer is not None:
                await self._analyzer.stop()
            if self._capture is not None:
                await run_in_threadpool(self._capture.close)
            status_dict = self._status_dict()
            self._analyzer = None
            self._capture = None
            log.info("Kamera oqimi to'xtatildi va resurslar tozalandi.")
            return status_dict

    def status(self) -> dict:
        return self._status_dict()

    def latest_jpeg(self) -> bytes | None:
        if self._capture is None:
            return None
        return self._capture.latest_jpeg()

    def _status_dict(self) -> dict:
        if self._analyzer is None or self._capture is None:
            return {
                "running": False,
                "device": None,
                "cadence_s": None,
                "last_analysis_ts": None,
                "frames_analyzed": 0,
                "recording": False,
            }
        st = self._analyzer.state
        return {
            "running": st.running,
            "device": st.device,
            "cadence_s": st.cadence_s,
            "last_analysis_ts": st.last_analysis_ts,
            "frames_analyzed": st.frames_analyzed,
            "recording": self._capture.recording,
            "record_path": str(self._capture.record_path) if self._capture.record_path else None,
            "last_risk_band": st.last_risk_band,
        }


# Modul darajasidagi singleton. app/main.py lifespan shutdown'da to'xtatadi.
_stream_manager = _StreamManager()


def get_stream_manager() -> _StreamManager:
    return _stream_manager


# ---------------------------------------------------------------------------
# Request/response shapes
# ---------------------------------------------------------------------------
class StreamStartRequest(BaseModel):
    device: int | str | None = None
    cadence_s: float | None = None


# ---------------------------------------------------------------------------
# MJPEG live preview
# ---------------------------------------------------------------------------
def _mjpeg_generator(manager: _StreamManager):
    """multipart/x-mixed-replace MJPEG oqimi. Eng so'nggi kadrni uzatadi."""
    boundary = b"--frame\r\n"
    while True:
        if not manager.running:
            break
        jpeg = manager.latest_jpeg()
        if jpeg is not None:
            yield boundary
            yield b"Content-Type: image/jpeg\r\n"
            yield b"Content-Length: " + str(len(jpeg)).encode() + b"\r\n\r\n"
            yield jpeg
            yield b"\r\n"
        time.sleep(0.05)  # ~20 fps cap; CPU/bandwidth himoyasi


@router.get(
    "/camera/live.mjpg",
    summary="Live MJPEG preview (token via query param for <img src>)",
    responses={200: {"content": {"multipart/x-mixed-replace": {}}}},
)
async def live_mjpg(
    token: str = Query(default="", description="JWT access token (query param for <img>)."),
) -> StreamingResponse:
    # Yengil auth: bypass rejimi yoki query param'dagi yaroqli JWT (<img>
    # header qo'ya olmaydi — get_frame bilan bir xil pattern).
    if not get_settings().auth_bypass:
        from jose import JWTError
        from app.auth.backend import decode_access_token
        try:
            decode_access_token(token)
        except JWTError:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token.")

    manager = get_stream_manager()
    if not manager.running:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Kamera oqimi ishlamayapti. Avval /v1/camera/stream/start ni chaqiring.",
        )
    return StreamingResponse(
        _mjpeg_generator(manager),
        media_type="multipart/x-mixed-replace; boundary=frame",
    )


# ---------------------------------------------------------------------------
# Stream lifecycle endpoints (operator+ JWT)
# ---------------------------------------------------------------------------
@router.post(
    "/camera/stream/start",
    summary="Start the continuous capture + Qwen analysis loop (idempotent)",
)
async def stream_start(
    body: StreamStartRequest | None = None,
    claims: TokenClaims = Depends(require_operator),
    detector: Detector = Depends(provide_detector),
    generator: VerdictGenerator = Depends(provide_verdict_generator),
) -> dict:
    body = body or StreamStartRequest()
    lane_id = claims.lane_ids[0] if claims.lane_ids else None
    manager = get_stream_manager()
    return await manager.start(
        device=body.device,
        cadence_s=body.cadence_s,
        detector=detector,
        generator=generator,
        lane_id=lane_id,
    )


@router.post(
    "/camera/stream/stop",
    summary="Stop the continuous loop and release the camera",
)
async def stream_stop(
    claims: TokenClaims = Depends(require_operator),
) -> dict:
    manager = get_stream_manager()
    return await manager.stop()


@router.get(
    "/camera/stream/status",
    summary="Current continuous-stream status",
)
async def stream_status(
    claims: TokenClaims = Depends(require_operator),
) -> dict:
    return get_stream_manager().status()
