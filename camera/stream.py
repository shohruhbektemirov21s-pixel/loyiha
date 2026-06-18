"""Uzluksiz video oqimi + Qwen doimiy tahlil (continuous analysis).

Maqsad
------
Mavjud ``USBCameraDriver`` faqat harakat bilan ishga tushadigan BITTA snapshot
beradi. Bu modul esa kamerani UZLUKSIZ video manbai sifatida ishlatadi:

  * ``VideoStreamCapture`` — fon (background) thread'da ``cv2.VideoCapture``
    orqali kadrlarni TO'XTOVSIZ o'qiydi. Eng so'nggi kadrni xotirada
    (thread-safe) saqlaydi — MJPEG jonli preview va davriy tahlil shu yerdan
    oziqlanadi. Ixtiyoriy: sessiyani diskka video (.mp4/.avi) yozib boradi
    ("kamerani bosgan video" = yozilgan fayl).

  * ``ContinuousAnalyzer`` — asyncio task. Sozlanadigan kadensda (``cadence_s``)
    eng so'nggi kadrni oladi → detektor (``Detector``) → Qwen VLM
    (``VerdictGenerator``) → har natijani "camera.analysis" WS xabari sifatida
    operatorlarga uzluksiz yuboradi. To'xtatilguncha to'xtovsiz ishlaydi
    ("qwen doim tahlil qilib tursin").

Fail-safe falsafasi
-------------------
Detektor yoki VLM ulanmagan (501/stub) bo'lsa yoki xato bersa: balandtovush
WARNING log + tahlil natijasi "mavjud emas" deb belgilanadi (jim soxta "toza"
emas). Video oqimi va tahlil loop'i YIQILMAYDI.

Qaror-yordami falsafasi saqlanadi: VLM o'zi qaror qilmaydi; risk band
detektorning kalibrlangan ballaridan (mavjud generator allaqachon shunday
qiladi) keladi.

Resurs xavfsizligi
------------------
``stop()`` capture thread'ini va ``cv2.VideoCapture``/``VideoWriter`` ni to'g'ri
release qiladi. Thread leak bo'lmasin — thread daemon va join'lanadi.

cv2 lazy-import qilinadi: ``import camera.stream`` cv2 ni tortmaydi, modul GPU'siz
yoki displaysiz serverda ham yuklanadi.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import pathlib
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Optional

import numpy as np

from camera.driver import CameraConfig, CameraError, CameraOpenError, CameraReadError

log = logging.getLogger("xray.camera.stream")


# ---------------------------------------------------------------------------
# Background video capture
# ---------------------------------------------------------------------------
class VideoStreamCapture:
    """Kameradan kadrlarni FONDA uzluksiz o'qiydigan thread-safe manba.

    Eng so'nggi kadr (BGR ndarray) va uning JPEG ko'rinishi xotirada saqlanadi.
    MJPEG preview ``latest_jpeg()`` dan, davriy tahlil ``latest_frame()`` dan
    oziqlanadi. Ixtiyoriy: ``record=True`` bo'lsa sessiya diskka yoziladi.

    Thread-safe: ``latest_frame``/``latest_jpeg`` istalgan thread'dan chaqirilsa
    bo'ladi; ichki reader thread mustaqil ishlaydi.
    """

    def __init__(
        self,
        cfg: CameraConfig | None = None,
        *,
        record: bool = False,
        record_dir: pathlib.Path | None = None,
    ) -> None:
        self._cfg = cfg or CameraConfig()
        self._cap = None  # cv2.VideoCapture
        self._writer = None  # cv2.VideoWriter (record bo'lsa)
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

        # Eng so'nggi kadr (lock ostida).
        self._latest_bgr: np.ndarray | None = None
        self._latest_jpeg: bytes | None = None
        self._latest_ts: float = 0.0
        self._frame_event = threading.Event()  # birinchi kadr kelganini bildiradi

        self._frames_read: int = 0
        self._read_errors: int = 0

        # Yozib borish (recording)
        self._record = record
        self._record_dir = record_dir
        self._record_path: pathlib.Path | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def open(self) -> None:
        """Kamerani ochadi va fon reader thread'ini ishga tushiradi. Fail-closed."""
        import cv2  # lazy import

        cfg = self._cfg
        device = cfg.device if isinstance(cfg.device, str) else int(cfg.device)
        log.info(
            "Video oqimi ochilmoqda: device=%s (%dx%d @ %dfps) record=%s",
            device, cfg.width, cfg.height, cfg.fps, self._record,
        )

        cap = cv2.VideoCapture(device)
        if not cap.isOpened():
            raise CameraOpenError(
                f"cv2.VideoCapture({device!r}) ochilmadi. "
                f"Tekshiring: ls /dev/video*, v4l2-ctl --list-devices"
            )

        cap.set(cv2.CAP_PROP_FRAME_WIDTH, cfg.width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, cfg.height)
        cap.set(cv2.CAP_PROP_FPS, cfg.fps)

        actual_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        actual_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        actual_fps = cap.get(cv2.CAP_PROP_FPS)
        log.info("Video oqimi ochildi: haqiqiy %dx%d @ %.1ffps", actual_w, actual_h, actual_fps)

        self._cap = cap

        if self._record:
            self._open_writer(actual_w, actual_h, actual_fps)

        self._stop.clear()
        self._frame_event.clear()
        self._thread = threading.Thread(
            target=self._reader_loop, name="camera-stream-reader", daemon=True
        )
        self._thread.start()

    def _open_writer(self, w: int, h: int, fps: float) -> None:
        """Sessiya videosini diskka yozish uchun VideoWriter ochadi (best-effort)."""
        import cv2

        out_dir = self._record_dir or (pathlib.Path(__file__).resolve().parent / "captures" / "videos")
        out_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        path = out_dir / f"session-{ts}.mp4"

        write_fps = fps if fps and fps > 1.0 else float(self._cfg.fps or 20)
        fourcc = cv2.VideoWriter_fourcc(*"mp4v")
        writer = cv2.VideoWriter(str(path), fourcc, write_fps, (w, h))
        if not writer.isOpened():
            # mp4 codec topilmasa AVI/MJPG ga tushamiz — yozish to'xtab qolmasin.
            path = path.with_suffix(".avi")
            fourcc = cv2.VideoWriter_fourcc(*"MJPG")
            writer = cv2.VideoWriter(str(path), fourcc, write_fps, (w, h))

        if not writer.isOpened():
            log.warning("Video yozuvchi ochilmadi — recording o'chirildi.")
            self._record = False
            self._writer = None
            return

        self._writer = writer
        self._record_path = path
        log.info("Sessiya videosi yozilmoqda: %s (%dx%d @ %.1ffps)", path, w, h, write_fps)

    def close(self) -> None:
        """Reader thread'ini to'xtatadi, kamera va writer'ni release qiladi."""
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3.0)
            self._thread = None
        with self._lock:
            if self._writer is not None:
                try:
                    self._writer.release()
                except Exception:  # noqa: BLE001 — release hech qachon xato yashirmasin
                    pass
                self._writer = None
            if self._cap is not None:
                try:
                    self._cap.release()
                except Exception:  # noqa: BLE001
                    pass
                self._cap = None
        log.info("Video oqimi yopildi (kadrlar=%d, xatolar=%d).", self._frames_read, self._read_errors)

    def __enter__(self) -> "VideoStreamCapture":
        self.open()
        return self

    def __exit__(self, *_) -> None:
        self.close()

    @property
    def is_open(self) -> bool:
        return self._cap is not None

    @property
    def device(self) -> str:
        return str(self._cfg.device)

    @property
    def recording(self) -> bool:
        return self._writer is not None

    @property
    def record_path(self) -> pathlib.Path | None:
        return self._record_path

    @property
    def frames_read(self) -> int:
        return self._frames_read

    # ------------------------------------------------------------------
    # Reader thread
    # ------------------------------------------------------------------
    def _reader_loop(self) -> None:
        import cv2

        cfg = self._cfg
        log.debug("Video reader boshlandi (device=%s)", cfg.device)

        while not self._stop.is_set():
            cap = self._cap
            if cap is None:
                break
            ok, bgr = cap.read()
            if not ok or bgr is None or bgr.size == 0:
                self._read_errors += 1
                log.warning("Video read xatosi device=%s — qayta urinish…", cfg.device)
                time.sleep(0.05)
                continue

            # ROI crop (agar sozlangan bo'lsa).
            if cfg.roi:
                x, y, w, h = cfg.roi
                bgr = bgr[y:y + h, x:x + w]

            # JPEG kodlash (MJPEG preview uchun).
            ok2, buf = cv2.imencode(
                ".jpg", bgr, [cv2.IMWRITE_JPEG_QUALITY, cfg.encode_quality]
            )
            jpeg = buf.tobytes() if ok2 else None

            with self._lock:
                self._latest_bgr = bgr
                if jpeg is not None:
                    self._latest_jpeg = jpeg
                self._latest_ts = time.time()
                self._frames_read += 1
                if self._writer is not None:
                    try:
                        self._writer.write(bgr)
                    except Exception as exc:  # noqa: BLE001 — yozuv xatosi oqimni yiqitmasin
                        log.warning("Video yozuv xatosi: %s", exc)
            self._frame_event.set()

        log.debug("Video reader to'xtadi.")

    # ------------------------------------------------------------------
    # Frame API (thread-safe)
    # ------------------------------------------------------------------
    def wait_first_frame(self, timeout_s: float = 5.0) -> bool:
        """Birinchi kadr kelguncha kutadi. True = kadr tayyor."""
        return self._frame_event.wait(timeout=timeout_s)

    def latest_frame(self) -> tuple[np.ndarray, float] | None:
        """Eng so'nggi BGR kadr nusxasi va vaqt tamg'asi, yoki None (hali yo'q)."""
        with self._lock:
            if self._latest_bgr is None:
                return None
            return self._latest_bgr.copy(), self._latest_ts

    def latest_jpeg(self) -> bytes | None:
        """Eng so'nggi kadrning JPEG baytlari (MJPEG preview uchun), yoki None."""
        with self._lock:
            return self._latest_jpeg


