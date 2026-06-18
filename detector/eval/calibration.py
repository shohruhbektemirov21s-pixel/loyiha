"""Confidence calibration for the detector score.

Why this exists: the contract carries a ``score`` in [0,1] and the *downstream*
FastAPI layer thresholds on it. A raw YOLO/RT-DETR objectness*class score is not
a probability — a "0.6" gun and a "0.6" knife rarely mean "60% likely correct".
If the score isn't calibrated, every threshold downstream is guesswork and the
risk bands the console shows are meaningless. So we fit a per-class mapping from
raw score -> empirical probability of being a true positive, on a held-out
*calibration split* (never the test split), and ship it inside the adapter's
``Calibrator``.

Method: **Platt scaling** (1-D logistic regression  p = sigmoid(a*s + b)) fit per
class on matched detections (label 1 = true positive, 0 = false positive at the
eval IoU). Platt is the right tool here — few parameters, robust on the modest
number of matched detections a calibration split yields, and monotonic so it
never reorders detections (recall ranking is preserved).

We report **ECE** (expected calibration error), **MCE** (the worst single-bin
error — what ECE's averaging can hide), and a reliability table before/after, so
the calibration's effect is measured, not assumed. Reliability binning supports
equal-WIDTH (legacy) and equal-MASS (quantile) strategies; equal-mass is the
default in ``calibration_report`` because detector scores pile up and equal-width
bins then leave most bins empty. A loud low-sample warning fires when there are
too few matched detections for the estimate to mean anything.

Pure numpy. The fitted ``PlattCalibrator`` plugs straight into
``detector.serving.adapter.WeaponsDetector(calibrator=...)``.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


def _sigmoid(z: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-np.clip(z, -30, 30)))


@dataclass
class _PlattParams:
    a: float
    b: float


def _fit_platt(scores: np.ndarray, y: np.ndarray, *, iters: int = 100, lr: float = 0.5) -> _PlattParams:
    """Newton-ish logistic fit of p = sigmoid(a*s + b). Small, well-conditioned.

    Uses batch gradient with a mild L2 on `a` for stability when a class has few
    samples. Falls back to identity-ish params if a class is degenerate.
    """
    s = scores.astype(np.float64)
    if len(s) < 8 or len(np.unique(y)) < 2:
        # not enough signal to calibrate — identity in logit space
        return _PlattParams(a=1.0, b=0.0)
    a, b = 1.0, 0.0
    n = len(s)
    for _ in range(iters):
        p = _sigmoid(a * s + b)
        ga = np.dot(p - y, s) / n + 1e-3 * (a - 1.0)
        gb = np.sum(p - y) / n
        a -= lr * ga
        b -= lr * gb
    return _PlattParams(a=float(a), b=float(b))


class PlattCalibrator:
    """Per-class Platt calibrator implementing the adapter's ``Calibrator``.

    Fit with :meth:`fit`, persist :attr:`params` (it's just 2 floats per class)
    alongside the weights, reload, and pass into the adapter. Unknown labels
    pass through unchanged (honest: we don't fabricate calibration we didn't
    fit).
    """

    def __init__(self, params: dict[str, tuple[float, float]] | None = None) -> None:
        self._params: dict[str, _PlattParams] = {
            k: _PlattParams(*v) for k, v in (params or {}).items()
        }

    @property
    def params(self) -> dict[str, tuple[float, float]]:
        return {k: (p.a, p.b) for k, p in self._params.items()}

    def fit(self, label: str, scores: np.ndarray, is_tp: np.ndarray) -> None:
        self._params[label] = _fit_platt(np.asarray(scores), np.asarray(is_tp, dtype=np.float64))

    def calibrate(self, native_label: str, raw_score: float) -> float:
        p = self._params.get(native_label)
        if p is None:
            return raw_score
        return float(_sigmoid(np.array([p.a * raw_score + p.b]))[0])


@dataclass(frozen=True)
class ReliabilityBin:
    lo: float
    hi: float
    count: int
    mean_score: float
    frac_positive: float


# Below this many matched detections, a reliability/ECE estimate is too noisy to
# trust — bins hold a handful of samples and |mean_score - frac_pos| is dominated
# by sampling variance. We don't refuse to compute it; we flag it LOUDLY.
_MIN_CALIB_SAMPLES: int = 200


def _equal_mass_edges(scores: np.ndarray, n_bins: int) -> np.ndarray:
    """Quantile (equal-MASS) bin edges: each bin holds ~the same #samples.

    Equal-WIDTH bins (linspace) put almost every detection in one or two bins for
    a peaky score distribution, so most bins are empty and ECE is dominated by the
    one crowded bin. Equal-mass bins spread the samples, giving every region of
    the score range a fair, comparably-powered estimate. Duplicate edges (mass
    piled on one score) are collapsed, so the realized bin count may be < n_bins.
    """
    if len(scores) == 0:
        return np.linspace(0.0, 1.0, n_bins + 1)
    qs = np.linspace(0.0, 1.0, n_bins + 1)
    edges = np.quantile(scores, qs)
    edges[0], edges[-1] = 0.0, 1.0
    return np.unique(edges)


def reliability_table(
    scores: np.ndarray, is_tp: np.ndarray, n_bins: int = 10,
    *, strategy: str = "uniform",
) -> list[ReliabilityBin]:
    """Reliability bins. ``strategy='uniform'`` = equal-width (legacy default);
    ``strategy='quantile'`` = equal-mass (recommended for peaky score dists)."""
    scores = np.asarray(scores, dtype=np.float64)
    y = np.asarray(is_tp, dtype=np.float64)
    if strategy == "quantile":
        edges = _equal_mass_edges(scores, n_bins)
    else:
        edges = np.linspace(0.0, 1.0, n_bins + 1)
    n_real = len(edges) - 1
    out: list[ReliabilityBin] = []
    for i in range(n_real):
        lo, hi = edges[i], edges[i + 1]
        m = (scores >= lo) & (scores < hi if i < n_real - 1 else scores <= hi)
        c = int(m.sum())
        if c == 0:
            out.append(ReliabilityBin(lo, hi, 0, float("nan"), float("nan")))
        else:
            out.append(ReliabilityBin(lo, hi, c, float(scores[m].mean()), float(y[m].mean())))
    return out


def expected_calibration_error(
    scores: np.ndarray, is_tp: np.ndarray, n_bins: int = 10,
    *, strategy: str = "uniform",
) -> float:
    """ECE: sum over bins of (bin weight) * |mean_score - frac_positive|."""
    table = reliability_table(scores, is_tp, n_bins, strategy=strategy)
    n = len(scores)
    if n == 0:
        return float("nan")
    return float(sum(
        (b.count / n) * abs(b.mean_score - b.frac_positive)
        for b in table if b.count > 0
    ))


def maximum_calibration_error(
    scores: np.ndarray, is_tp: np.ndarray, n_bins: int = 10,
    *, strategy: str = "uniform",
) -> float:
    """MCE: the WORST per-bin |mean_score - frac_positive|.

    ECE averages calibration error over bins, so a single badly-miscalibrated
    region (e.g. high-score detections that lie) can be hidden behind well-
    calibrated mass elsewhere. MCE reports that worst bin — the failure mode that
    matters when an operator trusts a high score. Reported alongside ECE.
    """
    table = reliability_table(scores, is_tp, n_bins, strategy=strategy)
    errs = [abs(b.mean_score - b.frac_positive) for b in table if b.count > 0]
    return float(max(errs)) if errs else float("nan")


def calibration_report(
    scores: np.ndarray, is_tp: np.ndarray, n_bins: int = 10,
    *, strategy: str = "quantile",
) -> dict:
    """ECE + MCE (equal-mass by default) with a loud low-sample warning.

    Returns a dict carrying the metrics, the realized bin count, the sample
    count, and ``warning`` (non-empty when the estimate is under-powered) so a
    caller/CI can surface it instead of trusting a noise-dominated number.
    """
    n = int(len(scores))
    ece = expected_calibration_error(scores, is_tp, n_bins, strategy=strategy)
    mce = maximum_calibration_error(scores, is_tp, n_bins, strategy=strategy)
    table = reliability_table(scores, is_tp, n_bins, strategy=strategy)
    warning = ""
    if n < _MIN_CALIB_SAMPLES:
        warning = (
            f"LOW SAMPLE: only {n} matched detections (< {_MIN_CALIB_SAMPLES}). "
            f"ECE/MCE are noise-dominated and the fitted calibration may be "
            f"unreliable — collect more calibration data before trusting it."
        )
    return {
        "n": n,
        "ece": ece,
        "mce": mce,
        "n_bins_realized": sum(1 for b in table if b.count > 0),
        "strategy": strategy,
        "warning": warning,
    }


__all__ = [
    "PlattCalibrator", "ReliabilityBin", "reliability_table",
    "expected_calibration_error", "maximum_calibration_error",
    "calibration_report",
]


if __name__ == "__main__":  # synthetic: an over-confident detector, then calibrated
    rng = np.random.default_rng(1)
    # true prob of being correct is ~ s**1.8 (model is over-confident: high scores lie)
    s = rng.uniform(0, 1, 4000)
    y = (rng.uniform(0, 1, 4000) < s ** 1.8).astype(float)
    raw_rep = calibration_report(s, y)
    print(f"raw  ECE={raw_rep['ece']:.4f}  MCE={raw_rep['mce']:.4f}  "
          f"(n={raw_rep['n']}, {raw_rep['strategy']} bins={raw_rep['n_bins_realized']})")
    if raw_rep["warning"]:
        print(f"  ! {raw_rep['warning']}")
    cal = PlattCalibrator()
    cal.fit("firearm", s, y)
    s_cal = np.array([cal.calibrate("firearm", float(v)) for v in s])
    cal_rep = calibration_report(s_cal, y)
    print(f"cal  ECE={cal_rep['ece']:.4f}  MCE={cal_rep['mce']:.4f}   params={cal.params}")
    if cal_rep["warning"]:
        print(f"  ! {cal_rep['warning']}")
    print("reliability (calibrated, equal-mass):")
    for b in reliability_table(s_cal, y, n_bins=5, strategy="quantile"):
        if b.count:
            print(f"  [{b.lo:.1f},{b.hi:.1f}) n={b.count:<5} mean_score={b.mean_score:.3f} "
                  f"frac_pos={b.frac_positive:.3f}")
