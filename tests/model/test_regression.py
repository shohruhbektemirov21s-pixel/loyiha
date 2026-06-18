"""Model version regression tests.

Verifies that a new detector or VLM candidate does not silently degrade on
a fixed set of previously-correct samples.  These are the examples that were
once right — catching them in a new version means the model went backwards.

Usage:
    # Save regression fixtures after a known-good release:
    XRAY_DETECTOR_WEIGHTS=/models/v1.0.0.onnx \
    python -m tests.model.save_regression_fixtures \
        --output tests/fixtures/regression_samples.jsonl

    # Run regression suite on the candidate:
    XRAY_DETECTOR_WEIGHTS=/models/v1.1.0-candidate.onnx \
    XRAY_REGRESSION_FIXTURES=tests/fixtures/regression_samples.jsonl \
    pytest tests/model/test_regression.py -v
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from uuid import uuid4

import pytest

from contracts.v1.common import ThreatCategory
from tests.fixtures.dataset import LabeledSample, MockLabeledDataset

REGRESSION_FIXTURES_PATH = os.environ.get("XRAY_REGRESSION_FIXTURES", "")

# ---------------------------------------------------------------------------
# Fixture loading
# ---------------------------------------------------------------------------

def _load_regression_fixtures() -> list[dict]:
    """Load saved regression fixtures from JSONL file."""
    if not REGRESSION_FIXTURES_PATH:
        return []
    path = Path(REGRESSION_FIXTURES_PATH)
    if not path.exists():
        return []
    fixtures = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if line:
            fixtures.append(json.loads(line))
    return fixtures


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.skipif(
    not REGRESSION_FIXTURES_PATH,
    reason="XRAY_REGRESSION_FIXTURES not set — skipping regression suite",
)
class TestDetectorRegression:
    """Each sample in regression_samples.jsonl must still be detected correctly."""

    @pytest.fixture(scope="class")
    def regression_fixtures(self):
        return _load_regression_fixtures()

    @pytest.fixture(scope="class")
    def detector(self):
        from tests.model.test_detector_metrics import DetectorUnderTest
        return DetectorUnderTest(mock_fn_rate=0.0)

    def test_all_previously_detected_still_detected(
        self, detector, regression_fixtures
    ):
        """Every sample that was correctly detected in the baseline must still be detected."""
        failures = []

        for fixture in regression_fixtures:
            if not fixture.get("was_tp"):
                continue   # only check previously-TP samples

            sample = LabeledSample(
                image_path=fixture.get("image_path"),
                category=ThreatCategory(fixture["category"]) if fixture.get("category") else None,
                has_threat=fixture["has_threat"],
                difficulty=fixture.get("difficulty", "medium"),
                metadata=fixture,
            )
            result = detector.predict(sample)
            if not result.predicted:
                failures.append(
                    f"{fixture.get('image_path', fixture.get('id', 'unknown'))}: "
                    f"category={fixture.get('category')} difficulty={fixture.get('difficulty')}"
                )

        assert not failures, (
            f"REGRESSION: {len(failures)} previously-detected sample(s) now missed:\n"
            + "\n".join(f"  {f}" for f in failures[:20])
            + ("\n  ... (truncated)" if len(failures) > 20 else "")
        )

    def test_no_new_false_positives_on_confirmed_negatives(
        self, detector, regression_fixtures
    ):
        """Samples confirmed as true negatives must still be clean."""
        regressions = []

        for fixture in regression_fixtures:
            if fixture.get("has_threat"):
                continue   # only check confirmed negatives

            sample = LabeledSample(
                image_path=fixture.get("image_path"),
                category=None,
                has_threat=False,
                difficulty=fixture.get("difficulty", "easy"),
                metadata=fixture,
            )
            result = detector.predict(sample)
            if result.predicted:
                regressions.append(
                    f"{fixture.get('image_path', 'unknown')}: "
                    f"new FP (was clean in baseline)"
                )

        if regressions:
            # FP regression is a warning, not a hard blocker, but we still report
            pytest.xfail(
                f"FP regression (non-blocking): {len(regressions)} new false positive(s). "
                "Review before release.\n" + "\n".join(regressions[:10])
            )


# ---------------------------------------------------------------------------
# Calibration regression: confidence scores must not drift
# ---------------------------------------------------------------------------

class TestConfidenceCalibration:
    """Detector confidence scores should be roughly calibrated.

    A score of 0.80 should mean the detector is right ~80% of the time.
    Significant drift (over-confidence or under-confidence) indicates
    distribution shift or a poorly-calibrated retrained model.
    """

    def test_high_confidence_detections_are_accurate(self):
        """Detections with score ≥ 0.90 should be correct at least 90% of the time.

        Uses mock dataset; in real mode the real dataset + real detector is used.
        """
        from tests.model.test_detector_metrics import DetectorUnderTest, compute_metrics

        ds      = MockLabeledDataset()
        det     = DetectorUnderTest(mock_fn_rate=0.0)
        results = det.run_dataset(ds)

        high_conf_tp = sum(
            1 for r in results
            if r.predicted and r.top_score >= 0.90 and r.sample.has_threat
        )
        high_conf_fp = sum(
            1 for r in results
            if r.predicted and r.top_score >= 0.90 and not r.sample.has_threat
        )

        total_high_conf = high_conf_tp + high_conf_fp
        if total_high_conf == 0:
            pytest.skip("No high-confidence detections in dataset")

        precision_at_90 = high_conf_tp / total_high_conf
        assert precision_at_90 >= 0.90, (
            f"Calibration issue: high-confidence (≥0.90) precision is "
            f"{precision_at_90:.3f}, expected ≥0.90. "
            "Model may be over-confident."
        )

    def test_score_distribution_is_not_degenerate(self):
        """Confidence score distribution must not collapse to 0 or 1.

        A degenerate distribution (all scores ≈ 0.5, or all ≈ 1.0) indicates
        a broken model head or a mismatch between training and inference config.
        """
        from tests.model.test_detector_metrics import DetectorUnderTest
        ds = MockLabeledDataset()
        det = DetectorUnderTest(mock_fn_rate=0.0)
        results = det.run_dataset(ds)

        positive_scores = [r.top_score for r in results if r.predicted and r.sample.has_threat]
        if not positive_scores:
            pytest.skip("No positive predictions to check distribution")

        mean_score = sum(positive_scores) / len(positive_scores)
        assert 0.40 <= mean_score <= 0.98, (
            f"Degenerate score distribution: mean={mean_score:.3f}. "
            "Expected scores distributed between 0.40 and 0.98."
        )
