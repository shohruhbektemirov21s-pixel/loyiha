"""Executable proof the v1 spine round-trips and the guardrails actually fire.

Run:  python -m contracts.v1._smoke
This is a smoke test, not the test suite — it asserts the load-bearing
invariants so a contract regression is loud and immediate.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from . import (
    AcquisitionResult,
    Detection,
    DetectionResult,
    DetectionStatus,
    DetectionVerdict,
    ImageFrame,
    ImageModality,
    Locale,
    ModelProvenance,
    OperatorVerdict,
    PixelBox,
    RiskBand,
    ScanSubject,
    StorageRef,
    ThreatCategory,
    VerdictRequest,
    validate_referential_integrity,
)

NOW = datetime(2026, 6, 15, 9, 0, tzinfo=timezone.utc)
SHA = "a" * 64


def _frame(fid: str) -> ImageFrame:
    return ImageFrame(
        frame_id=fid, width_px=2048, height_px=1024,
        image=StorageRef(uri=f"s3://scans/{fid}.tiff", sha256=SHA, size_bytes=10_000_000),
        view_label="high_energy",
    )


def main() -> None:
    scan_id = uuid4()
    det_id = uuid4()

    # Hop 1: Scanner -> Detector
    acq = AcquisitionResult(
        scan_id=scan_id, scanner_id="SCN-07", subject=ScanSubject.VEHICLE,
        modality=ImageModality.DUAL_ENERGY, captured_at=NOW, emitted_at=NOW,
        frames=[_frame("high"), _frame("low")],
    )
    assert acq.schema_version == "1.0"

    # Hop 2: Detector -> VLM
    det = DetectionResult(
        scan_id=scan_id, status=DetectionStatus.COMPLETED, emitted_at=NOW,
        model=ModelProvenance(name="xray-detector", version="0.3.1", weights_sha256=SHA),
        frames=[_frame("high")],
        detections=[Detection(
            detection_id=det_id, frame_id="high",
            box=PixelBox(x=100, y=120, width=300, height=200),
            native_label="organic_mass", category=ThreatCategory.NARCOTICS, score=0.82,
            attributes={"mean_density": "high"},
        )],
    )
    assert det.has_findings

    # Hop 3: VLM -> Console
    req = VerdictRequest(scan_id=scan_id, detection=det, locale=Locale.UZ_LATN, emitted_at=NOW)
    verdict = OperatorVerdict(
        verdict_id=uuid4(), scan_id=scan_id, locale=Locale.UZ_LATN,
        overall_risk=RiskBand.HIGH,
        summary_uz="Yuqori zichlikdagi organik massa aniqlandi. Tekshirish tavsiya etiladi.",
        per_detection=[DetectionVerdict(
            detection_id=det_id, category=ThreatCategory.NARCOTICS,
            rationale_uz="Zichligi yuqori, shakli notekis — narkotik moddaga o'xshash.", confidence=0.7,
        )],
        model=ModelProvenance(name="qwen3-vl", version="local-1", weights_sha256=SHA),
        generated_at=NOW,
    )
    assert verdict.decision_support_only is True
    validate_referential_integrity(req, verdict)  # must not raise

    # JSON round-trip (the actual wire form) survives intact.
    for msg in (acq, det, req, verdict):
        assert type(msg).model_validate_json(msg.model_dump_json()) == msg

    _assert_guardrails(scan_id, det, req)
    print("OK: v1 contract spine round-trips and all guardrails fire.")


def _assert_guardrails(scan_id, det, req) -> None:
    from pydantic import ValidationError

    def must_raise(fn, label):
        try:
            fn()
        except (ValidationError, ValueError):
            return
        raise AssertionError(f"guardrail did not fire: {label}")

    # 1. decision_support_only can never be False.
    must_raise(lambda: OperatorVerdict(
        verdict_id=uuid4(), scan_id=scan_id, locale=Locale.UZ_LATN, overall_risk=RiskBand.CLEAR,
        summary_uz="x", model=ModelProvenance(name="q", version="1"), generated_at=NOW,
        decision_support_only=False,
    ), "decision_support_only=False rejected")

    # 2. Box outside frame bounds rejected.
    must_raise(lambda: DetectionResult(
        scan_id=scan_id, status=DetectionStatus.COMPLETED, emitted_at=NOW,
        model=ModelProvenance(name="d", version="1"), frames=[_frame("high")],
        detections=[Detection(
            detection_id=uuid4(), frame_id="high",
            box=PixelBox(x=2000, y=0, width=500, height=10),  # x+w=2500 > 2048
            native_label="x", category=ThreatCategory.UNKNOWN, score=0.5,
        )],
    ), "box exceeding frame rejected")

    # 3. Detection referencing an unknown frame rejected.
    must_raise(lambda: DetectionResult(
        scan_id=scan_id, status=DetectionStatus.COMPLETED, emitted_at=NOW,
        model=ModelProvenance(name="d", version="1"), frames=[_frame("high")],
        detections=[Detection(
            detection_id=uuid4(), frame_id="ghost",
            box=PixelBox(x=0, y=0, width=10, height=10),
            native_label="x", category=ThreatCategory.UNKNOWN, score=0.5,
        )],
    ), "unknown frame_id rejected")

    # 4. Hallucinated detection_id in a verdict rejected.
    bad_verdict = OperatorVerdict(
        verdict_id=uuid4(), scan_id=req.scan_id, locale=Locale.UZ_LATN, overall_risk=RiskBand.HIGH,
        summary_uz="x", per_detection=[DetectionVerdict(
            detection_id=uuid4(), category=ThreatCategory.UNKNOWN, rationale_uz="x", confidence=0.5)],
        model=ModelProvenance(name="q", version="1"), generated_at=NOW,
    )
    must_raise(lambda: validate_referential_integrity(req, bad_verdict), "hallucinated detection_id rejected")

    # 5. Unknown/extra field on the wire rejected (fail-closed).
    must_raise(lambda: ModelProvenance.model_validate({"name": "q", "version": "1", "rogue": 1}),
               "extra field rejected")


if __name__ == "__main__":
    main()
