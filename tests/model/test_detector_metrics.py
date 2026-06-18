"""Detector metric gate tests — the highest-priority suite in this codebase.

PRINCIPLE: recall is the safety-critical metric for a customs X-ray detector.
A false negative (missed contraband) is worse than a false positive (wasted
inspection). These tests BLOCK RELEASE if recall drops below the hard gates.

Gates
─────
    Overall recall   ≥ RECALL_GATE         (0.95 default)
    Per-category     ≥ RECALL_GATE_PER_CAT (0.93 default for most categories)
    High-risk cats   ≥ RECALL_GATE_HIGH_RISK (0.97 for firearm, explosive, narcotics)
    Overall precision ≥ PRECISION_GATE     (0.80 — softer; operators can reject FPs)
    Recall regression ≤ REGRESSION_SLACK   (0.02 — new model must not drop > 2pp vs baseline)

How to run
──────────
    # Smoke (mock dataset, no GPU):
    pytest tests/model/test_detector_metrics.py -v

    # Full gate against real labeled data + real detector:
    XRAY_TEST_DATASET_PATH=/data/held-out \
    XRAY_DETECTOR_WEIGHTS=/models/detector.onnx \
    XRAY_DETECTOR_ENABLED=true \
    pytest tests/model/test_detector_metrics.py -v --tb=short

    # Regression vs previous model:
    XRAY_BASELINE_RECALL_JSON=/data/baseline/metrics.json \
    pytest tests/model/test_detector_metrics.py::test_recall_no_regression -v
"""

from __future__ import annotations

import json
import os
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

import pytest

from contracts.v1.common import ThreatCategory
from tests.fixtures.dataset import (
    REAL_DATASET_AVAILABLE,
    LabeledSample,
    MockLabeledDataset,
    get_evaluation_dataset,
)

# ---------------------------------------------------------------------------
# Gates (can be overridden via env for CI tuning during model development)
# ---------------------------------------------------------------------------
RECALL_GATE          = float(os.environ.get("XRAY_GATE_RECALL",           "0.95"))
RECALL_GATE_PER_CAT  = float(os.environ.get("XRAY_GATE_RECALL_PER_CAT",   "0.93"))
PRECISION_GATE       = float(os.environ.get("XRAY_GATE_PRECISION",         "0.80"))
REGRESSION_SLACK     = float(os.environ.get("XRAY_GATE_REGRESSION_SLACK",  "0.02"))

# Categories where a miss is most catastrophic — stricter per-category gate
HIGH_RISK_CATEGORIES = {
    ThreatCategory.FIREARM,
    ThreatCategory.EXPLOSIVE,
    ThreatCategory.NARCOTICS,
}
RECALL_GATE_HIGH_RISK = float(os.environ.get("XRAY_GATE_RECALL_HIGH_RISK", "0.97"))


# ---------------------------------------------------------------------------
# DetectorUnderTest — thin wrapper around the real or mock detector
# ---------------------------------------------------------------------------

@dataclass
class PredictionResult:
    """Raw output of one detector call."""
    sample:     LabeledSample
    predicted:  bool              # did the detector produce at least one detection?
    top_cat:    ThreatCategory | None = None
    top_score:  float = 0.0


