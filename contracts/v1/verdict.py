"""Hop 3 contract — VLM -> Operator console.

Two messages:

* ``VerdictRequest`` — the VLM serving input. It **embeds** the full
  ``DetectionResult``. There is no field for "raw image only"; the only way to
  invoke the VLM is to hand it the detector's findings. This is the structural
  expression of the rule *the VLM never acts as a detector*.

* ``OperatorVerdict`` — the VLM serving output. A plain-Uzbek, decision-support
  summary the console renders next to the highlighted boxes. Every verdict is
  permanently stamped ``decision_support_only = True``: the operator decides.
"""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import Field, model_validator

from .common import (
    SCHEMA_VERSION,
    DetectionId,
    ModelProvenance,
    RiskBand,
    ScanId,
    StrictModel,
    ThreatCategory,
    UnitInterval,
    VerdictId,
    datetime,
)
from .detection import DetectionResult


class Locale(str, Enum):
    UZ_LATN = "uz-Latn"   # Uzbek, Latin script (default operator language)
    UZ_CYRL = "uz-Cyrl"   # Uzbek, Cyrillic script
    RU = "ru"             # fallback / mixed crews


class VerdictRequest(StrictModel):
    """Input to the VLM. Embeds the detector output — the VLM's only knowledge.

    Produced by: the orchestrator.  Consumed by: ``POST /v1/verdict``.
    """

    schema_version: Literal["1.0"] = SCHEMA_VERSION
    scan_id: ScanId
    detection: DetectionResult = Field(description="The detector's findings. The VLM reasons only over this.")
    locale: Locale = Field(default=Locale.UZ_LATN, description="Language/script for the generated verdict.")
    emitted_at: datetime

    @model_validator(mode="after")
    def _scan_ids_match(self) -> "VerdictRequest":
        if self.detection.scan_id != self.scan_id:
            raise ValueError("VerdictRequest.scan_id must equal the embedded detection.scan_id.")
        return self


class DetectionVerdict(StrictModel):
    """The VLM's plain-language read of one detection, keyed back to its id."""

    detection_id: DetectionId = Field(description="Must reference a detection in the originating request.")
    category: ThreatCategory
    rationale_uz: str = Field(min_length=1, max_length=2000, description="Why this region was flagged, in the operator's language.")
    confidence: UnitInterval = Field(description="VLM's confidence in its own description (NOT a detection score).")


class OperatorVerdict(StrictModel):
    """The decision-support output rendered in the console. Operator decides.

    Produced by: ``POST /v1/verdict``.  Consumed by: the operator console.
    """

    schema_version: Literal["1.0"] = SCHEMA_VERSION
    verdict_id: VerdictId
    scan_id: ScanId
    locale: Locale

    overall_risk: RiskBand = Field(description="Coarse triage band. Advisory; for sorting the queue.")
    summary_uz: str = Field(min_length=1, max_length=4000, description="Plain-Uzbek summary shown to the operator.")
    per_detection: list[DetectionVerdict] = Field(
        default_factory=list,
        description="One entry per detection the VLM commented on. Empty when the scan is CLEAR.",
    )

    model: ModelProvenance
    generated_at: datetime

    # Hard, non-overridable provenance: this output is never an enforcement
    # decision. The literal type makes any other value a validation error.
    decision_support_only: Literal[True] = True

    @model_validator(mode="after")
    def _risk_payload_consistency(self) -> "OperatorVerdict":
        if self.overall_risk == RiskBand.CLEAR and self.per_detection:
            raise ValueError("A CLEAR verdict must have no per-detection findings.")
        ids = [d.detection_id for d in self.per_detection]
        if len(ids) != len(set(ids)):
            raise ValueError("Duplicate detection_id in per_detection.")
        return self


def validate_referential_integrity(request: VerdictRequest, verdict: OperatorVerdict) -> None:
    """Orchestrator-side guard: a verdict may only reference detections it was given.

    The VLM cannot invent a detection. Call this before persisting/serving a
    verdict; it raises if the model hallucinated an id or crossed scans.
    """
    if verdict.scan_id != request.scan_id:
        raise ValueError("Verdict scan_id does not match request scan_id.")
    known = {d.detection_id for d in request.detection.detections}
    for dv in verdict.per_detection:
        if dv.detection_id not in known:
            raise ValueError(f"Verdict references unknown detection_id {dv.detection_id} (hallucinated).")


__all__ = ["Locale", "VerdictRequest", "DetectionVerdict", "OperatorVerdict", "validate_referential_integrity"]
