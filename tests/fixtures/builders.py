"""Test data factories — build valid contract objects with sensible defaults.

Every factory returns a fully-valid, Pydantic-validated object so tests that
are not about validation don't drown in boilerplate.  Override any field via
keyword arguments.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from uuid import uuid4

from contracts.v1.acquisition import AcquisitionResult
from contracts.v1.common import (
    ImageFrame,
    ImageModality,
    ModelProvenance,
    PixelBox,
    RiskBand,
    ScanSubject,
    StorageRef,
    ThreatCategory,
)
from contracts.v1.detection import Detection, DetectionResult, DetectionStatus
from contracts.v1.feedback import (
    DetectionJudgement,
    DetectionReview,
    OperatorAnnotation,
    OperatorFeedback,
    OperatorOutcome,
)
from contracts.v1.verdict import (
    DetectionVerdict,
    Locale,
    OperatorVerdict,
    VerdictRequest,
)

_NOW = datetime(2025, 1, 15, 10, 0, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Primitives
# ---------------------------------------------------------------------------

def make_storage_ref(**kw) -> StorageRef:
    dummy_sha = hashlib.sha256(b"test-image-bytes").hexdigest()
    return StorageRef(
        uri=kw.get("uri", "file:///var/lib/xray/test/frame.tiff"),
        sha256=kw.get("sha256", dummy_sha),
        size_bytes=kw.get("size_bytes", 1024 * 512),
    )


def make_frame(frame_id: str = "frame-0", **kw) -> ImageFrame:
    return ImageFrame(
        frame_id=frame_id,
        width_px=kw.get("width_px", 1024),
        height_px=kw.get("height_px", 768),
        image=kw.get("image", make_storage_ref()),
        view_label=kw.get("view_label", "high_energy"),
    )


def make_box(**kw) -> PixelBox:
    return PixelBox(
        x=kw.get("x", 100),
        y=kw.get("y", 100),
        width=kw.get("width", 80),
        height=kw.get("height", 60),
    )


def make_provenance(name: str = "xray-detector", **kw) -> ModelProvenance:
    return ModelProvenance(
        name=name,
        version=kw.get("version", "1.0.0"),
        weights_sha256=kw.get("weights_sha256", "a" * 64),
        runtime=kw.get("runtime", "onnxruntime-1.18"),
    )


# ---------------------------------------------------------------------------
# Detection layer
# ---------------------------------------------------------------------------

def make_detection(
    category: ThreatCategory = ThreatCategory.FIREARM,
    score: float = 0.92,
    frame_id: str = "frame-0",
    **kw,
) -> Detection:
    return Detection(
        detection_id=kw.get("detection_id", uuid4()),
        frame_id=frame_id,
        category=category,
        score=score,
        box=kw.get("box", make_box()),
        native_label=kw.get("native_label", category.value),
        attributes=kw.get("attributes", {}),
    )


def make_detection_result(
    scan_id=None,
    detections: list[Detection] | None = None,
    has_findings: bool = True,
    **kw,
) -> DetectionResult:
    sid   = scan_id or uuid4()
    frame = make_frame()
    dets  = detections if detections is not None else (
        [make_detection()] if has_findings else []
    )
    status = (
        DetectionStatus.COMPLETED
        if dets
        else DetectionStatus.COMPLETED_NO_FINDINGS
    )
    return DetectionResult(
        schema_version="1.0",
        scan_id=sid,
        status=kw.get("status", status),
        emitted_at=kw.get("emitted_at", _NOW),
        frames=[kw.get("frame", frame)],
        detections=dets,
        model=kw.get("model", make_provenance()),
        error=kw.get("error", None),
    )


# ---------------------------------------------------------------------------
# Verdict layer
# ---------------------------------------------------------------------------

def make_verdict_request(detection_result: DetectionResult | None = None, **kw) -> VerdictRequest:
    det = detection_result or make_detection_result()
    return VerdictRequest(
        schema_version="1.0",
        scan_id=det.scan_id,
        detection=det,
        locale=kw.get("locale", Locale.UZ_LATN),
        emitted_at=kw.get("emitted_at", _NOW),
    )


def make_detection_verdict(detection: Detection, **kw) -> DetectionVerdict:
    return DetectionVerdict(
        detection_id=detection.detection_id,
        category=detection.category,
        rationale_uz=kw.get(
            "rationale_uz",
            "Chapda metall predmet aniqlandi. Operator tekshirishi tavsiya etiladi.",
        ),
        confidence=kw.get("confidence", 0.88),
    )


def make_operator_verdict(
    detection_result: DetectionResult | None = None,
    risk: RiskBand = RiskBand.HIGH,
    **kw,
) -> OperatorVerdict:
    det = detection_result or make_detection_result()
    per_det = (
        [make_detection_verdict(d) for d in det.detections]
        if det.detections and risk != RiskBand.CLEAR
        else []
    )
    return OperatorVerdict(
        schema_version="1.0",
        verdict_id=kw.get("verdict_id", uuid4()),
        scan_id=det.scan_id,
        locale=kw.get("locale", Locale.UZ_LATN),
        overall_risk=risk,
        summary_uz=kw.get(
            "summary_uz",
            "Xavfli buyum gumon qilinmoqda. Operator qarorini kutish talab etiladi.",
        ),
        per_detection=per_det,
        model=kw.get("model", make_provenance("qwen3-vl")),
        generated_at=kw.get("generated_at", _NOW),
    )


def make_clear_verdict(scan_id=None, **kw) -> OperatorVerdict:
    sid = scan_id or uuid4()
    return OperatorVerdict(
        schema_version="1.0",
        verdict_id=uuid4(),
        scan_id=sid,
        locale=Locale.UZ_LATN,
        overall_risk=RiskBand.CLEAR,
        summary_uz=kw.get(
            "summary_uz",
            "Avtomatik tahlil shubhali buyum aniqlamadi. "
            "Xulosa faqat yordam uchun — yakuniy qaror operatorga tegishli.",
        ),
        per_detection=[],
        model=make_provenance("qwen3-vl"),
        generated_at=_NOW,
    )


# ---------------------------------------------------------------------------
# Feedback layer
# ---------------------------------------------------------------------------

def make_operator_feedback(
    detection_result: DetectionResult | None = None,
    outcome: OperatorOutcome = OperatorOutcome.INSPECTED,
    **kw,
) -> OperatorFeedback:
    det = detection_result or make_detection_result()
    reviews = kw.get("reviews", [
        DetectionReview(
            detection_id=d.detection_id,
            judgement=DetectionJudgement.CONFIRMED,
        )
        for d in det.detections
    ])
    return OperatorFeedback(
        schema_version="1.0",
        feedback_id=kw.get("feedback_id", uuid4()),
        scan_id=det.scan_id,
        verdict_id=kw.get("verdict_id", uuid4()),
        operator_id=kw.get("operator_id", "op-001"),
        detection=det,
        outcome=outcome,
        reviews=reviews,
        missed=kw.get("missed", []),
        decided_at=kw.get("decided_at", _NOW),
        emitted_at=kw.get("emitted_at", _NOW),
        notes_uz=kw.get("notes_uz", None),
    )


def make_missed_annotation(frame: ImageFrame, **kw) -> OperatorAnnotation:
    return OperatorAnnotation(
        frame_id=frame.frame_id,
        box=kw.get("box", PixelBox(x=200, y=200, width=50, height=50)),
        category=kw.get("category", ThreatCategory.NARCOTICS),
        note_uz=kw.get("note_uz", "Operator tomonidan aniqlangan."),
    )