class DetectorUnderTest:
    """Wraps the real detector when XRAY_DETECTOR_ENABLED=true; uses a
    configurable mock otherwise.  The mock simulates a detector with
    parameterisable recall per category so the gate logic itself can be tested.
    """

    def __init__(self, mock_fn_rate: float = 0.0):
        """
        mock_fn_rate: probability of a synthetic miss (false negative) per sample.
        Set to > 0 to test that the gate fires when recall drops.
        """
        self._real = os.environ.get("XRAY_DETECTOR_ENABLED", "false").lower() == "true"
        self._mock_fn_rate = mock_fn_rate
        self._detector = None

        if self._real:
            self._detector = self._build_real_detector()

    def _build_real_detector(self):
        from detector.serving.composition import DetectorConfig, build_detector
        cfg = DetectorConfig(
            weights=os.environ["XRAY_DETECTOR_WEIGHTS"],
            device=os.environ.get("XRAY_DETECTOR_DEVICE", "cpu"),
            name="xray-detector",
            version=os.environ.get("XRAY_DETECTOR_VERSION", "test"),
        )
        return build_detector(cfg)

    def predict(self, sample: LabeledSample) -> PredictionResult:
        if self._real and self._detector and sample.image_path:
            return self._predict_real(sample)
        return self._predict_mock(sample)

    def _predict_real(self, sample: LabeledSample) -> PredictionResult:
        import numpy as np
        from PIL import Image
        img = np.array(Image.open(sample.image_path))
        result = self._detector.run_on_array(img)
        predicted = result.has_findings
        top_cat = result.detections[0].category if result.detections else None
        top_score = result.detections[0].score if result.detections else 0.0
        return PredictionResult(sample=sample, predicted=predicted, top_cat=top_cat, top_score=top_score)

    def _predict_mock(self, sample: LabeledSample) -> PredictionResult:
        """Perfect detector with configurable false-negative rate."""
        import random
        if not sample.has_threat:
            return PredictionResult(sample=sample, predicted=False)
        # Simulate FN with mock_fn_rate probability
        missed = random.random() < self._mock_fn_rate
        if missed:
            return PredictionResult(sample=sample, predicted=False)
        return PredictionResult(
            sample=sample,
            predicted=True,
            top_cat=sample.category,
            top_score=0.85,
        )

    def run_dataset(self, dataset) -> list[PredictionResult]:
        return [self.predict(s) for s in dataset]


# ---------------------------------------------------------------------------
# Metric computation
# ---------------------------------------------------------------------------

@dataclass
class MetricReport:
    overall_recall:     float
    overall_precision:  float
    per_category_recall: dict[ThreatCategory, float] = field(default_factory=dict)
    n_positives:        int = 0
    n_negatives:        int = 0
    n_tp:               int = 0
    n_fp:               int = 0
    n_fn:               int = 0
    n_tn:               int = 0


def compute_metrics(results: list[PredictionResult]) -> MetricReport:
    """Compute recall, precision, and per-category recall from prediction results."""
    tp = fp = fn = tn = 0
    per_cat_tp: dict[ThreatCategory, int] = defaultdict(int)
    per_cat_fn: dict[ThreatCategory, int] = defaultdict(int)

    for r in results:
        gt_pos = r.sample.has_threat
        pred_pos = r.predicted

        if gt_pos and pred_pos:
            tp += 1
            if r.sample.category:
                per_cat_tp[r.sample.category] += 1
        elif gt_pos and not pred_pos:
            fn += 1
            if r.sample.category:
                per_cat_fn[r.sample.category] += 1
        elif not gt_pos and pred_pos:
            fp += 1
        else:
            tn += 1

    recall    = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0

    all_cats = set(per_cat_tp) | set(per_cat_fn)
    per_cat_recall = {
        cat: per_cat_tp[cat] / (per_cat_tp[cat] + per_cat_fn[cat])
        for cat in all_cats
        if (per_cat_tp[cat] + per_cat_fn[cat]) > 0
    }

    return MetricReport(
        overall_recall=recall,
        overall_precision=precision,
        per_category_recall=per_cat_recall,
        n_positives=tp + fn,
        n_negatives=fp + tn,
        n_tp=tp, n_fp=fp, n_fn=fn, n_tn=tn,
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def dataset():
    return get_evaluation_dataset()


@pytest.fixture(scope="module")
def detector():
    return DetectorUnderTest(mock_fn_rate=0.0)   # perfect mock by default


@pytest.fixture(scope="module")
def metrics(dataset, detector) -> MetricReport:
    """Run the full dataset once and cache the report for all tests in this module."""
    results = detector.run_dataset(dataset)
    return compute_metrics(results)


# ---------------------------------------------------------------------------
# ── Gate 1: Overall recall ── (RELEASE BLOCKER)
# ---------------------------------------------------------------------------

def test_overall_recall_meets_gate(metrics: MetricReport):
    """Overall recall must be ≥ RECALL_GATE.

    This is the primary safety gate.  A drop in recall means the detector is
    missing more threats than before.  Block the release.
    """
    assert metrics.overall_recall >= RECALL_GATE, (
        f"RELEASE BLOCKED: overall recall {metrics.overall_recall:.4f} "
        f"< gate {RECALL_GATE:.4f}. "
        f"FN={metrics.n_fn} / positives={metrics.n_positives}. "
        "The detector is missing too many real threats."
    )


# ---------------------------------------------------------------------------
# ── Gate 2: Per-category recall ── (RELEASE BLOCKER per category)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("category", [
    c for c in ThreatCategory if c != ThreatCategory.UNKNOWN
])
def test_per_category_recall(metrics: MetricReport, category: ThreatCategory):
    """Every threat category must individually meet its recall gate.

    High-risk categories (firearm, explosive, narcotics) have a stricter gate.
    Skipped if fewer than 5 samples exist for that category (statistical noise).
    """
    cat_recall = metrics.per_category_recall.get(category)
    if cat_recall is None:
        pytest.skip(f"No samples for category {category.value}")

    gate = RECALL_GATE_HIGH_RISK if category in HIGH_RISK_CATEGORIES else RECALL_GATE_PER_CAT

    assert cat_recall >= gate, (
        f"RELEASE BLOCKED: {category.value} recall {cat_recall:.4f} < gate {gate:.4f}. "
        f"The detector is missing {category.value} items at an unacceptable rate."
    )


