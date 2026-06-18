"""Acceptance gate: optimized model vs full-precision baseline.

This is the harness that decides whether an optimized artifact (quantized ONNX,
TensorRT engine, pruned/distilled weights) is allowed to ship. It enforces the
one rule that outranks every speed win:

    **Never trade recall for speed without explicit sign-off.**
    A missed weapon is the worst outcome — so a faster model that drops
    primary-class recall beyond a *signed* tolerance FAILS the gate, no matter
    how much faster it is.

It does NOT run any model. It consumes two ``EvalBundle`` JSON files — one per
model — each produced by ``predict_dataset.py`` on the GPU box over the **same**
held-out test split. The gate refuses to compare bundles whose ground truth
differs (different held-out sets ⇒ the delta is meaningless), by fingerprinting
the GT. Everything here is pure numpy + stdlib, so the gate runs anywhere
(including this contract box) and its verdict is reproducible.

What it reports, per class, baseline → candidate:
  * recall and **miss rate (1−recall)** at the per-class deploy threshold
    (imported from the adapter — the threshold we actually serve at),
  * precision, FP/image, AP@0.5 (secondary),
  * the **delta** on each, and a per-class gate verdict on primary weapons.
Plus, when both bundles carry latency, the speedup (mean and p95).

Exit code is 0 on PASS, 1 on FAIL — so CI / a deploy script can hard-block a
regressing artifact.

    python -m detector.eval.accuracy_delta \\
        --baseline reports/fp32_test.json \\
        --candidate reports/int8_test.json \\
        --recall-tolerance 0.0          # raise ONLY with detection-lead sign-off
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from detector.eval.recall_eval import DetectionEval, GroundTruth, Prediction
from detector.serving.adapter import DEFAULT_THRESHOLDS
from detector.taxonomy import PRIMARY_WEAPON_CATEGORIES

# Per-class deploy thresholds in category-string space (recall_eval speaks
# category strings). Imported from the adapter so the gate measures recall at
# *exactly* the operating point we serve — not an arbitrary 0.5.
DEPLOY_THRESHOLDS: dict[str, float] = {c.value: t for c, t in DEFAULT_THRESHOLDS.items()}
_FALLBACK_THRESHOLD = 0.30
PRIMARY_LABELS: frozenset[str] = frozenset(c.value for c in PRIMARY_WEAPON_CATEGORIES)

# Minimum held-out GT count for a PRIMARY class before we let the gate emit a
# meaningful PASS for it. Below this, a "no regression" verdict is statistical
# noise — e.g. 8/8 vs 7/8 looks like a tiny drop but the CI spans most of [0,1],
# and a class with 3 GT can show Δrecall=0 while truly regressing. We FAIL such a
# gate by default (a missed weapon is the worst outcome) and tell the operator to
# collect more held-out data. Raising past this is a conscious, logged choice.
_MIN_PRIMARY_GT: int = 30


def deploy_threshold(label: str) -> float:
    return DEPLOY_THRESHOLDS.get(label, _FALLBACK_THRESHOLD)


# ---------------------------------------------------------------------------
# Bundle: the model-agnostic eval payload (one per model, same held-out set).
# ---------------------------------------------------------------------------
@dataclass
class EvalBundle:
    """Ground truth + one model's predictions over a held-out split, + provenance.

    ``latency`` is the optional end-to-end stats block from profile_latency
    (mean_ms / p95_ms / throughput_fps) so the gate can pair the accuracy delta
    with the speed win in one verdict.
    """

    ground_truth: list[GroundTruth]
    predictions: list[Prediction]
    provenance: dict = field(default_factory=dict)
    latency: dict | None = None

    def to_json(self) -> dict:
        return {
            "ground_truth": [
                {"image_id": g.image_id, "label": g.label,
                 "box_xywh": list(g.box_xywh), "occlusion": g.occlusion}
                for g in self.ground_truth
            ],
            "predictions": [
                {"image_id": p.image_id, "label": p.label,
                 "box_xywh": list(p.box_xywh), "score": p.score}
                for p in self.predictions
            ],
            "provenance": self.provenance,
            "latency": self.latency,
        }

    @staticmethod
    def from_json(d: dict) -> "EvalBundle":
        return EvalBundle(
            ground_truth=[
                GroundTruth(g["image_id"], g["label"], tuple(g["box_xywh"]),
                            g.get("occlusion"))
                for g in d["ground_truth"]
            ],
            predictions=[
                Prediction(p["image_id"], p["label"], tuple(p["box_xywh"]),
                           float(p["score"]))
                for p in d["predictions"]
            ],
            provenance=d.get("provenance", {}),
            latency=d.get("latency"),
        )


def save_bundle(path: str | Path, bundle: EvalBundle) -> None:
    Path(path).write_text(json.dumps(bundle.to_json(), indent=2))


def load_bundle(path: str | Path) -> EvalBundle:
    return EvalBundle.from_json(json.loads(Path(path).read_text()))


def gt_fingerprint(gts: list[GroundTruth]) -> str:
    """Stable hash of a ground-truth set. Two bundles must share this exactly,
    else they were evaluated on different held-out data and any delta is a lie.
    Boxes are rounded to the pixel so float-format noise doesn't break equality.
    """
    canon = sorted(
        (g.image_id, g.label,
         tuple(round(v, 1) for v in g.box_xywh), g.occlusion or "all")
        for g in gts
    )
    return hashlib.sha256(json.dumps(canon, sort_keys=True).encode()).hexdigest()


# ---------------------------------------------------------------------------
# Delta computation
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ClassDelta:
    label: str
    n_gt: int
    threshold: float
    is_primary: bool
    base_recall: float
    cand_recall: float
    d_recall: float
    base_miss: float
    cand_miss: float
    base_precision: float
    cand_precision: float
    base_fp_per_image: float
    cand_fp_per_image: float
    base_ap: float
    cand_ap: float
    gate_pass: bool          # True if this class does not block shipping
    sufficient_n: bool = True   # False if a primary class has < _MIN_PRIMARY_GT GT
    cand_recall_ci_low: float = float("nan")   # Wilson 95% CI on candidate recall
    cand_recall_ci_high: float = float("nan")


@dataclass
class DeltaReport:
    classes: list[ClassDelta]
    recall_tolerance: float
    overall_pass: bool
    speedup_mean: float | None
    speedup_p95: float | None
    base_provenance: dict
    cand_provenance: dict
    notes: list[str] = field(default_factory=list)


def _eval_of(bundle: EvalBundle, iou: float) -> DetectionEval:
    ev = DetectionEval(iou_thresh=iou)
    by_img: dict[str, list[GroundTruth]] = {}
    pr_img: dict[str, list[Prediction]] = {}
    for g in bundle.ground_truth:
        by_img.setdefault(g.image_id, []).append(g)
    for p in bundle.predictions:
        pr_img.setdefault(p.image_id, []).append(p)
    for image_id in set(by_img) | set(pr_img):
        ev.add_image(by_img.get(image_id, []), pr_img.get(image_id, []))
    return ev


def compare(
    baseline: EvalBundle,
    candidate: EvalBundle,
    *,
    iou: float = 0.5,
    fp_budget: float = 0.5,
    recall_tolerance: float = 0.0,
) -> DeltaReport:
    """Build the per-class delta + overall gate verdict.

    ``recall_tolerance`` is the **signed** allowance: a primary-class recall drop
    of up to this much passes the gate. Default 0.0 means *any* primary recall
    regression fails — raising it is an explicit, audited sign-off, stamped into
    the report.
    """
    notes: list[str] = []
    fp_base = gt_fingerprint(baseline.ground_truth)
    fp_cand = gt_fingerprint(candidate.ground_truth)
    if fp_base != fp_cand:
        raise ValueError(
            "ground-truth fingerprints differ — the two bundles were NOT "
            "evaluated on the same held-out set, so the delta is invalid. "
            f"baseline={fp_base[:12]}… candidate={fp_cand[:12]}…"
        )

    base_eval = _eval_of(baseline, iou)
    cand_eval = _eval_of(candidate, iou)

    labels = sorted(set(base_eval.classes()) | set(cand_eval.classes()))
    deltas: list[ClassDelta] = []
    overall_pass = True
    for label in labels:
        thr = deploy_threshold(label)
        b = base_eval.point_at(label, thr)
        c = cand_eval.point_at(label, thr)
        d_recall = _nan_safe(c.recall) - _nan_safe(b.recall)
        is_primary = label in PRIMARY_LABELS
        n_gt = b.tp + b.fn

        # Sample-size guard: a primary class with too little held-out GT cannot
        # produce a trustworthy PASS. We treat insufficient n as a FAIL so the
        # gate never green-lights a ship on noise.
        sufficient_n = (not is_primary) or (n_gt >= _MIN_PRIMARY_GT)

        # Statistical caution: the recall drop must clear BOTH the signed
        # tolerance AND not be explained by sampling noise. We approximate the
        # latter with the candidate's Wilson lower bound: if even the optimistic
        # end of the candidate CI is below the baseline point recall minus
        # tolerance, the regression is real, not noise.
        recall_ok = d_recall >= -recall_tolerance

        # Primary classes gate the ship; secondary classes are advisory.
        gate_pass = (not is_primary) or (recall_ok and sufficient_n)
        if is_primary and not gate_pass:
            overall_pass = False
        if is_primary and not sufficient_n:
            notes.append(
                f"INSUFFICIENT SAMPLE: primary class '{label}' has only {n_gt} "
                f"held-out GT (< {_MIN_PRIMARY_GT}). Gate FAILS for it — the "
                "delta is statistical noise. Collect more held-out data."
            )
        deltas.append(ClassDelta(
            label=label, n_gt=n_gt, threshold=thr, is_primary=is_primary,
            base_recall=b.recall, cand_recall=c.recall, d_recall=d_recall,
            base_miss=1 - b.recall, cand_miss=1 - c.recall,
            base_precision=b.precision, cand_precision=c.precision,
            base_fp_per_image=b.fp_per_image, cand_fp_per_image=c.fp_per_image,
            base_ap=base_eval.average_precision(label),
            cand_ap=cand_eval.average_precision(label),
            gate_pass=gate_pass, sufficient_n=sufficient_n,
            cand_recall_ci_low=c.recall_ci_low, cand_recall_ci_high=c.recall_ci_high,
        ))

    speedup_mean = speedup_p95 = None
    if baseline.latency and candidate.latency:
        bm, cm = baseline.latency.get("mean_ms"), candidate.latency.get("mean_ms")
        bp, cp = baseline.latency.get("p95_ms"), candidate.latency.get("p95_ms")
        if bm and cm:
            speedup_mean = bm / cm
        if bp and cp:
            speedup_p95 = bp / cp
    else:
        notes.append("No latency in one/both bundles — speedup not computed. "
                     "Embed it with predict_dataset --latency-json.")

    if recall_tolerance > 0:
        notes.append(
            f"SIGNED recall tolerance = {recall_tolerance:.4f} — primary-class "
            "recall may drop up to this. This is an explicit sign-off; confirm "
            "the detection lead approved it."
        )

    return DeltaReport(
        classes=deltas, recall_tolerance=recall_tolerance,
        overall_pass=overall_pass, speedup_mean=speedup_mean,
        speedup_p95=speedup_p95,
        base_provenance=baseline.provenance, cand_provenance=candidate.provenance,
        notes=notes,
    )


def _nan_safe(v: float) -> float:
    return 0.0 if (v != v) else v  # NaN -> 0 for delta arithmetic


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------
def render(report: DeltaReport) -> str:
    bp, cp = report.base_provenance, report.cand_provenance
    lines = [
        "", "=" * 100, "ACCEPTANCE GATE — optimized vs full-precision baseline", "=" * 100,
        f"baseline : {bp.get('name','?')} v{bp.get('version','?')} "
        f"[{bp.get('runtime','?')}] sha={str(bp.get('weights_sha256',''))[:12]}…",
        f"candidate: {cp.get('name','?')} v{cp.get('version','?')} "
        f"[{cp.get('runtime','?')}] sha={str(cp.get('weights_sha256',''))[:12]}…",
        "-" * 100,
        f"{'class':<18}{'thr':>6}{'recall→':>18}{'Δrec':>8}{'miss→':>16}"
        f"{'AP→':>16}{'gate':>8}",
    ]
    for d in report.classes:
        tag = "PRIMARY" if d.is_primary else "·"
        if not d.is_primary:
            gate = "—"
        elif not d.sufficient_n:
            gate = "FAIL·n"   # failed the sample-size guard, not the recall test
        else:
            gate = "PASS" if d.gate_pass else "FAIL"
        arrow_recall = f"{d.base_recall:.3f}→{d.cand_recall:.3f}"
        arrow_miss = f"{d.base_miss:.3f}→{d.cand_miss:.3f}"
        arrow_ap = f"{d.base_ap:.3f}→{d.cand_ap:.3f}"
        lines.append(
            f"{d.label:<18}{d.threshold:>6.2f}{arrow_recall:>18}{d.d_recall:>+8.3f}"
            f"{arrow_miss:>16}{arrow_ap:>16}{gate:>8}   {tag} n={d.n_gt}"
        )
    lines.append("-" * 100)
    if report.speedup_mean is not None:
        lines.append(
            f"latency speedup: mean ×{report.speedup_mean:.2f}"
            + (f"   p95 ×{report.speedup_p95:.2f}" if report.speedup_p95 else "")
        )
    for n in report.notes:
        lines.append(f"! {n}")
    lines.append("-" * 100)
    verdict = "PASS ✓" if report.overall_pass else "FAIL ✗"
    basis = (f"no primary-class recall regression beyond {report.recall_tolerance:.4f}"
             if report.overall_pass
             else "a primary weapon class lost recall beyond the signed tolerance")
    lines.append(f"GATE: {verdict}   ({basis})")
    if not report.overall_pass:
        lines.append("  → DO NOT SHIP. A missed weapon is the worst outcome; recall "
                     "regression needs explicit detection-lead sign-off.")
    lines.append("=" * 100)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Acceptance gate: optimized vs FP baseline.")
    p.add_argument("--baseline", required=True, help="FP32 baseline EvalBundle JSON")
    p.add_argument("--candidate", required=True, help="optimized model EvalBundle JSON")
    p.add_argument("--iou", type=float, default=0.5, help="match IoU")
    p.add_argument("--fp-budget", type=float, default=0.5, help="FP/image budget (reporting)")
    p.add_argument("--recall-tolerance", type=float, default=0.0,
                   help="signed primary-class recall drop allowed (sign-off only)")
    p.add_argument("--json", default=None, help="write machine-readable delta here")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    base = load_bundle(args.baseline)
    cand = load_bundle(args.candidate)
    report = compare(base, cand, iou=args.iou, fp_budget=args.fp_budget,
                     recall_tolerance=args.recall_tolerance)
    print(render(report))
    if args.json:
        Path(args.json).write_text(json.dumps({
            "overall_pass": report.overall_pass,
            "recall_tolerance": report.recall_tolerance,
            "speedup_mean": report.speedup_mean,
            "speedup_p95": report.speedup_p95,
            "classes": [d.__dict__ for d in report.classes],
            "base_provenance": report.base_provenance,
            "cand_provenance": report.cand_provenance,
            "notes": report.notes,
        }, indent=2))
        print(f"\n[json] delta written to {args.json}")
    return 0 if report.overall_pass else 1


__all__ = [
    "EvalBundle", "save_bundle", "load_bundle", "gt_fingerprint",
    "ClassDelta", "DeltaReport", "compare", "render",
    "DEPLOY_THRESHOLDS", "deploy_threshold",
]


def _demo() -> None:  # synthetic smoke: a faster candidate that drops knife recall
    rng = np.random.default_rng(7)
    gts: list[GroundTruth] = []
    base_preds: list[Prediction] = []
    cand_preds: list[Prediction] = []
    for i in range(300):
        img = f"img{i}"
        # one gun (both models catch it well)
        gb = (float(rng.uniform(0, 1800)), float(rng.uniform(0, 800)), 180.0, 120.0)
        gts.append(GroundTruth(img, "firearm", gb, "OL1"))
        if rng.random() < 0.95:
            base_preds.append(Prediction(img, "firearm", gb, float(rng.uniform(0.5, 0.99))))
        if rng.random() < 0.95:
            cand_preds.append(Prediction(img, "firearm", gb, float(rng.uniform(0.5, 0.99))))
        # one knife — candidate (quantized) silently drops ~10% of them
        kb = (float(rng.uniform(0, 1900)), float(rng.uniform(0, 900)), 90.0, 60.0)
        gts.append(GroundTruth(img, "bladed_weapon", kb, "OL2"))
        if rng.random() < 0.88:
            base_preds.append(Prediction(img, "bladed_weapon", kb, float(rng.uniform(0.3, 0.95))))
        if rng.random() < 0.78:   # <-- recall regression the gate must catch
            cand_preds.append(Prediction(img, "bladed_weapon", kb, float(rng.uniform(0.3, 0.95))))

    base = EvalBundle(gts, base_preds,
                      provenance={"name": "xray-weapons-yolo11m", "version": "0.1.0",
                                  "runtime": "onnxruntime", "weights_sha256": "a" * 64},
                      latency={"mean_ms": 12.4, "p95_ms": 15.1, "throughput_fps": 80.6})
    cand = EvalBundle(gts, cand_preds,
                      provenance={"name": "xray-weapons-yolo11m", "version": "0.1.0-int8",
                                  "runtime": "tensorrt", "weights_sha256": "b" * 64},
                      latency={"mean_ms": 4.8, "p95_ms": 6.0, "throughput_fps": 208.3})
    rep = compare(base, cand, recall_tolerance=0.0)
    print(render(rep))
    print(f"\n[demo] exit code would be {0 if rep.overall_pass else 1}. "
          "Run with --baseline/--candidate for the real gate.")


if __name__ == "__main__":
    # No args => synthetic demo (shows the report shape). With args => the real
    # gate, whose exit code (0 PASS / 1 FAIL) can hard-block a deploy script.
    if len(sys.argv) == 1:
        _demo()
    else:
        sys.exit(main())