# ---------------------------------------------------------------------------
# Continuous analysis loop
# ---------------------------------------------------------------------------
# Detektor va VLM seam protokollari app.deps da; tip- check'ni yengillashtirish
# uchun bu yerda Any sifatida qabul qilamiz (modul app paketiga bog'lanmasin).
BroadcastFn = Callable[[Optional[str], dict[str, Any]], Awaitable[None]]


@dataclass
class StreamState:
    """Tashqaridan (status endpoint) o'qiladigan oqim holati."""

    running: bool = False
    device: str = ""
    cadence_s: float = 2.0
    last_analysis_ts: float | None = None
    frames_analyzed: int = 0
    recording: bool = False
    record_path: str | None = None
    last_risk_band: str | None = None
    last_error: str | None = None


class ContinuousAnalyzer:
    """Eng so'nggi kadrni davriy ravishda detektor+VLM orqali tahlil qiladi.

    Har bir tsiklda:
      1. ``VideoStreamCapture`` dan eng so'nggi JPEG kadrni oladi.
      2. Detektor (``Detector.detect``) — fail-safe.
      3. VLM verdict (``VerdictGenerator.generate``) — fail-safe.
      4. "camera.analysis" kanonik WS xabarini hub orqali yuboradi.

    Inference event loop'ni bloklamasin: detektor/VLM seam'lari async
    (``await``), lekin ketma-ket chaqiriladi (bitta GPU'ni parallel so'rovlar
    bilan to'ldirmaslik uchun — generator allaqachon shunday ishlaydi). JPEG→
    disk yozish kabi qisqa bloklovchi ishlar ``asyncio.to_thread`` da.
    """

    def __init__(
        self,
        capture: VideoStreamCapture,
        *,
        detector: Any,
        generator: Any,
        broadcast: BroadcastFn,
        lane_id: str | None,
        cadence_s: float = 2.0,
        not_implemented_exc: type[BaseException] | None = None,
    ) -> None:
        self._cap = capture
        self._detector = detector
        self._generator = generator
        self._broadcast = broadcast
        self._lane_id = lane_id
        self._cadence_s = max(0.2, float(cadence_s))
        # app.deps.ServiceNotImplemented — stub seam'larni alohida ushlash uchun.
        self._not_impl = not_implemented_exc or RuntimeError

        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self.state = StreamState(
            running=False,
            device=capture.device,
            cadence_s=self._cadence_s,
            recording=capture.recording,
            record_path=str(capture.record_path) if capture.record_path else None,
        )

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def start(self) -> None:
        if self._task is not None and not self._task.done():
            return  # idempotent
        self._stop.clear()
        self.state.running = True
        self._task = asyncio.create_task(self._run(), name="camera-continuous-analysis")
        log.info("Doimiy tahlil boshlandi (cadence=%.2fs device=%s).", self._cadence_s, self._cap.device)

    async def stop(self) -> None:
        self._stop.set()
        self.state.running = False
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, timeout=self._cadence_s + 5.0)
            except asyncio.TimeoutError:
                self._task.cancel()
                try:
                    await self._task
                except (asyncio.CancelledError, Exception):  # noqa: BLE001
                    pass
            self._task = None
        log.info("Doimiy tahlil to'xtadi.")

    # ------------------------------------------------------------------
    # Loop
    # ------------------------------------------------------------------
    async def _run(self) -> None:
        # Birinchi kadr kelguncha biroz kutamiz (kamera isigancha).
        await asyncio.to_thread(self._cap.wait_first_frame, 5.0)

        while not self._stop.is_set():
            tick = time.monotonic()
            try:
                await self._analyze_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 — bitta tsikl xatosi loop'ni yiqitmasin
                self.state.last_error = str(exc)
                log.warning("Tahlil tsiklida xato (loop davom etadi): %s", exc, exc_info=True)

            # Kadensgacha qolgan vaqtni kutamiz (tahlil vaqtini hisobga olib).
            elapsed = time.monotonic() - tick
            remaining = self._cadence_s - elapsed
            if remaining > 0:
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=remaining)
                except asyncio.TimeoutError:
                    pass

    async def _analyze_once(self) -> None:
        frame = self._cap.latest_frame()
        jpeg = self._cap.latest_jpeg()
        if frame is None or jpeg is None:
            log.debug("Hali tahlil uchun kadr yo'q — o'tkazib yuborildi.")
            return

        bgr, ts = frame
        height, width = bgr.shape[:2]
        device = self._cap.device

        # Kadrni vaqtinchalik diskka yozamiz — VLM generator full-frame'ni
        # ``file://`` StorageRef orqali o'qiydi (egress yo'q, lokal). Yozuv
        # bloklovchi, shuning uchun thread'da.
        scan_id = uuid.uuid4()
        frame_id = "cam-0"
        now = datetime.now(timezone.utc)
        frame_path = await asyncio.to_thread(self._persist_frame, scan_id, frame_id, jpeg)

        acquisition, image_frame = self._build_acquisition(
            scan_id, frame_id, jpeg, width, height, device, now, frame_path
        )

        # 1) Detektor — fail-safe.
        detection, detector_ok = await self._run_detector(acquisition, image_frame, now)
        # 2) VLM verdict — fail-safe.
        verdict, vlm_ok = await self._run_verdict(scan_id, detection, now)

        analysis_available = detector_ok  # risk band detektorga asoslanadi
        msg = self._build_message(
            device=device,
            ts=now,
            detection=detection,
            verdict=verdict,
            analysis_available=analysis_available,
            detector_ok=detector_ok,
            vlm_ok=vlm_ok,
        )

        await self._broadcast(self._lane_id, msg)

        self.state.frames_analyzed += 1
        self.state.last_analysis_ts = time.time()
        self.state.last_risk_band = msg["risk_band"]

        # Vaqtinchalik kadr faylini tozalaymiz (disk to'lib ketmasin).
        await asyncio.to_thread(self._cleanup_frame, scan_id)

    # ------------------------------------------------------------------
    # Detector / VLM (fail-safe)
    # ------------------------------------------------------------------
    async def _run_detector(self, acquisition, image_frame, now):
        """Detektorni ishlatadi. (DetectionResult, ok: bool) qaytaradi.

        ``ok=False`` => detektor ulanmagan/xato; tahlil "mavjud emas" deb
        belgilanadi (jim soxta "toza" emas).
        """
        from contracts.v1 import ModelProvenance
        from contracts.v1.detection import DetectionResult, DetectionStatus

        try:
            result = await self._detector.detect(acquisition)
            if result.scan_id == acquisition.scan_id:
                return result, True
            log.warning("detektor mos kelmagan scan_id qaytardi — natija ishonchsiz.")
        except self._not_impl:
            log.warning(
                "detektor ULANMAGAN (XRAY_DETECTOR_ENABLED=true qiling) — "
                "uzluksiz tahlil AI detektorsiz ishlamoqda."
            )
        except Exception as exc:  # noqa: BLE001 — detektor xatosi loop'ni yiqitmasin
            log.warning("detektor xatosi (%s) — tahlil mavjud emas deb belgilandi.", exc)

        failed = DetectionResult(
            scan_id=acquisition.scan_id,
            status=DetectionStatus.FAILED,
            emitted_at=now,
            model=ModelProvenance(name="no-detector", version="0.0.0", runtime="camera-stream"),
            frames=[image_frame],
            detections=[],
            error="Detektor ulanmagan yoki xato berdi — tahlil mavjud emas.",
        )
        return failed, False

    async def _run_verdict(self, scan_id, detection, now):
        """VLM verdict. (OperatorVerdict | None, ok: bool) qaytaradi."""
        from contracts.v1.verdict import Locale, VerdictRequest

        # Detektor o'zi muvaffaqiyatsiz bo'lsa, VLM uchun mazmun yo'q.
        from contracts.v1.detection import DetectionStatus
        if detection.status == DetectionStatus.FAILED:
            return None, False

        request = VerdictRequest(
            scan_id=scan_id, detection=detection, locale=Locale.UZ_LATN, emitted_at=now,
        )
        try:
            result = await self._generator.generate(request)
            if result.scan_id == scan_id:
                return result, True
            log.warning("VLM mos kelmagan scan_id qaytardi — verdict ishlatilmadi.")
        except self._not_impl:
            log.warning("VLM ULANMAGAN (XRAY_VLM_ENABLED=1 qiling) — xulosa matni mavjud emas.")
        except Exception as exc:  # noqa: BLE001 — sekin/xato VLM loop'ni yiqitmasin
            log.warning("VLM xatosi (%s) — xulosa matni mavjud emas.", exc)
        return None, False

    # ------------------------------------------------------------------
    # Message building (KANONIK format)
    # ------------------------------------------------------------------
    def _build_message(
        self,
        *,
        device: str,
        ts: datetime,
        detection,
        verdict,
        analysis_available: bool,
        detector_ok: bool,
        vlm_ok: bool,
    ) -> dict[str, Any]:
        detections_payload: list[dict[str, Any]] = []
        for d in detection.detections:
            detections_payload.append({
                "category": getattr(getattr(d, "category", None), "value", str(d.category)),
                "score": float(d.score),
                "box_x": int(d.box.x),
                "box_y": int(d.box.y),
                "box_w": int(d.box.width),
                "box_h": int(d.box.height),
            })

        if not analysis_available:
            # Fail-safe: jim soxta "toza" bermaymiz — ochiq "mavjud emas".
            risk_band = "unavailable"
            summary_uz = (
                "Tahlil mavjud emas: detektor ulanmagan yoki xato berdi. "
                "Qaror operatorga tegishli."
            )
        elif verdict is not None and vlm_ok:
            risk_band = getattr(verdict.overall_risk, "value", str(verdict.overall_risk))
            summary_uz = verdict.summary_uz
        else:
            # Detektor ishladi, VLM yo'q: risk band detektordan, matn cheklangan.
            from contracts.v1.detection import DetectionStatus
            if detection.status == DetectionStatus.COMPLETED_NO_FINDINGS:
                risk_band = "clear"
                summary_uz = "Shubhali buyum aniqlanmadi. (VLM xulosasi mavjud emas.)"
            else:
                risk_band = self._risk_from_detections(detection)
                summary_uz = (
                    f"{len(detection.detections)} ta shubhali hudud aniqlandi. "
                    "VLM xulosa matni mavjud emas — boxlarni ko'ring."
                )

        return {
            "type": "camera.analysis",
            "device": device,
            "ts": ts.isoformat(),
            "risk_band": risk_band,
            "n_detections": len(detection.detections),
            "summary_uz": summary_uz,
            "detections": detections_payload,
        }

    @staticmethod
    def _risk_from_detections(detection) -> str:
        """VLM bo'lmaganda detektorning eng yuqori balidan qo'pol risk band.

        Qaror-yordami: bu ham detektorning KALIBRLANGAN balidan keladi, VLM
        emas. Generator mavjud bo'lsa ``compute_risk_band`` ishlatiladi.
        """
        try:
            from vlm.prompts import compute_risk_band
            return getattr(compute_risk_band(detection), "value", "low")
        except Exception:  # noqa: BLE001 — fallback qo'pol chegaralar
            top = max((float(d.score) for d in detection.detections), default=0.0)
            if top >= 0.85:
                return "high"
            if top >= 0.6:
                return "medium"
            return "low"

    # ------------------------------------------------------------------
    # Acquisition / persistence helpers
    # ------------------------------------------------------------------
    def _build_acquisition(self, scan_id, frame_id, jpeg, width, height, device, now, frame_path):
        from contracts.v1 import (
            AcquisitionResult,
            ImageFrame,
            ImageModality,
            ScanSubject,
            StorageRef,
        )

        image_ref = StorageRef(
            uri=f"file://{frame_path}",
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
            lane_id=self._lane_id,
            operator_id=None,
            subject=ScanSubject.BAGGAGE,
            modality=ImageModality.SINGLE_ENERGY,
            captured_at=now,
            emitted_at=now,
            frames=[image_frame],
        )
        return acquisition, image_frame

    def _persist_frame(self, scan_id, frame_id, jpeg) -> pathlib.Path:
        d = _stream_capture_root() / str(scan_id)
        d.mkdir(parents=True, exist_ok=True)
        path = d / f"{frame_id}.jpg"
        path.write_bytes(jpeg)
        return path

    def _cleanup_frame(self, scan_id) -> None:
        import shutil
        d = _stream_capture_root() / str(scan_id)
        try:
            shutil.rmtree(d, ignore_errors=True)
        except Exception:  # noqa: BLE001
            pass


def _stream_capture_root() -> pathlib.Path:
    """Uzluksiz tahlil uchun vaqtinchalik kadrlar papkasi."""
    import os
    raw = os.environ.get("XRAY_CAPTURE_DIR", "").strip()
    base = (
        pathlib.Path(raw)
        if raw
        else pathlib.Path(__file__).resolve().parent / "captures" / "scans"
    )
    root = base / "_stream"
    root.mkdir(parents=True, exist_ok=True)
    return root


__all__ = [
    "VideoStreamCapture",
    "ContinuousAnalyzer",
    "StreamState",
    "BroadcastFn",
]