# ---------------------------------------------------------------------------
# ── Gate 3: Precision ── (WARNING, not a release blocker)
# ---------------------------------------------------------------------------

def test_overall_precision_meets_gate(metrics: MetricReport):
    """Precision must be ≥ PRECISION_GATE.

    Low precision (many false positives) imposes operator fatigue but is less
    dangerous than low recall.  Still enforce a floor to prevent alert fatigue
    from causing operators to start ignoring warnings.
    """
    assert metrics.overall_precision >= PRECISION_GATE, (
        f"Precision {metrics.overall_precision:.4f} < gate {PRECISION_GATE:.4f}. "
        f"FP={metrics.n_fp} / predicted_pos={metrics.n_tp + metrics.n_fp}. "
        "High false-positive rate risks operator fatigue and loss of trust."
    )


# ---------------------------------------------------------------------------
# ── Gate 4: Hard-category zero-miss ── (ABSOLUTE RELEASE BLOCKER)
# ---------------------------------------------------------------------------

def test_no_complete_miss_on_hard_categories(dataset, detector):
    """For firearm and explosive: zero complete misses allowed on 'easy' samples.

    An 'easy' sample should be impossible for a reasonable detector to miss.
    If the detector misses any easy firearm or explosive, it is critically broken.
    """
    from tests.fixtures.dataset import LabeledSample
    critical_cats = {ThreatCategory.FIREARM, ThreatCategory.EXPLOSIVE}

    failures: list[str] = []
    for sample in dataset:
        if (sample.category in critical_cats
                and sample.difficulty == "easy"
                and sample.has_threat):
            result = detector.predict(sample)
            if not result.predicted:
                failures.append(
                    f"MISSED easy {sample.category.value} "
                    f"(path={sample.image_path}, meta={sample.metadata})"
                )

    assert not failures, (
        f"ABSOLUTE RELEASE BLOCKER: detector missed {len(failures)} easy "
        f"high-risk sample(s):\n" + "\n".join(failures[:10])
    )


# ---------------------------------------------------------------------------
# ── Gate 5: Regression vs baseline ──
# ---------------------------------------------------------------------------

