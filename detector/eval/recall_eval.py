"""Recall-first detection evaluation.

Principle this module exists to enforce: **a missed weapon is the worst
failure**, so the headline number is not mAP — it is *miss rate* (FNR =
1 - recall) at the confidence threshold we actually deploy, measured per class,
on held-out data, ideally from a *different scanner* than we trained on.

mAP is reported too, but as a secondary, comparison-only number. mAP averages
over precision at recall levels we would never operate at; a detector can have a
great mAP and still miss 1-in-10 guns at the threshold we ship. This harness
makes that visible.

What it computes
----------------
* greedy IoU matching of predictions to ground truth, per class, per image
  (one prediction may claim at most one GT; highest-confidence first — the COCO
  convention);
* a confidence sweep -> per-class recall, precision, and **false-positives per
  image** (the operator-facing cost of a low threshold, more honest than
  precision when negatives dominate);
* operating-point queries: ``recall_at_fp_per_image`` and
  ``recall_at_precision`` — "what recall do we get if we accept N nuisance
  boxes per scan?";
* occlusion-stratified recall (OPIXray OL1/2/3, PIDray hidden subset) — recall
  must be reported *as occlusion increases*, because that is where detectors
  silently fail and a single average hides it;
* mAP@0.5 (secondary).

Everything is pure numpy so it runs anywhere — no model, no GPU.

Data format (deliberately model-agnostic; the adapter's RawDetection or raw
YOLO output both map onto it trivially):

    GroundTruth(image_id, label, box_xywh, occlusion='OL1'|...)   # label = category string
    Prediction(image_id, label, box_xywh, score)

Boxes are ``(x, y, w, h)`` in pixels. ``label`` is the shared category string
(e.g. 'firearm') so eval speaks the same vocabulary as the contract.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class GroundTruth:
    image_id: str
    label: str
    box_xywh: tuple[float, float, float, float]
    occlusion: str | None = None  # e.g. 'OL1'/'OL2'/'OL3' or 'hidden'/'visible'


@dataclass(frozen=True)
class Prediction:
    image_id: str
    label: str
    box_xywh: tuple[float, float, float, float]
    score: float


def wilson_interval(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score interval for a binomial proportion (recall = k recalled / n GT).

    Why Wilson, not normal-approx: recall is a proportion estimated from a SMALL,
    per-class GT count, often near the 0/1 boundary (a class we catch ~95% of, or
    a rare class with 12 instances). The textbook ``p ± z·sqrt(p(1-p)/n)`` breaks
    exactly there — it can dip below 0 or claim a [1.0, 1.0] CI from 8/8. Wilson
    stays inside [0,1] and is honest at the boundary and on tiny n, which is the
    whole point of reporting a CI on a miss-rate. Returns (low, high); for n==0 a
    full-uncertainty (0.0, 1.0).
    """
    if n <= 0:
        return (0.0, 1.0)
    p = k / n
    z2 = z * z
    denom = 1.0 + z2 / n
    center = (p + z2 / (2 * n)) / denom
    margin = (z * ((p * (1 - p) / n + z2 / (4 * n * n)) ** 0.5)) / denom
    return (max(0.0, center - margin), min(1.0, center + margin))


def iou_xywh(a: tuple, b: tuple) -> float:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    ax2, ay2, bx2, by2 = ax + aw, ay + ah, bx + bw, by + bh
    ix1, iy1 = max(ax, bx), max(ay, by)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    iw, ih = max(0.0, ix2 - ix1), max(0.0, iy2 - iy1)
    inter = iw * ih
    if inter <= 0:
        return 0.0
    union = aw * ah + bw * bh - inter
    return inter / union if union > 0 else 0.0


@dataclass(frozen=True)
class ClassPRPoint:
    threshold: float
    recall: float
    precision: float
    fp_per_image: float
    tp: int
    fp: int
    fn: int
    recall_ci_low: float = float("nan")   # Wilson 95% CI on recall (binomial)
    recall_ci_high: float = float("nan")


