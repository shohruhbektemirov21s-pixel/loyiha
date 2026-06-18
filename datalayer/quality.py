"""Label quality gate — runs before any candidate enters the dataset.

Inconsistent labels poison the model. This gate is the last line of
defense before a ``LabelEntry`` is queued. It is pure Python (no ML
dependencies) so it runs on every box.

Gate philosophy:
* Every check is **explicit and named** — when a label is rejected the
  operator / data engineer sees exactly which rule it violated.
* Checks are **conservative**: reject on doubt. A missing label is a
  smaller problem than a wrong label.
* Rules are **per-source** where appropriate: missed regions (false-
  negative catches) get slightly more latitude on box size because the
  operator is drawing from memory with a mouse, not a calibrated tool.
* No check touches model weights or probabilities — purely structural
  and geometric.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable

from contracts.v1 import ThreatCategory
from datalayer.labelstore import LabelEntry, LabelSource

log = logging.getLogger("xray.datalayer.quality")


# ---------------------------------------------------------------------------
# Per-class minimum box area (pixels²)
# ---------------------------------------------------------------------------
# These are intentionally conservative lower bounds.  A 32×32 box is 1 024 px²;
# below that the annotator likely mis-clicked. Raise thresholds as scanner
# resolution data accumulates.
_MIN_BOX_AREA: dict[ThreatCategory, int] = {
    ThreatCategory.NARCOTICS:          1_024,   # 32×32 px
    ThreatCategory.FIREARM:            2_025,   # 45×45 px — firearms are recognizable shapes
    ThreatCategory.BLADED_WEAPON:      900,     # 30×30 px — thin objects
    ThreatCategory.EXPLOSIVE:          1_024,
    ThreatCategory.CURRENCY:           625,     # 25×25 px — small bundles
    ThreatCategory.ORGANIC_ANOMALY:    4_096,   # 64×64 px — amorphous mass
    ThreatCategory.METALLIC_ANOMALY:   2_025,
    ThreatCategory.CONTRABAND_OTHER:   625,
    ThreatCategory.UNKNOWN:            625,
}

_DEFAULT_MIN_BOX_AREA = 625  # fallback for any future category

# Maximum aspect ratio (width/height or height/width, whichever > 1).
# A box with ratio > 20:1 is almost certainly a mis-click or annotation error.
_MAX_ASPECT_RATIO: float = 20.0

# Minimum frame dimension we trust a box to be sane within.
_MIN_FRAME_DIM: int = 32


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------
class CheckResult(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    SKIP = "skip"       # check not applicable to this entry (e.g. no-box check on positive)


@dataclass(frozen=True)
class CheckOutcome:
    name: str
    result: CheckResult
    reason: str = ""


@dataclass
class QualityReport:
    label_id: str
    passed: bool
    checks: list[CheckOutcome] = field(default_factory=list)

    def failed_checks(self) -> list[CheckOutcome]:
        return [c for c in self.checks if c.result == CheckResult.FAIL]

    def summary(self) -> str:
        if self.passed:
            return f"[PASS] label={self.label_id}"
        failures = "; ".join(f"{c.name}: {c.reason}" for c in self.failed_checks())
        return f"[FAIL] label={self.label_id} — {failures}"


# ---------------------------------------------------------------------------
# Individual checks (each is a pure function: LabelEntry -> CheckOutcome)
# ---------------------------------------------------------------------------
def _check_operator_id(entry: LabelEntry) -> CheckOutcome:
    if not entry.operator_id or not entry.operator_id.strip():
        return CheckOutcome("operator_id_present", CheckResult.FAIL, "operator_id is blank — label is unattributable.")
    return CheckOutcome("operator_id_present", CheckResult.PASS)


def _check_category_not_unknown(entry: LabelEntry) -> CheckOutcome:
    if entry.source not in (LabelSource.TRUE_NEGATIVE_SCAN, LabelSource.HARD_NEGATIVE):
        if entry.category == ThreatCategory.UNKNOWN:
            return CheckOutcome(
                "category_not_unknown",
                CheckResult.FAIL,
                "Positive label must have a specific category, not UNKNOWN.",
            )
    return CheckOutcome("category_not_unknown", CheckResult.PASS)


def _check_box_present_for_positive(entry: LabelEntry) -> CheckOutcome:
    if entry.source in (LabelSource.OPERATOR_CONFIRMED,
                        LabelSource.OPERATOR_RECLASSIFIED,
                        LabelSource.OPERATOR_MISSED):
        if not entry.has_box:
            return CheckOutcome(
                "box_present_for_positive",
                CheckResult.FAIL,
                f"Source {entry.source.value} requires a bounding box.",
            )
    return CheckOutcome("box_present_for_positive", CheckResult.PASS)


def _check_box_area(entry: LabelEntry) -> CheckOutcome:
    if not entry.has_box:
        return CheckOutcome("box_area", CheckResult.SKIP, "No box — skip area check.")
    area = (entry.box_w or 0) * (entry.box_h or 0)
    minimum = _MIN_BOX_AREA.get(entry.category, _DEFAULT_MIN_BOX_AREA)
    if area < minimum:
        return CheckOutcome(
            "box_area",
            CheckResult.FAIL,
            f"Box area {area} px² < minimum {minimum} px² for {entry.category.value}.",
        )
    return CheckOutcome("box_area", CheckResult.PASS)


def _check_box_aspect_ratio(entry: LabelEntry) -> CheckOutcome:
    if not entry.has_box:
        return CheckOutcome("box_aspect_ratio", CheckResult.SKIP)
    w, h = entry.box_w or 1, entry.box_h or 1
    ratio = max(w, h) / max(min(w, h), 1)
    if ratio > _MAX_ASPECT_RATIO:
        return CheckOutcome(
            "box_aspect_ratio",
            CheckResult.FAIL,
            f"Aspect ratio {ratio:.1f}:1 exceeds maximum {_MAX_ASPECT_RATIO}:1 — probable mis-click.",
        )
    return CheckOutcome("box_aspect_ratio", CheckResult.PASS)


def _check_box_positive_dims(entry: LabelEntry) -> CheckOutcome:
    if not entry.has_box:
        return CheckOutcome("box_positive_dims", CheckResult.SKIP)
    if (entry.box_w or 0) <= 0 or (entry.box_h or 0) <= 0:
        return CheckOutcome(
            "box_positive_dims",
            CheckResult.FAIL,
            f"Box w={entry.box_w} h={entry.box_h} — dimensions must be positive.",
        )
    return CheckOutcome("box_positive_dims", CheckResult.PASS)


def _check_scan_sha256_format(entry: LabelEntry) -> CheckOutcome:
    if not entry.scan_sha256 or len(entry.scan_sha256) != 64:
        return CheckOutcome(
            "scan_sha256_format",
            CheckResult.FAIL,
            f"scan_sha256 {entry.scan_sha256!r} is not a 64-char hex string.",
        )
    return CheckOutcome("scan_sha256_format", CheckResult.PASS)


def _check_seizure_needs_box(entry: LabelEntry) -> CheckOutcome:
    """A SEIZED scan without any positive box is suspicious — flag for review."""
    from contracts.v1.feedback import OperatorOutcome
    if (
        entry.operator_outcome == OperatorOutcome.SEIZED
        and entry.source in (LabelSource.TRUE_NEGATIVE_SCAN, LabelSource.HARD_NEGATIVE)
        and not entry.has_box
    ):
        return CheckOutcome(
            "seizure_needs_box",
            CheckResult.FAIL,
            "SEIZED outcome paired with no positive box — at least one positive label expected.",
        )
    return CheckOutcome("seizure_needs_box", CheckResult.PASS)


# ---------------------------------------------------------------------------
# Gate
# ---------------------------------------------------------------------------
_ALL_CHECKS: list[Callable[[LabelEntry], CheckOutcome]] = [
    _check_operator_id,
    _check_category_not_unknown,
    _check_box_present_for_positive,
    _check_box_area,
    _check_box_aspect_ratio,
    _check_box_positive_dims,
    _check_scan_sha256_format,
    _check_seizure_needs_box,
]


class LabelQualityGate:
    """Runs all checks and returns a ``QualityReport``.

    A label with any FAIL result is rejected — it must not enter the
    queue. The gate is intentionally strict: a rejected label can be
    corrected and resubmitted; a bad label that slipped through
    corrupts the model silently.
    """

    def __call__(self, entry: LabelEntry) -> QualityReport:
        outcomes = [check(entry) for check in _ALL_CHECKS]
        passed = all(o.result != CheckResult.FAIL for o in outcomes)
        report = QualityReport(label_id=entry.label_id, passed=passed, checks=outcomes)
        if not passed:
            log.warning("quality gate FAIL %s", report.summary())
        return report

    def filter_passing(self, entries: list[LabelEntry]) -> tuple[list[LabelEntry], list[QualityReport]]:
        """Return (passing_entries, all_reports).  Logs rejections."""
        passing: list[LabelEntry] = []
        reports: list[QualityReport] = []
        for entry in entries:
            report = self(entry)
            reports.append(report)
            if report.passed:
                passing.append(entry)
        return passing, reports


__all__ = [
    "CheckResult",
    "CheckOutcome",
    "QualityReport",
    "LabelQualityGate",
]
