"""Retrain trigger and job-spec emitter.

The active-learning loop closes when labeled data is automatically
converted into a retrain *job specification* that the GPU box can
consume. This module decides *when* retraining is warranted and *what*
to retrain on.

Trigger philosophy (conservative):
* Quantity gate: a minimum number of new labeled examples must have
  accumulated since the last retrain. Training on tiny deltas wastes
  GPU time and introduces noise from one operator's weekend shift.
* Minimum-per-class gate: at least one trainable class must have
  reached its per-class minimum (``balance.MIN_LABELS_PER_CLASS``).
  We never retrain if adding a class would train it below minimum —
  that produces a worse model than the previous checkpoint.
* Cooldown: a minimum wall-clock gap between retrains prevents
  runaway scheduling when many feedback events arrive at once.

The ``RetrainJobSpec`` is a JSON file dropped into a watched directory
on the GPU box. The GPU box's own scheduler (cron, Airflow, or a
simple file-watcher) picks it up and runs the training job. This
keeps the serving-layer API free of GPU dependencies.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Optional

from datalayer.balance import BalanceReport, compute_balance_report
from datalayer.labelstore import LabelQueue, LabelStatus
from datalayer.versioning import DatasetVersion

log = logging.getLogger("xray.datalayer.retrain")


# ---------------------------------------------------------------------------
# Trigger reasons
# ---------------------------------------------------------------------------
class TriggerReason(str, Enum):
    NEW_CLASS_REACHED_MINIMUM = "new_class_reached_minimum"
    DELTA_THRESHOLD_REACHED = "delta_threshold_reached"
    MANUAL_OVERRIDE = "manual_override"


@dataclass(frozen=True)
class RetrainThresholds:
    """Tunable thresholds. Inject at startup; do not hard-code in callers."""

    min_new_labels_since_last: int = 200      # new reviewed labels needed before considering a retrain
    min_cooldown_hours: float = 24.0          # minimum hours between consecutive retrains
    min_total_labels: int = 500               # absolute floor — don't retrain on a near-empty dataset


# ---------------------------------------------------------------------------
# Job spec
# ---------------------------------------------------------------------------
@dataclass
class RetrainJobSpec:
    """Everything the GPU box needs to launch a training run.

    Written as a JSON file; the GPU training harness reads it.
    Immutable once emitted — a new retrain gets a new spec_id.
    """

    spec_id: str                                 # UUID string
    dataset_version_tag: str                     # which snapshot to train on
    triggered_by: TriggerReason
    trainable_classes: list[str]                 # category values that meet minimum
    sample_weights: dict[str, float]             # per-class oversampling weights
    total_labels: int
    per_class_counts: dict[str, int]
    created_at: str                              # ISO-8601 UTC

    # Advisory fields — the GPU harness may use or ignore them.
    suggested_epochs: int = 50
    suggested_imgsz: int = 640
    base_checkpoint: str | None = None          # previous model weights path (fine-tuning)

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(asdict(self), indent=indent, ensure_ascii=False)

    @classmethod
    def from_json(cls, raw: str) -> "RetrainJobSpec":
        return cls(**json.loads(raw))


# ---------------------------------------------------------------------------
# Scheduler
# ---------------------------------------------------------------------------
class RetrainScheduler:
    """Checks whether a retrain should be triggered and emits the job spec.

    ``job_dir`` is a directory the GPU box watches.  When a ``.json``
    file appears there, the GPU box starts a training run.  This box
    only writes; the GPU box only reads — the air gap is preserved.
    """

    def __init__(
        self,
        queue: LabelQueue,
        job_dir: str | Path,
        thresholds: RetrainThresholds | None = None,
        *,
        last_retrain_at: datetime | None = None,
        labels_at_last_retrain: int = 0,
        base_checkpoint: str | None = None,
    ) -> None:
        self._queue = queue
        self._job_dir = Path(job_dir)
        self._job_dir.mkdir(parents=True, exist_ok=True)
        self._thresholds = thresholds or RetrainThresholds()
        self._last_retrain_at: datetime | None = last_retrain_at
        self._labels_at_last_retrain: int = labels_at_last_retrain
        self._base_checkpoint = base_checkpoint

    # -- public API --------------------------------------------------------
    def check_and_emit(
        self,
        version: DatasetVersion | None = None,
        *,
        force: bool = False,
    ) -> RetrainJobSpec | None:
        """Evaluate trigger conditions and optionally emit a job spec.

        Returns the emitted ``RetrainJobSpec``, or ``None`` if conditions
        are not yet met.  Pass ``force=True`` to bypass threshold checks
        (e.g. for a manual admin trigger).
        """
        counts = self._queue.count_by_category(LabelStatus.REVIEWED)
        balance = compute_balance_report(counts)

        if force:
            reason = TriggerReason.MANUAL_OVERRIDE
        else:
            reason = self._evaluate_triggers(balance, counts)
            if reason is None:
                return None

        trainable = [s.category.value for s in balance.trainable_classes()]
        if not trainable and not force:
            log.warning(
                "Retrain triggered but no class meets minimum — aborting. "
                "Collect more labeled examples before retraining."
            )
            return None

        spec = RetrainJobSpec(
            spec_id=str(uuid.uuid4()),
            dataset_version_tag=version.tag if version else "unversioned",
            triggered_by=reason,
            trainable_classes=trainable,
            sample_weights=balance.sample_weights(),
            total_labels=balance.total_positive_labels,
            per_class_counts={s.category.value: s.count for s in balance.per_class},
            created_at=datetime.now(timezone.utc).isoformat(),
            base_checkpoint=self._base_checkpoint,
        )
        self._write_spec(spec)
        self._last_retrain_at = datetime.now(timezone.utc)
        self._labels_at_last_retrain = balance.total_positive_labels
        return spec

    # -- helpers -----------------------------------------------------------
    def _evaluate_triggers(
        self,
        balance: BalanceReport,
        counts: dict[str, int],
    ) -> TriggerReason | None:
        t = self._thresholds
        total = balance.total_positive_labels

        if total < t.min_total_labels:
            log.info(
                "Retrain deferred: total=%d < floor=%d. Collect %d more labels.",
                total,
                t.min_total_labels,
                t.min_total_labels - total,
            )
            return None

        if self._last_retrain_at is not None:
            elapsed = datetime.now(timezone.utc) - self._last_retrain_at
            cooldown = timedelta(hours=t.min_cooldown_hours)
            if elapsed < cooldown:
                remaining = (cooldown - elapsed).total_seconds() / 3600
                log.info("Retrain deferred: cooldown %.1f h remaining.", remaining)
                return None

        new_since_last = total - self._labels_at_last_retrain

        previously_untrainable = {
            s.category for s in balance.per_class if not s.meets_minimum
        }
        newly_trainable = [
            s for s in balance.trainable_classes()
            if s.category in previously_untrainable
        ]
        if newly_trainable:
            log.info(
                "Trigger: new class(es) crossed minimum — %s",
                [s.category.value for s in newly_trainable],
            )
            return TriggerReason.NEW_CLASS_REACHED_MINIMUM

        if new_since_last >= t.min_new_labels_since_last:
            log.info(
                "Trigger: %d new labels since last retrain (threshold=%d).",
                new_since_last,
                t.min_new_labels_since_last,
            )
            return TriggerReason.DELTA_THRESHOLD_REACHED

        log.info(
            "Retrain deferred: new=%d/%d labels since last run.",
            new_since_last,
            t.min_new_labels_since_last,
        )
        return None

    def _write_spec(self, spec: RetrainJobSpec) -> Path:
        path = self._job_dir / f"retrain_{spec.spec_id[:8]}.json"
        path.write_text(spec.to_json(), encoding="utf-8")
        log.info(
            "RetrainJobSpec emitted: %s reason=%s classes=%s total_labels=%d",
            path.name,
            spec.triggered_by.value,
            spec.trainable_classes,
            spec.total_labels,
        )
        return path


__all__ = [
    "TriggerReason",
    "RetrainThresholds",
    "RetrainJobSpec",
    "RetrainScheduler",
]
