"""Shared contract primitives for the X-ray assistant, schema v1.

Everything here is reused across the three internal hops:

    Scanner  -> Detector   (acquisition.py)
    Detector -> VLM         (detection.py)
    VLM      -> Console      (verdict.py)

Design rules enforced in this module:

* **Fail-closed wire format.** Models forbid unknown fields and are immutable
  (frozen). A payload that drifts from the contract is rejected, not guessed at.
  Producer and consumer ship together in one air-gapped deployment, so strict
  is safer than lenient.
* **Reference-by-URI for image bytes.** X-ray frames are multi-MB (dual-energy =
  several channels). Bytes never travel inline in JSON; messages carry a
  ``StorageRef`` into the local encrypted object store. Keeps the API async and
  the audit log small.
* **One correlation key.** ``scan_id`` threads every hop and every audit row.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------------
# Versioning
# ---------------------------------------------------------------------------
# The spine is versioned as a whole. A *breaking* change => new package
# `contracts/v2`, served under `/v2`, run side-by-side during migration.
# Additive, optional fields are allowed within a major version.
# `schema_version` is pinned on the wire so a consumer can hard-reject a payload
# it was not built against.
SCHEMA_VERSION = "1.0"


# ---------------------------------------------------------------------------
# Correlation identifiers
# ---------------------------------------------------------------------------
ScanId = Annotated[UUID, Field(description="Correlation key for one scan; stable across all hops + audit.")]
ScannerId = Annotated[str, Field(min_length=1, max_length=64, description="Stable hardware id of the source scanner.")]
LaneId = Annotated[str, Field(min_length=1, max_length=64)]
OperatorId = Annotated[str, Field(min_length=1, max_length=64)]
DetectionId = Annotated[UUID, Field(description="Stable id of one detected region, assigned by the detector.")]
VerdictId = Annotated[UUID, Field(description="Stable id of one VLM verdict.")]
FeedbackId = Annotated[UUID, Field(description="Stable id of one operator feedback event; the seed of a label.")]
FrameId = Annotated[str, Field(min_length=1, max_length=64, description="Identifies one image/view within a scan.")]

UnitInterval = Annotated[float, Field(ge=0.0, le=1.0, description="Confidence/score in [0, 1].")]
Sha256Hex = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$", description="Lowercase hex SHA-256.")]


class StrictModel(BaseModel):
    """Base for every contract message: strict, immutable, audit-friendly."""

    model_config = ConfigDict(
        extra="forbid",       # fail-closed on unknown/typo'd fields
        frozen=True,          # messages are immutable once built (audit integrity)
        str_strip_whitespace=True,
        validate_assignment=True,
    )


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------
class ScanSubject(str, Enum):
    VEHICLE = "vehicle"
    CARGO = "cargo"
    BAGGAGE = "baggage"
    PARCEL = "parcel"
    OTHER = "other"


class ImageModality(str, Enum):
    """How the frame was acquired. Dual-energy enables material discrimination."""

    SINGLE_ENERGY = "single_energy"
    DUAL_ENERGY = "dual_energy"
    MULTI_VIEW = "multi_view"


class ThreatCategory(str, Enum):
    """Normalized taxonomy the whole system reasons over.

    The detector emits a fine-grained *native* label (free string); the
    ingest/detector layer maps it onto exactly one of these so downstream
    layers (VLM prompt, console filters, audit analytics) share a vocabulary.
    """

    NARCOTICS = "narcotics"
    FIREARM = "firearm"
    BLADED_WEAPON = "bladed_weapon"
    EXPLOSIVE = "explosive"
    CURRENCY = "currency"
    ORGANIC_ANOMALY = "organic_anomaly"     # dense organic mass, no clean class
    METALLIC_ANOMALY = "metallic_anomaly"
    CONTRABAND_OTHER = "contraband_other"
    UNKNOWN = "unknown"


class RiskBand(str, Enum):
    """Coarse risk used for triage/sorting in the console. Advisory only."""

    CLEAR = "clear"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


# ---------------------------------------------------------------------------
# Provenance (audit)
# ---------------------------------------------------------------------------
class ModelProvenance(StrictModel):
    """Identifies the artifact that produced a hop output. Logged at every hop.

    In an air-gapped deployment we cannot rely on a model registry URL; the
    weights hash is the ground truth of *what ran*.
    """

    name: str = Field(min_length=1, max_length=128, description="e.g. 'xray-detector' or 'qwen3-vl'.")
    version: str = Field(min_length=1, max_length=64, description="Semantic or build version.")
    weights_sha256: Sha256Hex | None = Field(default=None, description="Hash of the loaded weights, if applicable.")
    runtime: str | None = Field(default=None, max_length=128, description="Serving runtime, e.g. 'vllm-0.x', 'onnxruntime'.")


# ---------------------------------------------------------------------------
# Storage references + geometry
# ---------------------------------------------------------------------------
class StorageRef(StrictModel):
    """Pointer to image bytes in the local encrypted object store.

    `uri` is opaque to the contract (e.g. ``s3://scans/<scan_id>/high.tiff`` for
    MinIO, or ``file:///var/lib/xray/...``). The SHA-256 lets any consumer
    verify integrity and lets the audit log prove which bytes were analyzed.
    """

    uri: str = Field(min_length=1, max_length=1024)
    media_type: str = Field(default="image/tiff", max_length=64)
    sha256: Sha256Hex
    size_bytes: int = Field(gt=0)


class ImageFrame(StrictModel):
    """One acquired view of a scan (e.g. high-energy channel, view_0).

    Carries both the pixel dimensions (authoritative frame of reference for all
    bounding boxes) and the bytes reference. Pixel spacing, when the scanner
    reports it, lets the VLM/console reason in real-world millimetres.
    """

    frame_id: FrameId
    width_px: int = Field(gt=0)
    height_px: int = Field(gt=0)
    image: StorageRef
    view_label: str | None = Field(default=None, max_length=64, description="e.g. 'high_energy', 'side', 'top'.")
    pixel_spacing_mm: float | None = Field(default=None, gt=0, description="mm per pixel, if known.")


class PixelBox(StrictModel):
    """Axis-aligned bounding box in **pixel** coordinates of a named frame.

    Pixels (not normalized floats) are authoritative: detectors emit pixels and
    the console renders pixels, so we avoid a lossy round-trip. Normalization is
    a pure function of the box + its frame, provided by ``normalized()``.
    """

    x: int = Field(ge=0, description="Left edge, pixels.")
    y: int = Field(ge=0, description="Top edge, pixels.")
    width: int = Field(gt=0)
    height: int = Field(gt=0)

    def fits_within(self, frame: ImageFrame) -> bool:
        return (self.x + self.width) <= frame.width_px and (self.y + self.height) <= frame.height_px

    def normalized(self, frame: ImageFrame) -> tuple[float, float, float, float]:
        """(x, y, w, h) in [0, 1], relative to the frame. Convenience for UI."""
        return (
            self.x / frame.width_px,
            self.y / frame.height_px,
            self.width / frame.width_px,
            self.height / frame.height_px,
        )


__all__ = [
    "SCHEMA_VERSION",
    "ScanId", "ScannerId", "LaneId", "OperatorId", "DetectionId", "VerdictId", "FeedbackId", "FrameId",
    "UnitInterval", "Sha256Hex",
    "StrictModel",
    "ScanSubject", "ImageModality", "ThreatCategory", "RiskBand",
    "ModelProvenance", "StorageRef", "ImageFrame", "PixelBox",
    "datetime",
]
