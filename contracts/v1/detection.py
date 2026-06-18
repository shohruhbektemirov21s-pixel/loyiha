"""Hop 2 contract — Detector -> VLM.

The object-detection model is the **primary** detector. It localizes and
classifies; everything it found (or that it found nothing) is expressed here.

This message is *the entire input the VLM is allowed to see* (see verdict.py,
``VerdictRequest``, which embeds it). The VLM is a verbalizer over detector
output — it is structurally impossible, in this contract, to hand the VLM a raw
scan with no detections attached. That guardrail lives in code, not in a
docstring, on purpose.
"""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import Field, model_validator

from .common import (
    SCHEMA_VERSION,
    DetectionId,
    FrameId,
    ImageFrame,
    ModelProvenance,
    PixelBox,
    ScanId,
    StorageRef,
    StrictModel,
    ThreatCategory,
    UnitInterval,
    datetime,
)


class DetectionStatus(str, Enum):
    COMPLETED = "completed"                    # ran; zero or more findings in `detections`
    COMPLETED_NO_FINDINGS = "completed_no_findings"  # ran cleanly, nothing flagged
    FAILED = "failed"                          # detector errored; `detections` is empty, see `error`


class Detection(StrictModel):
    """One localized region the detector flagged."""

    detection_id: DetectionId
    frame_id: FrameId = Field(description="Which frame in the parent result this box belongs to.")
    box: PixelBox
    native_label: str = Field(min_length=1, max_length=128, description="Detector's own class label.")
    category: ThreatCategory = Field(description="Native label normalized to the shared taxonomy.")
    score: UnitInterval = Field(description="Detector confidence for this region.")

    crop: StorageRef | None = Field(
        default=None,
        description="Cropped region image for the VLM to inspect. Recommended; avoids re-cropping downstream.",
    )
    attributes: dict[str, str] = Field(
        default_factory=dict,
        description="Optional detector hints, e.g. {'mean_density':'high','material':'organic'} from dual-energy.",
    )


class DetectionResult(StrictModel):
    """Everything the detector produced for one scan. The VLM's sole input.

    Produced by: ``POST /v1/detect``.  Consumed by: the VLM serving endpoint.
    Carries its own copy of the frame descriptors so the VLM/console need not
    re-read the acquisition message to resolve boxes or load views.
    """

    schema_version: Literal["1.0"] = SCHEMA_VERSION
    scan_id: ScanId
    status: DetectionStatus
    emitted_at: datetime = Field(description="When the detector emitted this message (timezone-aware).")
    model: ModelProvenance

    frames: list[ImageFrame] = Field(min_length=1, description="Frame descriptors (echoed from acquisition).")
    detections: list[Detection] = Field(
        default_factory=list,
        description="Localized findings. Empty iff status is COMPLETED_NO_FINDINGS or FAILED.",
    )
    error: str | None = Field(default=None, max_length=2000, description="Failure detail when status is FAILED.")

    @model_validator(mode="after")
    def _check_consistency(self) -> "DetectionResult":
        frame_ids = {f.frame_id for f in self.frames}
        if len(frame_ids) != len(self.frames):
            raise ValueError("Duplicate frame_id in frames.")

        # Referential integrity: every detection lives in a real frame and fits it.
        seen: set = set()
        for d in self.detections:
            if d.detection_id in seen:
                raise ValueError(f"Duplicate detection_id {d.detection_id}.")
            seen.add(d.detection_id)
            frame = next((f for f in self.frames if f.frame_id == d.frame_id), None)
            if frame is None:
                raise ValueError(f"Detection {d.detection_id} references unknown frame_id {d.frame_id!r}.")
            if not d.box.fits_within(frame):
                raise ValueError(f"Detection {d.detection_id} box exceeds frame {d.frame_id!r} bounds.")

        # Status / payload consistency (fail-closed).
        if self.status == DetectionStatus.FAILED and self.detections:
            raise ValueError("FAILED result must carry no detections.")
        if self.status == DetectionStatus.COMPLETED_NO_FINDINGS and self.detections:
            raise ValueError("COMPLETED_NO_FINDINGS must carry no detections.")
        if self.status == DetectionStatus.FAILED and not self.error:
            raise ValueError("FAILED result must carry an error message.")
        return self

    @property
    def has_findings(self) -> bool:
        return bool(self.detections)


__all__ = ["DetectionStatus", "Detection", "DetectionResult"]