class DetectionEval:
    """Match once at a fixed IoU, then answer recall/precision/FP questions at
    any confidence threshold without re-matching.

    Matching is independent of the confidence threshold: we match *all*
    predictions to GT greedily by score, recording each matched prediction's
    score and each GT's occlusion tag. Thresholding then becomes a cheap filter
    over those per-prediction/per-GT records. This is what lets us sweep
    thresholds and stratify by occlusion cheaply and consistently.
    """

    def __init__(self, iou_thresh: float = 0.5) -> None:
        self.iou_thresh = iou_thresh
        # per class: list of (score, is_tp) for every prediction
        self._pred_records: dict[str, list[tuple[float, bool]]] = defaultdict(list)
        # per class: total GT count, and GT count per occlusion bucket
        self._n_gt: dict[str, int] = defaultdict(int)
        self._n_gt_by_occ: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        # per class: for each GT, (matched?, matched_score_or_-1, occlusion) — for recall stratification
        self._gt_records: dict[str, list[tuple[bool, float, str | None]]] = defaultdict(list)
        self._n_images = 0

    def add_image(self, gts: list[GroundTruth], preds: list[Prediction]) -> None:
        self._n_images += 1
        by_label_gt: dict[str, list[GroundTruth]] = defaultdict(list)
        by_label_pred: dict[str, list[Prediction]] = defaultdict(list)
        for g in gts:
            by_label_gt[g.label].append(g)
            self._n_gt[g.label] += 1
            self._n_gt_by_occ[g.label][g.occlusion or "all"] += 1
        for p in preds:
            by_label_pred[p.label].append(p)

        labels = set(by_label_gt) | set(by_label_pred)
        for label in labels:
            self._match_label(label, by_label_gt[label], by_label_pred[label])

    def _match_label(self, label: str, gts: list[GroundTruth], preds: list[Prediction]) -> None:
        gt_matched = [False] * len(gts)
        gt_match_score = [-1.0] * len(gts)
        # highest score first — COCO greedy assignment
        for p in sorted(preds, key=lambda x: x.score, reverse=True):
            best_iou, best_j = 0.0, -1
            for j, g in enumerate(gts):
                if gt_matched[j]:
                    continue
                v = iou_xywh(p.box_xywh, g.box_xywh)
                if v >= self.iou_thresh and v > best_iou:
                    best_iou, best_j = v, j
            is_tp = best_j >= 0
            if is_tp:
                gt_matched[best_j] = True
                gt_match_score[best_j] = p.score
            self._pred_records[label].append((p.score, is_tp))
        for j, g in enumerate(gts):
            self._gt_records[label].append((gt_matched[j], gt_match_score[j], g.occlusion))

    # -- queries -------------------------------------------------------------
    def classes(self) -> list[str]:
        return sorted(set(self._n_gt) | set(self._pred_records))

    def point_at(self, label: str, threshold: float) -> ClassPRPoint:
        """Recall / precision / FP-per-image for one class at one threshold."""
        preds = self._pred_records.get(label, [])
        tp = sum(1 for s, is_tp in preds if s >= threshold and is_tp)
        fp = sum(1 for s, is_tp in preds if s >= threshold and not is_tp)
        n_gt = self._n_gt.get(label, 0)
        fn = n_gt - tp
        recall = tp / n_gt if n_gt else float("nan")
        precision = tp / (tp + fp) if (tp + fp) else float("nan")
        fp_per_image = fp / self._n_images if self._n_images else float("nan")
        # 95% Wilson CI on recall — exposes how trustworthy a per-class recall is
        # given the GT count (a 0.90 on 10 GT is not a 0.90 on 1000 GT).
        if n_gt:
            ci_low, ci_high = wilson_interval(tp, n_gt)
        else:
            ci_low = ci_high = float("nan")
        return ClassPRPoint(threshold, recall, precision, fp_per_image, tp, fp, fn,
                            recall_ci_low=ci_low, recall_ci_high=ci_high)

    def sweep(self, label: str, thresholds: np.ndarray | None = None) -> list[ClassPRPoint]:
        if thresholds is None:
            thresholds = np.linspace(0.01, 0.99, 99)
        return [self.point_at(label, float(t)) for t in thresholds]

    def recall_at_fp_per_image(self, label: str, max_fp_per_image: float) -> ClassPRPoint:
        """Highest recall achievable while keeping FP/image <= budget.

        This is the operator-facing operating point: 'if we tolerate N nuisance
        boxes per scan, what fraction of real weapons do we catch?'
        """
        feasible = [p for p in self.sweep(label) if p.fp_per_image <= max_fp_per_image]
        if not feasible:
            return self.point_at(label, 0.99)
        return max(feasible, key=lambda p: p.recall)

    def recall_at_precision(self, label: str, min_precision: float) -> ClassPRPoint:
        feasible = [p for p in self.sweep(label)
                    if not np.isnan(p.precision) and p.precision >= min_precision]
        if not feasible:
            return self.point_at(label, 0.99)
        return max(feasible, key=lambda p: p.recall)

    def recall_by_occlusion(self, label: str, threshold: float) -> dict[str, float]:
        """Recall split by occlusion bucket at a fixed threshold.

        A GT is recalled iff it was matched by a prediction whose score >=
        threshold. This is the curve that exposes silent failure under clutter.
        """
        buckets: dict[str, list[int]] = defaultdict(lambda: [0, 0])  # occ -> [recalled, total]
        for matched, score, occ in self._gt_records.get(label, []):
            key = occ or "all"
            buckets[key][1] += 1
            if matched and score >= threshold:
                buckets[key][0] += 1
        return {k: (r / n if n else float("nan")) for k, (r, n) in sorted(buckets.items())}

    def average_precision(self, label: str) -> float:
        """AP@iou (area under PR curve, all-points). Secondary metric."""
        preds = sorted(self._pred_records.get(label, []), key=lambda x: x[0], reverse=True)
        n_gt = self._n_gt.get(label, 0)
        if n_gt == 0:
            return float("nan")
        tp_cum = fp_cum = 0
        recalls, precisions = [0.0], [1.0]
        for score, is_tp in preds:
            tp_cum += int(is_tp)
            fp_cum += int(not is_tp)
            recalls.append(tp_cum / n_gt)
            precisions.append(tp_cum / (tp_cum + fp_cum))
        recalls_a = np.array(recalls)
        precisions_a = np.array(precisions)
        # monotone-decreasing precision envelope, then integrate over recall
        for i in range(len(precisions_a) - 2, -1, -1):
            precisions_a[i] = max(precisions_a[i], precisions_a[i + 1])
        idx = np.where(recalls_a[1:] != recalls_a[:-1])[0]
        return float(np.sum((recalls_a[idx + 1] - recalls_a[idx]) * precisions_a[idx + 1]))

    def report(self, *, deploy_threshold: float = 0.20, fp_budget: float = 0.5) -> str:
        lines = [
            f"Recall-first evaluation  (IoU={self.iou_thresh}, images={self._n_images})",
            f"Deploy threshold = {deploy_threshold}   FP/image budget = {fp_budget}",
            "-" * 78,
            f"{'class':<18}{'recall@thr':>11}{'recall 95%CI':>18}{'miss@thr':>10}"
            f"{'prec@thr':>10}{'FP/img':>9}{'R@FPbud':>9}{'AP@'+str(self.iou_thresh):>9}",
        ]
        for label in self.classes():
            p = self.point_at(label, deploy_threshold)
            rb = self.recall_at_fp_per_image(label, fp_budget)
            ap = self.average_precision(label)
            miss = 1 - p.recall if not np.isnan(p.recall) else float("nan")
            ci = (f"[{p.recall_ci_low:.3f},{p.recall_ci_high:.3f}]"
                  if not np.isnan(p.recall_ci_low) else "—")
            n_gt = self._n_gt.get(label, 0)
            small = "  ⚠n<30" if 0 < n_gt < 30 else ""
            lines.append(
                f"{label:<18}{p.recall:>11.3f}{ci:>18}{miss:>10.3f}"
                f"{p.precision:>10.3f}{p.fp_per_image:>9.3f}{rb.recall:>9.3f}{ap:>9.3f}{small}"
            )
        # occlusion stratification, primary weapon classes only
        for label in self.classes():
            occ = self.recall_by_occlusion(label, deploy_threshold)
            if len(occ) > 1:  # only interesting when buckets exist
                buckets = "  ".join(f"{k}:{v:.3f}" for k, v in occ.items())
                lines.append(f"  occlusion recall [{label}] @thr {deploy_threshold}: {buckets}")
        return "\n".join(lines)


