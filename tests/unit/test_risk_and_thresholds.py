"""Risk band ↔ detection consistency, calibrated field, threshold bounds (BO'SHLIQ-9).

Three pure concerns (no DB/GPU):

1. ``Detection.calibrated`` — the new first-class typed field. Defaults False,
   accepts True, and round-trips through DetectionResult JSON.

2. ``compute_risk_band`` — risk band must be consistent with the detections it
   is derived from: CLEAR iff no findings, HIGH for a high-risk category over
   threshold, never CLEAR when findings exist. This is the pure function the VLM
   may not override.

3. Threshold bounds — ``CategoryThreshold.decide`` zone logic, and the admin
   ``ThresholdUpdate`` schema rejecting out-of-[0,1] values at validation time
   (an immediate 422 before any DB write). The DB-level admin endpoint bounds
   test lives in tests/integration (requires_db).
"""

from __future__ import annotations

from datetime import UTC

import pytest
from pydantic import ValidationError

from contracts.v1 import RiskBand, ThreatCategory
from contracts.v1.detection import Detection, DetectionResult, DetectionStatus
from tests.fixtures.builders import make_box, make_detection, make_detection_result, make_frame

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# 1. calibrated field
# ---------------------------------------------------------------------------
class TestCalibratedField:
    def test_calibrated_defaults_false(self):
        det = make_detection()
        assert det.calibrated is False

    def test_calibrated_can_be_true(self):
        det = Detection(
            detection_id=make_detection().detection_id,
            frame_id="frame-0",
            category=ThreatCategory.FIREARM,
            score=0.9,
            box=make_box(),
            native_label="firearm",
            calibrated=True,
        )
        assert det.calibrated is True

    def test_calibrated_round_trips_through_json(self):
        frame = make_frame()
        det = Detection(
            detection_id=make_detection().detection_id,
            frame_id=frame.frame_id,
            category=ThreatCategory.NARCOTICS,
            score=0.7,
            box=make_box(),
            native_label="narcotics",
            calibrated=True,
        )
        result = DetectionResult(
            schema_version="1.0",
            scan_id=make_detection_result().scan_id,
            status=DetectionStatus.COMPLETED,
            emitted_at=make_detection_result().emitted_at,
            frames=[frame],
            detections=[det],
            model=make_detection_result().model,
        )
        dumped = result.model_dump(mode="json")
        assert dumped["detections"][0]["calibrated"] is True
        reloaded = DetectionResult.model_validate(dumped)
        assert reloaded.detections[0].calibrated is True


# ---------------------------------------------------------------------------
# 2. compute_risk_band consistency
# ---------------------------------------------------------------------------
class TestRiskBandConsistency:
    def test_no_findings_is_clear(self):
        from vlm.prompts import compute_risk_band
        det = make_detection_result(detections=[], has_findings=False)
        assert compute_risk_band(det) == RiskBand.CLEAR

    def test_high_risk_category_over_threshold_is_high(self):
        from vlm.prompts import compute_risk_band
        d = make_detection(category=ThreatCategory.FIREARM, score=0.95)
        det = make_detection_result(detections=[d])
        assert compute_risk_band(det) == RiskBand.HIGH

    def test_findings_present_never_clear(self):
        from vlm.prompts import compute_risk_band
        d = make_detection(category=ThreatCategory.CURRENCY, score=0.30)
        det = make_detection_result(detections=[d])
        assert compute_risk_band(det) != RiskBand.CLEAR

    def test_medium_score_low_severity_is_medium(self):
        from vlm.prompts import compute_risk_band
        d = make_detection(category=ThreatCategory.CURRENCY, score=0.55)
        det = make_detection_result(detections=[d])
        assert compute_risk_band(det) == RiskBand.MEDIUM

    def test_low_score_is_low(self):
        from vlm.prompts import compute_risk_band
        d = make_detection(category=ThreatCategory.CURRENCY, score=0.20)
        det = make_detection_result(detections=[d])
        assert compute_risk_band(det) == RiskBand.LOW

    def test_clear_verdict_with_findings_is_a_contract_error(self):
        # The OperatorVerdict contract refuses a CLEAR band that still lists
        # per-detection findings — risk/payload consistency, enforced structurally.
        from datetime import datetime, timezone
        from uuid import uuid4

        from contracts.v1.verdict import DetectionVerdict, Locale, OperatorVerdict
        from tests.fixtures.builders import make_detection, make_provenance

        d = make_detection()
        with pytest.raises(ValidationError):
            OperatorVerdict(
                schema_version="1.0",
                verdict_id=uuid4(),
                scan_id=uuid4(),
                locale=Locale.UZ_LATN,
                overall_risk=RiskBand.CLEAR,           # CLEAR ...
                summary_uz="Bu yerda shubhali buyum yo'q deb topildi tahlil bo'yicha.",
                per_detection=[                         # ... but findings listed
                    DetectionVerdict(
                        detection_id=d.detection_id,
                        category=d.category,
                        rationale_uz="Chapda metall buyum aniqlandi, tekshirilsin albatta.",
                        confidence=0.8,
                    )
                ],
                model=make_provenance("qwen3-vl"),
                generated_at=datetime.now(UTC),
            )


# ---------------------------------------------------------------------------
# 3. Threshold bounds + decision zones
# ---------------------------------------------------------------------------
class TestThresholdDecisionZones:
    def test_decide_alert_monitor_autoclear(self):
        from app.state.thresholds import CategoryThreshold, ThresholdDecision
        thr = CategoryThreshold("firearm", alert_threshold=0.55, auto_clear_threshold=0.20)
        assert thr.decide(0.90) == ThresholdDecision.ALERT
        assert thr.decide(0.55) == ThresholdDecision.ALERT      # boundary inclusive
        assert thr.decide(0.40) == ThresholdDecision.MONITOR
        assert thr.decide(0.20) == ThresholdDecision.MONITOR    # boundary inclusive
        assert thr.decide(0.10) == ThresholdDecision.AUTO_CLEAR


class TestThresholdUpdateSchema:
    def test_valid_thresholds_accepted(self):
        from app.api.v1.admin import ThresholdUpdate
        body = ThresholdUpdate(alert_threshold=0.6, auto_clear_threshold=0.2)
        assert body.alert_threshold == 0.6

    @pytest.mark.parametrize("alert,clear", [
        (1.5, 0.2),    # alert > 1
        (0.6, -0.1),   # clear < 0
        (2.0, 2.0),    # both out of range
    ])
    def test_out_of_range_rejected_at_schema(self, alert, clear):
        from app.api.v1.admin import ThresholdUpdate
        with pytest.raises(ValidationError):
            ThresholdUpdate(alert_threshold=alert, auto_clear_threshold=clear)
