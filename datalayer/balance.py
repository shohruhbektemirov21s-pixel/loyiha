"""Class imbalance analysis and oversampling strategy.

Real customs X-ray data is violently imbalanced: 95 %+ of scans are
benign. Within threat categories, narcotics dwarf firearms by scan
frequency (volume smuggling vs. single-item), and exotic categories
(EXPLOSIVE, CURRENCY) may appear only a handful of times per quarter.

This module deals with imbalance **at the data level** — not by
adjusting loss weights at training time (a downstream band-aid) but by:

1. **Tracking hard minimum requirements** per class. Below the minimum
   we do not start training that class — more data is the only fix.
2. **Reporting shortfall clearly** so "train a little more" arguments
   are defeated with numbers, not opinions.
3. **Computing inverse-frequency sample weights** so the GPU trainer
   can oversample rare classes without duplicating files.
4. **Recommending augmentation targets** for classes that are above
   minimum but still underrepresented.

Numbers used here are *starting minimums*, not ceilings.  They are
based on empirical detection literature for object-detection on X-ray:

  * A new class trained below ~300 examples will show poor recall on
    unseen orientations/packagings — the model pattern-matches the
    training artefacts, not the class.
  * Narcotics require 1 500+ due to extreme intra-class variation
    (powder, pressed tablets, liquid, concealment methods) and because
    a miss is catastrophically worse than a false alarm.
  * SEIZED feedback examples count double in practice because they are
    the highest-confidence ground truth.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Mapping

from contracts.v1 import ThreatCategory

log = logging.getLogger("xray.datalayer.balance")

# ---------------------------------------------------------------------------
# Hard minimums per class (positive labels needed before a category is
# considered trainable). Backed by detection literature + domain judgment.
# ---------------------------------------------------------------------------
MIN_LABELS_PER_CLASS: dict[ThreatCategory, int] = {
    ThreatCategory.NARCOTICS:          1_500,  # extreme intra-class variation; miss = catastrophe
    ThreatCategory.FIREARM:              800,  # shape-distinctive but many form factors
    ThreatCategory.BLADED_WEAPON:        600,  # thin profile; orientation-sensitive
    ThreatCategory.EXPLOSIVE:          1_200,  # improvised devices vary wildly
    ThreatCategory.CURRENCY:             500,  # visual patterns consistent; easier
    ThreatCategory.ORGANIC_ANOMALY:      400,  # catch-all; intentionally broad
    ThreatCategory.METALLIC_ANOMALY:     400,
    ThreatCategory.CONTRABAND_OTHER:     300,
    ThreatCategory.UNKNOWN:                0,  # not trained as a class
}

# Recommended augmentation target: 3× the minimum (so the augmented dataset
# is not dominated by original scans of one unusual packaging).
_AUGMENTATION_MULTIPLIER: float = 3.0


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ClassStats:
    category: ThreatCategory
    count: int
    minimum_required: int
    shortfall: int          # max(0, minimum - count)
    meets_minimum: bool
    sample_weight: float    # inverse-frequency weight for oversampling


@dataclass
class BalanceReport:
    """Full balance picture at a point in time."""

    total_positive_labels: int
    per_class: list[ClassStats] = field(default_factory=list)

    def untrainable_classes(self) -> list[ClassStats]:
        return [s for s in self.per_class if not s.meets_minimum and s.minimum_required > 0]

    def trainable_classes(self) -> list[ClassStats]:
        return [s for s in self.per_class if s.meets_minimum]

    def sample_weights(self) -> dict[str, float]:
        """Return {category_value: weight} for use as training sample weights."""
        return {s.category.value: s.sample_weight for s in self.per_class}

    def shortfall_table(self) -> str:
        """Human-readable table for stand-ups / data-collection targets."""
        lines = [
            f"{'Class':<22} {'Have':>6} {'Need':>6} {'Gap':>6} {'OK':>4}",
            "-" * 48,
        ]
        for s in sorted(self.per_class, key=lambda x: -x.shortfall):
            ok = "YES" if s.meets_minimum else "NO"
            lines.append(
                f"{s.category.value:<22} {s.count:>6} {s.minimum_required:>6} "
                f"{s.shortfall:>6} {ok:>4}"
            )
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main API
# ---------------------------------------------------------------------------
def compute_balance_report(counts: Mapping[str, int]) -> BalanceReport:
    """Build a ``BalanceReport`` from a {category_value: count} mapping.

    ``counts`` typically comes from ``LabelQueue.count_by_category()``.
    Categories not present in ``counts`` are treated as having zero labels.
    """
    all_categories = [c for c in ThreatCategory if c != ThreatCategory.UNKNOWN]
    total = sum(counts.values())

    per_class: list[ClassStats] = []
    for cat in all_categories:
        n = counts.get(cat.value, 0)
        minimum = MIN_LABELS_PER_CLASS.get(cat, 0)
        shortfall = max(0, minimum - n)
        meets = n >= minimum if minimum > 0 else True

        if total > 0 and n > 0:
            weight = (total / (len(all_categories) * n))
        else:
            weight = float(len(all_categories))  # maximum upweight for unseen class

        per_class.append(ClassStats(
            category=cat,
            count=n,
            minimum_required=minimum,
            shortfall=shortfall,
            meets_minimum=meets,
            sample_weight=round(weight, 4),
        ))

    report = BalanceReport(total_positive_labels=total, per_class=per_class)

    # Log untrainable classes loudly — this is the primary escalation signal.
    untrainable = report.untrainable_classes()
    if untrainable:
        names = ", ".join(f"{s.category.value}({s.count}/{s.minimum_required})" for s in untrainable)
        log.warning(
            "UNDER-MINIMUM classes — do NOT train these yet: %s. "
            "Collect more examples; do not adjust loss weights as a workaround.",
            names,
        )

    return report


def augmentation_target(stats: ClassStats) -> int:
    """How many augmented examples to generate for this class.

    Returns 0 if the class already meets its minimum ×3 target.
    """
    target = math.ceil(stats.minimum_required * _AUGMENTATION_MULTIPLIER)
    return max(0, target - stats.count)


__all__ = [
    "MIN_LABELS_PER_CLASS",
    "ClassStats",
    "BalanceReport",
    "compute_balance_report",
    "augmentation_target",
]