__all__ = ["GroundTruth", "Prediction", "DetectionEval", "ClassPRPoint",
           "iou_xywh", "wilson_interval"]


if __name__ == "__main__":  # synthetic smoke: shows the report shape end-to-end
    rng = np.random.default_rng(0)
    ev = DetectionEval(iou_thresh=0.5)
    # simulate 200 images: guns easy (high scores), knives harder under occlusion
    for i in range(200):
        gts, preds = [], []
        # one gun per image
        gbox = (rng.uniform(0, 1800), rng.uniform(0, 800), 180, 120)
        gts.append(GroundTruth(f"img{i}", "firearm", gbox, "OL1"))
        if rng.random() < 0.93:  # 93% caught
            preds.append(Prediction(f"img{i}", "firearm", gbox, float(rng.uniform(0.5, 0.99))))
        # one knife, occlusion makes recall drop
        occ = rng.choice(["OL1", "OL2", "OL3"])
        kbox = (rng.uniform(0, 1900), rng.uniform(0, 900), 90, 60)
        gts.append(GroundTruth(f"img{i}", "bladed_weapon", kbox, occ))
        catch_p = {"OL1": 0.9, "OL2": 0.75, "OL3": 0.5}[occ]
        if rng.random() < catch_p:
            preds.append(Prediction(f"img{i}", "bladed_weapon", kbox, float(rng.uniform(0.3, 0.95))))
        # a couple of nuisance false positives
        for _ in range(rng.integers(0, 2)):
            fb = (rng.uniform(0, 1900), rng.uniform(0, 900), 80, 80)
            preds.append(Prediction(f"img{i}", "bladed_weapon", fb, float(rng.uniform(0.1, 0.5))))
        ev.add_image(gts, preds)
    print(ev.report(deploy_threshold=0.20, fp_budget=0.5))
