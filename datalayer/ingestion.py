"""Scanner image ingestion pipeline — Hop 0, first contact with classified bytes.

Raw frames arrive from the scanner bus and must be, in strict order:
  1. Validated: non-empty, sane dimensions, within size bounds.
  2. Persisted: written to ``SecureImageStore`` (encrypted, content-addressed).
  3. Packaged: wrapped into ``AcquisitionResult`` (the Hop-1 wire message).

The store is the *only* path bytes take from scanner hardware into the
system. Nothing bypasses it — a ``StorageRef`` is the proof that bytes
are already safe.

This module is deliberately dumb: no detection, no classification. Keeping
ingest dumb is what lets scanner drivers change without touching downstream
layers.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Sequence

from contracts.v1 import AcquisitionResult, ImageFrame, ImageModality, ScanSubject
from contracts.v1.common import StorageRef
from datalayer.storage import SecureImageStore

log = logging.getLogger("xray.datalayer.ingestion")

# ---------------------------------------------------------------------------
# Hard size bounds (enforced structurally, not by convention)
# ---------------------------------------------------------------------------
_MIN_SCAN_BYTES: int = 1_024           # 1 KiB — real X-ray frames are larger
_MAX_SCAN_BYTES: int = 200 * 1024**2   # 200 MiB — generous upper bound for dual-energy TIFF


class IngestValidationError(ValueError):
    """A raw frame failed pre-storage validation. Nothing was persisted."""


# ---------------------------------------------------------------------------
# Data carriers
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class RawFrame:
    """One physical view delivered by the scanner bus, before any processing.

    Immutable carrier — the pipeline never mutates incoming bytes.
    """

    raw_bytes: bytes
    frame_label: str                    # e.g. "high_energy", "low_energy", "side"
    width_px: int
    height_px: int
    media_type: str = "image/tiff"
    pixel_spacing_mm: float | None = None


@dataclass(frozen=True)
class IngestConfig:
    """Static configuration for one scanner lane. Injected at startup."""

    scanner_id: str
    lane_id: str
    modality: ImageModality
    subject: ScanSubject
    operator_id: str | None = None      # operator on shift; audit only at this hop


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------
class IngestPipeline:
    """Validates, stores, and packages one scan into an ``AcquisitionResult``.

    ``SecureImageStore`` is injected — the encrypted-at-rest guarantee is a
    structural consequence of construction, not a caller responsibility.
    """

    def __init__(self, store: SecureImageStore, config: IngestConfig) -> None:
        self._store = store
        self._cfg = config

    # -- validation --------------------------------------------------------
    def _validate_frame(self, frame: RawFrame) -> None:
        n = len(frame.raw_bytes)
        if n < _MIN_SCAN_BYTES:
            raise IngestValidationError(
                f"Frame {frame.frame_label!r} too small: {n} B < {_MIN_SCAN_BYTES} B minimum."
            )
        if n > _MAX_SCAN_BYTES:
            raise IngestValidationError(
                f"Frame {frame.frame_label!r} too large: {n} B > {_MAX_SCAN_BYTES} B maximum."
            )
        if frame.width_px <= 0 or frame.height_px <= 0:
            raise IngestValidationError(
                f"Frame {frame.frame_label!r} has invalid dimensions "
                f"{frame.width_px}x{frame.height_px} px."
            )

    # -- ingest ------------------------------------------------------------
    def ingest(
        self,
        frames: Sequence[RawFrame],
        *,
        captured_at: datetime | None = None,
        notes: str | None = None,
    ) -> AcquisitionResult:
        """Validate all frames, persist each, and return a wired ``AcquisitionResult``.

        Raises ``IngestValidationError`` if any frame fails — nothing is
        persisted until all frames pass (the store is idempotent, so retries
        are safe if a partial run somehow occurred).
        """
        if not frames:
            raise IngestValidationError("At least one frame is required to form a scan.")

        # Validate first — no partial persists.
        for f in frames:
            self._validate_frame(f)

        scan_id = uuid.uuid4()
        now = datetime.now(timezone.utc)
        captured_at = captured_at or now

        image_frames: list[ImageFrame] = []
        for i, raw in enumerate(frames):
            ref: StorageRef = self._store.put(raw.raw_bytes, media_type=raw.media_type)
            frame_id = f"{scan_id.hex[:8]}_{i:02d}_{raw.frame_label}"
            image_frames.append(
                ImageFrame(
                    frame_id=frame_id,
                    width_px=raw.width_px,
                    height_px=raw.height_px,
                    image=ref,
                    view_label=raw.frame_label,
                    pixel_spacing_mm=raw.pixel_spacing_mm,
                )
            )
            log.info(
                "ingested frame=%s sha256=%s... size=%dB",
                frame_id,
                ref.sha256[:12],
                ref.size_bytes,
            )

        result = AcquisitionResult(
            scan_id=scan_id,
            scanner_id=self._cfg.scanner_id,
            lane_id=self._cfg.lane_id,
            modality=self._cfg.modality,
            subject=self._cfg.subject,
            frames=image_frames,
            captured_at=captured_at,
            emitted_at=now,
            operator_id=self._cfg.operator_id,
            notes=notes,
        )
        log.info(
            "scan_id=%s ingested scanner=%s frames=%d",
            scan_id,
            self._cfg.scanner_id,
            len(image_frames),
        )
        return result


__all__ = [
    "RawFrame",
    "IngestConfig",
    "IngestValidationError",
    "IngestPipeline",
]
