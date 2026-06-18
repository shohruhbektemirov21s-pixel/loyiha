"""``WeaponsDetector`` — the Hop-2 seam implementation (``app.deps.Detector``).

This is the bridge between a dumb inference backend (pixels -> RawDetection) and
the strict ``DetectionResult`` contract. Everything that makes detector output
*trustworthy to downstream layers* lives here, on purpose:

* **Box legality.** Model boxes are floats that can spill past the image edge
  (especially after letterbox/NMS). The contract rejects a box that doesn't
  ``fits_within`` its frame — so we clamp to the frame descriptor (the
  authoritative geometry) and drop anything degenerate. A box that can't be
  made legal is dropped, never emitted malformed.
* **Calibrated confidence.** The raw model score is replaced by a calibrated
  probability so the threshold the *downstream* FastAPI layer applies actually
  means what it says (see eval/calibration.py). The raw score is preserved in
  ``attributes`` for audit.
* **Recall-first thresholding.** Per-category operating points, low for weapons.
  Filtering happens here (after calibration), not in the model.
* **Fail-closed status.** Any backend/loader exception yields a ``FAILED``
  result with the error text and *zero* detections — the contract forbids a
  FAILED result that smuggles findings, and we never degrade an error into a
  misleading 'no findings'. The operator must be told the eye didn't see, not
  shown a clean scan.

The adapter holds no torch/cv2 imports; it depends only on the Protocols in
predictor.py, so it is fully unit-testable with fakes.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Callable, Protocol, runtime_checkable
from uuid import uuid4

from contracts.v1 import (
    AcquisitionResult,
    Detection,
    DetectionResult,
    DetectionStatus,
    ImageFrame,
    ModelProvenance,
    PixelBox,
    ThreatCategory,
)

from detector.taxonomy import to_category
from detector.serving.predictor import FrameLoader, Predictor, RawDetection

log = logging.getLogger("xray.detector.adapter")


@runtime_checkable
class Calibrator(Protocol):
    """Maps a raw model score to a calibrated probability for a native label."""

    def calibrate(self, native_label: str, raw_score: float) -> float: ...


class IdentityCalibrator:
    """No-op calibrator. Honest default: until a calibration set is fit, we do
    not *claim* the score is calibrated — but we also don't distort it."""

    def calibrate(self, native_label: str, raw_score: float) -> float:
        return raw_score


# Recall-first operating points, applied AFTER calibration. Weapons sit low: we
# would rather hand the VLM a borderline knife to verify than miss it. Tune each
# value to a measured recall target on held-out target-scanner data — these are
# starting points, not final.
DEFAULT_THRESHOLDS: dict[ThreatCategory, float] = {
    ThreatCategory.FIREARM: 0.20,
    ThreatCategory.BLADED_WEAPON: 0.20,
    ThreatCategory.METALLIC_ANOMALY: 0.35,
    ThreatCategory.UNKNOWN: 0.40,
}
_FALLBACK_THRESHOLD = 0.30


class WeaponsDetector:
    """Concrete ``Detector``. Wire it in via ``app.dependency_overrides``."""

    def __init__(
        self,
        *,
        predictor: Predictor,
        loader: FrameLoader,
        provenance: ModelProvenance,
        calibrator: Calibrator | None = None,
        thresholds: dict[ThreatCategory, float] | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._predictor = predictor
        self._loader = loader
        self._provenance = provenance
        self._calibrator = calibrator or IdentityCalibrator()
        self._thresholds = thresholds or dict(DEFAULT_THRESHOLDS)
        # Injectable clock keeps emitted_at deterministic under test.
        self._now = clock or (lambda: datetime.now(timezone.utc))

    # -- Detector protocol ---------------------------------------------------
    async def detect(self, acquisition: AcquisitionResult) -> DetectionResult:
        try:
            detections = await asyncio.to_thread(self._detect_sync, acquisition)
        except Exception as exc:  # noqa: BLE001 — fail-closed is the whole point
            log.exception("detection failed for scan %s", acquisition.scan_id)
            return DetectionResult(
                scan_id=acquisition.scan_id,
                status=DetectionStatus.FAILED,
                emitted_at=self._now(),
                model=self._provenance,
                frames=list(acquisition.frames),
                detections=[],
                error=f"{type(exc).__name__}: {exc}"[:2000],
            )

        status = (
            DetectionStatus.COMPLETED
            if detections
            else DetectionStatus.COMPLETED_NO_FINDINGS
        )
        return DetectionResult(
            scan_id=acquisition.scan_id,
            status=status,
            emitted_at=self._now(),
            model=self._provenance,
            frames=list(acquisition.frames),
            detections=detections,
        )

    # -- internals (blocking; run in a worker thread) ------------------------
    def _detect_sync(self, acquisition: AcquisitionResult) -> list[Detection]:
        out: list[Detection] = []
        for frame in acquisition.frames:
            image = self._loader.load(frame.image)
            for raw in self._predictor.predict_frame(image):
                det = self._to_detection(raw, frame)
                if det is not None:
                    out.append(det)
        return out

    def _to_detection(self, raw: RawDetection, frame: ImageFrame) -> Detection | None:
        category = to_category(raw.native_label)
        score = self._calibrator.calibrate(raw.native_label, raw.score)

        threshold = self._thresholds.get(category, _FALLBACK_THRESHOLD)
        if score < threshold:
            return None

        box = self._legal_box(raw, frame)
        if box is None:
            log.debug("dropped degenerate/out-of-frame box %s on frame %s",
                      raw, frame.frame_id)
            return None

        attributes = dict(raw.attributes)
        attributes["raw_score"] = f"{raw.score:.4f}"
        attributes["calibrated"] = "true" if not isinstance(
            self._calibrator, IdentityCalibrator) else "false"

        return Detection(
            detection_id=uuid4(),
            frame_id=frame.frame_id,
            box=box,
            native_label=raw.native_label[:128],
            category=category,
            score=_clip_unit(score),
            attributes=attributes,
        )

    @staticmethod
    def _legal_box(raw: RawDetection, frame: ImageFrame) -> PixelBox | None:
        """Quantize + clamp a float xyxy box to a contract-legal PixelBox.

        Returns ``None`` if nothing legal survives (entirely out of frame, or
        collapses to zero area). Clamping to ``frame`` — not to the loaded
        image — keeps us aligned with the descriptor the contract validates and
        the console renders against.
        """
        x1 = max(0, int(round(min(raw.x1, raw.x2))))
        y1 = max(0, int(round(min(raw.y1, raw.y2))))
        x2 = min(frame.width_px, int(round(max(raw.x1, raw.x2))))
        y2 = min(frame.height_px, int(round(max(raw.y1, raw.y2))))
        w = x2 - x1
        h = y2 - y1
        if w <= 0 or h <= 0 or x1 >= frame.width_px or y1 >= frame.height_px:
            return None
        return PixelBox(x=x1, y=y1, width=w, height=h)


def _clip_unit(v: float) -> float:
    return 0.0 if v < 0.0 else 1.0 if v > 1.0 else v


__all__ = ["WeaponsDetector", "Calibrator", "IdentityCalibrator", "DEFAULT_THRESHOLDS"]