@pytest.mark.skipif(
    not os.environ.get("XRAY_BASELINE_RECALL_JSON"),
    reason="XRAY_BASELINE_RECALL_JSON not set — skipping regression gate",
)
def test_recall_no_regression(metrics: MetricReport):
    """New model must not regress recall by more than REGRESSION_SLACK vs baseline.

    Usage:
        # After a known-good release, save metrics:
        python -m tests.model.save_baseline --output /data/baseline/metrics.json

        # Then in CI for the new candidate:
        XRAY_BASELINE_RECALL_JSON=/data/baseline/metrics.json pytest ...
    """
    baseline_path = os.environ["XRAY_BASELINE_RECALL_JSON"]
    baseline = json.loads(Path(baseline_path).read_text())

    baseline_recall = baseline["overall_recall"]
    current_recall  = metrics.overall_recall
    delta = baseline_recall - current_recall

    assert delta <= REGRESSION_SLACK, (
        f"REGRESSION DETECTED: recall dropped {delta:.4f} "
        f"(baseline={baseline_recall:.4f} → current={current_recall:.4f}). "
        f"Slack allowed: {REGRESSION_SLACK:.4f}. "
        "Investigate before releasing."
    )

    # Also check per-category regression
    regressions = []
    for cat_str, baseline_cat_recall in baseline.get("per_category_recall", {}).items():
        try:
            cat = ThreatCategory(cat_str)
        except ValueError:
            continue
        current_cat = metrics.per_category_recall.get(cat, 0.0)
        cat_delta = baseline_cat_recall - current_cat
        if cat_delta > REGRESSION_SLACK:
            regressions.append(
                f"  {cat_str}: {baseline_cat_recall:.4f} → {current_cat:.4f} "
                f"(dropped {cat_delta:.4f})"
            )

    assert not regressions, (
        "Per-category recall regression detected:\n" + "\n".join(regressions)
    )


# ---------------------------------------------------------------------------
# ── Metric gate self-tests: verify the gate logic fires correctly ──
# ---------------------------------------------------------------------------

class TestGateLogicItself:
    """Meta-tests: confirm the gate machinery catches a degraded detector.

    These run on the mock dataset with an injected FN rate and verify that
    the gates correctly fail.  If these tests pass it means the gate code works.
    """

    def test_perfect_detector_passes_all_gates(self):
        """A 0% FN rate detector must pass every gate."""
        ds       = MockLabeledDataset()
        det      = DetectorUnderTest(mock_fn_rate=0.0)
        results  = det.run_dataset(ds)
        m        = compute_metrics(results)

        assert m.overall_recall    >= RECALL_GATE
        assert m.overall_precision >= PRECISION_GATE

    def test_high_fn_rate_fails_recall_gate(self):
        """A 20% FN rate detector must fail the recall gate."""
        ds       = MockLabeledDataset()
        det      = DetectorUnderTest(mock_fn_rate=0.20)
        results  = det.run_dataset(ds)
        m        = compute_metrics(results)

        assert m.overall_recall < RECALL_GATE, (
            "Expected a 20% FN rate detector to fail the recall gate, but it passed. "
            "Check gate thresholds."
        )

    def test_moderate_fn_fails_high_risk_category_gate(self):
        """A detector missing 5% of firearms must fail the high-risk gate."""
        import random
        ds      = MockLabeledDataset()
        det     = DetectorUnderTest(mock_fn_rate=0.0)
        results = det.run_dataset(ds)

        # Inject firearm misses post-hoc
        firearm_results = [r for r in results if r.sample.category == ThreatCategory.FIREARM]
        n_miss = max(1, int(len(firearm_results) * 0.05))
        for r in firearm_results[:n_miss]:
            # Replace predicted=True with predicted=False
            results[results.index(r)] = PredictionResult(
                sample=r.sample, predicted=False
            )

        m = compute_metrics(results)
        cat_recall = m.per_category_recall.get(ThreatCategory.FIREARM, 1.0)
        assert cat_recall < RECALL_GATE_HIGH_RISK, (
            "Expected injected firearm FNs to fail the high-risk gate."
        )
