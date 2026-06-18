"""Hop 1 contract — Scanner/ingest -> Detector.

The acquisition layer pulls bytes off a scanner, writes them to the encrypted
store, and emits an ``AcquisitionResult``: "here is scan <id>, these are its
frames, this is the context". It performs **no** analysis — it never localizes
or classifies. That is the detector's job, and keeping ingest dumb is what lets
us swap scanner drivers without touching downstream layers.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from .common import (
    SCHEMA_VERSION,
    ImageFrame,
    ImageModality,
    LaneId,
    OperatorId,
    ScanId,
    ScannerId,
    ScanSubject,
    StrictModel,
    datetime,
)


class AcquisitionResult(StrictModel):
    """A scan, captured and persisted, ready for detection.

    Consumed by: the detector serving endpoint (``POST /v1/detect``).
    """

    schema_version: Literal["1.0"] = SCHEMA_VERSION
    scan_id: ScanId
    scanner_id: ScannerId
    lane_id: LaneId | None = None
    operator_id: OperatorId | None = Field(
        default=None,
        description="Operator on shift at capture time. Audit only — not the decision-maker for this scan.",
    )

    subject: ScanSubject
    modality: ImageModality
    captured_at: datetime = Field(description="When the scanner produced the bytes (timezone-aware).")
    emitted_at: datetime = Field(description="When ingest emitted this message (timezone-aware).")

    frames: list[ImageFrame] = Field(
        min_length=1,
        description="One or more views/channels. Dual-energy scans carry >1 frame.",
    )

    notes: str | None = Field(default=None, max_length=2000, description="Free-text ingest context, if any.")

    def frame_ids(self) -> set[str]:
        return {f.frame_id for f in self.frames}


__all__ = ["AcquisitionResult"]
