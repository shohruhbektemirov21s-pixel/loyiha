"""CLI utility: save current detector metrics as the regression baseline.

Run after a known-good release to establish a baseline that future candidate
models must not regress below:

    python -m tests.model.save_baseline \
        --output /data/baseline/metrics.json \
        [--dataset-path /data/held-out]

Then in CI for candidates:
    XRAY_BASELINE_RECALL_JSON=/data/baseline/metrics.json \
    pytest tests/model/test_detector_metrics.py::test_recall_no_regression -v
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from tests.fixtures.dataset import get_evaluation_dataset
from tests.model.test_detector_metrics import DetectorUnderTest, compute_metrics


def main() -> None:
    parser = argparse.ArgumentParser(description="Save detector metric baseline for regression tests.")
    parser.add_argument("--output",       required=True, help="Path to write metrics.json")
    parser.add_argument("--dataset-path", default="",    help="Override XRAY_TEST_DATASET_PATH")
    parser.add_argument("--fn-rate",      type=float, default=0.0,
                        help="Synthetic FN rate for mock mode (default 0.0 = perfect)")
    args = parser.parse_args()

    if args.dataset_path:
        os.environ["XRAY_TEST_DATASET_PATH"] = args.dataset_path

    print("Loading evaluation dataset…")
    dataset = get_evaluation_dataset()
    print(f"  Dataset: {dataset.version} ({len(dataset)} samples)")

    print("Running detector…")
    detector = DetectorUnderTest(mock_fn_rate=args.fn_rate)
    results  = detector.run_dataset(dataset)
    metrics  = compute_metrics(results)

    print(f"\nResults:")
    print(f"  Overall recall:    {metrics.overall_recall:.4f}")
    print(f"  Overall precision: {metrics.overall_precision:.4f}")
    print(f"  TP={metrics.n_tp}  FP={metrics.n_fp}  FN={metrics.n_fn}  TN={metrics.n_tn}")
    print("\nPer-category recall:")
    for cat, recall in sorted(metrics.per_category_recall.items(), key=lambda x: x[1]):
        print(f"  {cat.value:<25} {recall:.4f}")

    per_cat_json = {cat.value: recall for cat, recall in metrics.per_category_recall.items()}

    output = {
        "generated_at":        datetime.now(timezone.utc).isoformat(),
        "dataset_version":     dataset.version,
        "n_samples":           len(dataset),
        "overall_recall":      metrics.overall_recall,
        "overall_precision":   metrics.overall_precision,
        "n_tp":                metrics.n_tp,
        "n_fp":                metrics.n_fp,
        "n_fn":                metrics.n_fn,
        "n_tn":                metrics.n_tn,
        "per_category_recall": per_cat_json,
    }

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(output, indent=2))
    print(f"\nBaseline saved to: {out_path}")


if __name__ == "__main__":
    main()
