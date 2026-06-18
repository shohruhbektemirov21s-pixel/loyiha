"""Unit tests for OperatorVerdict contract invariants.

Critical invariants:
    1. decision_support_only is always True — structurally, not by convention.
    2. A CLEAR verdict never carries per-detection findings.
    3. Verdict can only reference detection IDs that exist in the request.
    4. summary_uz must not be empty on any verdict.
    5. VLM cannot hallucinate detection IDs (validate_referential_integrity).
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from pydantic import ValidationError

from contracts.v1.common import RiskBand, ThreatCategory
from contracts.v1.verdict import (
    DetectionVerdict,
    OperatorVerdict,
    Locale,
    validate_referential_integrity,
)
from tests.fixtures.builders import (
    make_clear_verdict,
    make_detection,
    make_detection_result,
    make_detection_verdict,
    make_operator_verdict,
    make_provenance,
    make_verdict_request,
)


class TestDecisionSupportOnly:
    """decision_support_only MUST always be True — this is the structural
    guarantee that the system never makes an enforcement decision."""

    def test_decision_support_only_is_true_on_standard_verdict(self):
        v = make_operator_verdict()
        assert v.decision_support_only is True

    def test_decision_support_only_is_true_on_clear_verdict(self):
        v = make_clear_verdict()
        assert v.decision_support_only is True

    def test_cannot_set_decision_support_only_to_false(self):
        """The field is typed Literal[True] — any other value is a contract error."""
        det = make_detection_result()
        with pytest.raises(ValidationError) as exc_info:
            OperatorVerdict(
                schema_version="1.0",
                verdict_id=uuid4(),
                scan_id=det.scan_id,
                locale=Locale.UZ_LATN,
                overall_risk=RiskBand.HIGH,
                summary_uz="Test.",
                per_detection=[],
                model=make_provenance("qwen3-vl"),
                generated_at=__import__("datetime").datetime(2025, 1, 1, tzinfo=__import__("datetime").timezone.utc),
                decision_support_only=False,   # MUST be rejected
            )
        assert "decision_support_only" in str(exc_info.value).lower() or "literal" in str(exc_info.value).lower()


class TestClearVerdictInvariant:
    """CLEAR verdict ↔ empty per_detection is a bidirectional invariant."""

    def test_clear_verdict_has_no_per_detection(self):
        v = make_clear_verdict()
        assert v.overall_risk == RiskBand.CLEAR
        assert v.per_detection == []

    def test_clear_verdict_with_findings_is_rejected(self):
        det = make_detection_result()
        with pytest.raises(ValidationError):
            OperatorVerdict(
                schema_version="1.0",
                verdict_id=uuid4(),
                scan_id=det.scan_id,
                locale=Locale.UZ_LATN,
                overall_risk=RiskBand.CLEAR,
                summary_uz="Clear scan.",
                per_detection=[make_detection_verdict(det.detections[0])],   # forbidden
                model=make_provenance("qwen3-vl"),
                generated_at=__import__("datetime").datetime(2025, 1, 1, tzinfo=__import__("datetime").timezone.utc),
            )

    def test_non_clear_verdict_can_have_per_detection(self):
        det = make_detection_result()
        v   = make_operator_verdict(det, risk=RiskBand.HIGH)
        assert v.overall_risk == RiskBand.HIGH
        assert len(v.per_detection) == len(det.detections)


class TestReferentialIntegrity:
    """validate_referential_integrity must catch hallucinated detection IDs."""

    def test_valid_verdict_passes_integrity_check(self):
        det     = make_detection_result()
        request = make_verdict_request(det)
        verdict = make_operator_verdict(det)
        validate_referential_integrity(request, verdict)   # must not raise

    def test_hallucinated_detection_id_is_caught(self):
        det     = make_detection_result()
        request = make_verdict_request(det)

        # Replace a detection_id with a hallucinated one
        dv = DetectionVerdict(
            detection_id=uuid4(),    # fabricated id
            category=ThreatCategory.FIREARM,
            rationale_uz="Some rationale.",
            confidence=0.8,
        )
        verdict = OperatorVerdict(
            schema_version="1.0",
            verdict_id=uuid4(),
            scan_id=det.scan_id,
            locale=Locale.UZ_LATN,
            overall_risk=RiskBand.HIGH,
            summary_uz="Xavfli.",
            per_detection=[dv],
            model=make_provenance("qwen3-vl"),
            generated_at=__import__("datetime").datetime(2025, 1, 1, tzinfo=__import__("datetime").timezone.utc),
        )

        with pytest.raises(ValueError, match="hallucinated"):
            validate_referential_integrity(request, verdict)

    def test_mismatched_scan_id_is_caught(self):
        det1 = make_detection_result()
        det2 = make_detection_result()  # different scan_id
        req  = make_verdict_request(det1)
        v    = make_operator_verdict(det1)

        # Build a verdict that references det1's scan but against det2's request
        req2 = make_verdict_request(det2)
        with pytest.raises(ValueError, match="scan_id"):
            validate_referential_integrity(req2, v)


class TestVerdictDeduplication:
    """No duplicate detection_ids in per_detection."""

    def test_duplicate_detection_id_rejected(self):
        det = make_detection_result()
        dv  = make_detection_verdict(det.detections[0])
        with pytest.raises(ValidationError):
            OperatorVerdict(
                schema_version="1.0",
                verdict_id=uuid4(),
                scan_id=det.scan_id,
                locale=Locale.UZ_LATN,
                overall_risk=RiskBand.HIGH,
                summary_uz="Duplicate.",
                per_detection=[dv, dv],   # same detection_id twice
                model=make_provenance("qwen3-vl"),
                generated_at=__import__("datetime").datetime(2025, 1, 1, tzinfo=__import__("datetime").timezone.utc),
            )


class TestSummaryField:
    """summary_uz must never be blank."""

    def test_empty_summary_uz_rejected(self):
        det = make_detection_result()
        with pytest.raises(ValidationError):
            OperatorVerdict(
                schema_version="1.0",
                verdict_id=uuid4(),
                scan_id=det.scan_id,
                locale=Locale.UZ_LATN,
                overall_risk=RiskBand.HIGH,
                summary_uz="",    # empty
                per_detection=[],
                model=make_provenance("qwen3-vl"),
                generated_at=__import__("datetime").datetime(2025, 1, 1, tzinfo=__import__("datetime").timezone.utc),
            )
