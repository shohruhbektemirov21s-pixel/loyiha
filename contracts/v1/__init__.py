"""X-ray assistant contract spine, schema v1.

The versioned, single source of truth for the Pydantic messages exchanged
between the four layers. Import from here, not from submodules:

    from contracts.v1 import AcquisitionResult, DetectionResult, VerdictRequest, OperatorVerdict

Hop map:
    Scanner  -> Detector : AcquisitionResult
    Detector -> VLM      : DetectionResult   (the VLM's *only* input)
    VLM      -> Console  : OperatorVerdict    (decision-support only)
"""

from .common import (
    SCHEMA_VERSION,
    ImageFrame,
    ImageModality,
    ModelProvenance,
    PixelBox,
    RiskBand,
    ScanSubject,
    StorageRef,
    ThreatCategory,
)
from .acquisition import AcquisitionResult
from .detection import Detection, DetectionResult, DetectionStatus
from .verdict import (
    DetectionVerdict,
    Locale,
    OperatorVerdict,
    VerdictRequest,
    validate_referential_integrity,
)
from .feedback import (
    DetectionJudgement,
    DetectionReview,
    FeedbackReceipt,
    OperatorAnnotation,
    OperatorFeedback,
    OperatorOutcome,
)

__all__ = [
    "SCHEMA_VERSION",
    # common
    "ImageFrame", "ImageModality", "ModelProvenance", "PixelBox", "RiskBand",
    "ScanSubject", "StorageRef", "ThreatCategory",
    # hop 1
    "AcquisitionResult",
    # hop 2
    "Detection", "DetectionResult", "DetectionStatus",
    # hop 3
    "DetectionVerdict", "Locale", "OperatorVerdict", "VerdictRequest",
    "validate_referential_integrity",
    # hop 4
    "DetectionJudgement", "DetectionReview", "FeedbackReceipt",
    "OperatorAnnotation", "OperatorFeedback", "OperatorOutcome",
]
