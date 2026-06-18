"""Unit tests for OperatorFeedback contract invariants.

Critical invariants:
    1. Reviews can only target detection IDs that exist in the embedded result.
    2. No duplicate reviews for the same detection_id.
    3. RECLASSIFIED review requires corrected_category; no other judgement may carry one.
    4. Missed-region boxes must fit within the named frame.
    5. Missed-region frames must exist in the embedded detection result.
    6. operator_id is required — every label must be attributable.
    7. scan_id must match the embedded detection's scan_id.
    8. n_gold_labels and n_hard_negatives counts are correct.
    9. is_false_negative_report is True iff missed is non-empty.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest
from pydantic import ValidationError

from contracts.v1.common import PixelBox, ThreatCategory
from contracts.v1.feedback import (
    DetectionJudgement,
    DetectionReview,
    OperatorAnnotation,
    OperatorFeedback,
    OperatorOutcome,
)
from tests.fixtures.builders import (
    make_detection,
    make_detection_result,
    make_frame,
    make_missed_annotation,
    make_operator_feedback,
)

_NOW = datetime(2025, 1, 15, 10, 0, 0, tzinfo=timezone.utc)


class TestReviewReferentialIntegrity:
    def test_valid_review_accepted(self):
        det = make_detection_result()
        fb  = make_operator_feedback(det)
        assert len(fb.reviews) == len(det.detections)

    def test_review_targeting_unknown_detection_id_rejected(self):
        det = make_detection_result()
        bad_review = DetectionReview(
            detection_id=uuid4(),       # not in det.detections
            judgement=DetectionJudgement.CONFIRMED,
        )
        with pytest.raises(ValidationError, match="unknown detection_id"):
            OperatorFeedback(
                schema_version="1.0",
                feedback_id=uuid4(),
                scan_id=det.scan_id,
                operator_id="op-001",
                detection=det,
                outcome=OperatorOutcome.INSPECTED,
                reviews=[bad_review],
                decided_at=_NOW,
                emitted_at=_NOW,
            )

    def test_duplicate_review_rejected(self):
        det    = make_detection_result()
        review = DetectionReview(
            detection_id=det.detections[0].detection_id,
            judgement=DetectionJudgement.CONFIRMED,
        )
        with pytest.raises(ValidationError, match="uplicate"):
            OperatorFeedback(
                schema_version="1.0",
                feedback_id=uuid4(),
                scan_id=det.scan_id,
                operator_id="op-001",
                detection=det,
                outcome=OperatorOutcome.INSPECTED,
                reviews=[review, review],   # same detection reviewed twice
                decided_at=_NOW,
                emitted_at=_NOW,
            )


class TestReclassifyInvariant:
    def test_reclassified_requires_corrected_category(self):
        with pytest.raises(ValidationError, match="corrected_category"):
            DetectionReview(
                detection_id=uuid4(),
                judgement=DetectionJudgement.RECLASSIFIED,
                corrected_category=None,   # required but absent
            )

    def test_non_reclassified_must_not_carry_corrected_category(self):
        with pytest.raises(ValidationError, match="corrected_category"):
            DetectionReview(
                detection_id=uuid4(),
                judgement=DetectionJudgement.CONFIRMED,
                corrected_category=ThreatCategory.NARCOTICS,   # forbidden
            )

    def test_reclassified_with_category_accepted(self):
        dr = DetectionReview(
            detection_id=uuid4(),
            judgement=DetectionJudgement.RECLASSIFIED,
            corrected_category=ThreatCategory.NARCOTICS,
        )
        assert dr.corrected_category == ThreatCategory.NARCOTICS


class TestMissedRegionValidation:
    def test_valid_missed_region_accepted(self):
        frame = make_frame()
        det   = make_detection_result(detections=[])
        ann   = make_missed_annotation(frame)
        fb    = make_operator_feedback(
            det, missed=[ann], reviews=[]
        )
        assert fb.missed[0].category == ann.category

    def test_missed_region_out_of_frame_rejected(self):
        small_frame = make_frame(width_px=100, height_px=100)
        # Build detection result whose frame is 100×100
        det = make_detection_result(
            detections=[],
            has_findings=False,
            frame=small_frame,
        )
        # Box extends beyond 100×100: x=90, width=50 → right edge at 140 > 100
        out_of_bounds = PixelBox(x=90, y=90, width=50, height=50)
        ann = OperatorAnnotation(
            frame_id=small_frame.frame_id,
            box=out_of_bounds,
            category=ThreatCategory.FIREARM,
        )
        with pytest.raises(ValidationError, match="exceeds frame"):
            OperatorFeedback(
                schema_version="1.0",
                feedback_id=uuid4(),
                scan_id=det.scan_id,
                operator_id="op-001",
                detection=det,
                outcome=OperatorOutcome.INSPECTED,
                missed=[ann],
                decided_at=_NOW,
                emitted_at=_NOW,
            )

    def test_missed_region_unknown_frame_rejected(self):
        det = make_detection_result(detections=[], has_findings=False)
        ann = OperatorAnnotation(
            frame_id="frame-NONEXISTENT",
            box=PixelBox(x=10, y=10, width=50, height=50),
            category=ThreatCategory.NARCOTICS,
        )
        with pytest.raises(ValidationError, match="unknown frame_id"):
            OperatorFeedback(
                schema_version="1.0",
                feedback_id=uuid4(),
                scan_id=det.scan_id,
                operator_id="op-001",
                detection=det,
                outcome=OperatorOutcome.INSPECTED,
                missed=[ann],
                decided_at=_NOW,
                emitted_at=_NOW,
            )


class TestScanIdConsistency:
    def test_mismatched_scan_id_rejected(self):
        det = make_detection_result()
        with pytest.raises(ValidationError, match="scan_id"):
            OperatorFeedback(
                schema_version="1.0",
                feedback_id=uuid4(),
                scan_id=uuid4(),         # different from det.scan_id
                operator_id="op-001",
                detection=det,
                outcome=OperatorOutcome.CLEARED,
                decided_at=_NOW,
                emitted_at=_NOW,
            )


class TestLabelCounting:
    """n_gold_labels and n_hard_negatives must be computed correctly."""

    def test_confirmed_detection_counts_as_gold_label(self):
        det = make_detection_result()
        fb  = make_operator_feedback(det, outcome=OperatorOutcome.SEIZED)
        assert fb.n_gold_labels   == len(det.detections)
        assert fb.n_hard_negatives == 0

    def test_rejected_detection_counts_as_hard_negative(self):
        det    = make_detection_result()
        review = DetectionReview(
            detection_id=det.detections[0].detection_id,
            judgement=DetectionJudgement.REJECTED,
        )
        fb = make_operator_feedback(det, reviews=[review], outcome=OperatorOutcome.CLEARED)
        assert fb.n_gold_labels    == 0
        assert fb.n_hard_negatives == 1

    def test_missed_region_counts_as_gold_label(self):
        det   = make_detection_result(detections=[], has_findings=False)
        frame = det.frames[0]
        ann   = make_missed_annotation(frame, category=ThreatCategory.EXPLOSIVE)
        fb    = make_operator_feedback(det, missed=[ann], reviews=[], outcome=OperatorOutcome.SEIZED)
        assert fb.n_gold_labels         == 1
        assert fb.is_false_negative_report is True

    def test_empty_feedback_yields_zero_labels(self):
        det = make_detection_result(detections=[], has_findings=False)
        fb  = make_operator_feedback(det, missed=[], reviews=[], outcome=OperatorOutcome.CLEARED)
        assert fb.n_gold_labels         == 0
        assert fb.is_false_negative_report is False


class TestOperatorIdRequired:
    def test_missing_operator_id_rejected(self):
        det = make_detection_result()
        with pytest.raises(ValidationError):
            OperatorFeedback(
                schema_version="1.0",
                feedback_id=uuid4(),
                scan_id=det.scan_id,
                operator_id="",          # empty string — must be rejected
                detection=det,
                outcome=OperatorOutcome.CLEARED,
                decided_at=_NOW,
                emitted_at=_NOW,
            )
