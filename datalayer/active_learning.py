"""Active-learning loop coordinator — the system's self-improvement engine.

This module is the single entry point that closes the loop:

    OperatorFeedback (Hop 4)
        → quality gate     (reject malformed/ambiguous labels before they enter)
        → label queue      (durable JSONL, attributed, content-addressed)
        → balance check    (how many more examples does each class need?)
        → retrain trigger  (emit job spec when thresholds are crossed)
        → FeedbackReceipt  (tell the console what was banked)

Architecture rules that must not be broken:
* **No label enters the queue without passing the quality gate.**
  A rejected label is logged with its reason; the operator can correct
  and resubmit.
* **The loop is synchronous at the API hop** (the receipt is returned
  in the same request). Retrain scheduling is a side-effect: it writes
  a file; the GPU box picks it up asynchronously. The API never blocks
  on GPU resources.
* **No scan bytes flow through this module.** This layer works with
  ``StorageRef`` handles and label metadata only. The bytes stay in
  the ``SecureImageStore``.
* **Thread-safe.** The queue's file lock + Python's GIL cover the
  common case (one API worker process). For multi-process deployments
  the file lock in ``LabelQueue`` is sufficient for POSIX filesystems.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from contracts.v1.feedback import FeedbackReceipt, OperatorFeedback
from datalayer.balance import BalanceReport, compute_balance_report
from datalayer.labelstore import LabelQueue, LabelStatus, extract_label_entries
from datalayer.quality import LabelQualityGate, QualityReport
from datalayer.retrain import RetrainJobSpec, RetrainScheduler, RetrainThresholds
from datalayer.versioning import VersioningManager

log = logging.getLogger("xray.datalayer.active_learning")


# ---------------------------------------------------------------------------
# Per-feedback processing result (internal; not on the wire)
# ---------------------------------------------------------------------------
@dataclass
class ProcessingResult:
    feedback_id: str
    total_candidates: int
    passing_count: int
    rejected_count: int
    quality_reports: list[QualityReport]
    retrain_spec: RetrainJobSpec | None
    receipt: FeedbackReceipt


# ---------------------------------------------------------------------------
# Loop
# ---------------------------------------------------------------------------
class ActiveLearningLoop:
    """Coordinates the full Hop-4 processing pipeline.

    Constructed once at application startup; shared across requests.
    All collaborators are injected so each can be tested in isolation.
    """

    def __init__(
        self,
        queue: LabelQueue,
        gate: LabelQualityGate,
        scheduler: RetrainScheduler,
        versioning: VersioningManager,
        *,
        dataset_target: str = "active-learning/pending",
    ) -> None:
        self._queue = queue
        self._gate = gate
        self._scheduler = scheduler
        self._versioning = versioning
        self._dataset_target = dataset_target

    # -- main entry point --------------------------------------------------
    def process_feedback(self, feedback: OperatorFeedback) -> ProcessingResult:
        """Run the full pipeline for one ``OperatorFeedback``.

        Steps:
          1. Extract label candidates from the feedback message.
          2. Run quality gate — reject failing entries.
          3. Enqueue all passing entries.
          4. Check retrain trigger (non-blocking; emits a job-spec file).
          5. Build and return the ``FeedbackReceipt``.
        """
        accepted_at = datetime.now(timezone.utc)

        # 1. Extract.
        candidates = extract_label_entries(feedback)
        log.info(
            "feedback_id=%s scan_id=%s candidates=%d",
            feedback.feedback_id,
            feedback.scan_id,
            len(candidates),
        )

        # 2. Quality gate.
        passing, reports = self._gate.filter_passing(candidates)
        rejected_count = len(candidates) - len(passing)
        if rejected_count:
            log.warning(
                "feedback_id=%s rejected=%d/%d candidates by quality gate",
                feedback.feedback_id,
                rejected_count,
                len(candidates),
            )

        # 3. Enqueue.
        enqueued = self._queue.enqueue_many(passing)

        # 4. Retrain trigger (best-effort; errors here must not fail the receipt).
        retrain_spec: RetrainJobSpec | None = None
        try:
            retrain_spec = self._scheduler.check_and_emit()
        except Exception as exc:
            log.error("Retrain scheduler error (non-fatal): %s", exc, exc_info=True)

        # 5. Receipt.
        positive_count = sum(
            1 for e in passing
            if e.source.value in (
                "operator_confirmed",
                "operator_reclassified",
                "operator_missed",
            )
        )
        hard_negative_count = sum(
            1 for e in passing if e.source.value == "hard_negative"
        )

        receipt = FeedbackReceipt(
            feedback_id=feedback.feedback_id,
            scan_id=feedback.scan_id,
            labels_queued=positive_count,
            hard_negatives_queued=hard_negative_count,
            accepted_at=accepted_at,
            dataset_target=self._dataset_target,
        )

        log.info(
            "feedback_id=%s queued=%d hard_neg=%d retrain_triggered=%s",
            feedback.feedback_id,
            positive_count,
            hard_negative_count,
            retrain_spec is not None,
        )

        return ProcessingResult(
            feedback_id=str(feedback.feedback_id),
            total_candidates=len(candidates),
            passing_count=len(passing),
            rejected_count=rejected_count,
            quality_reports=reports,
            retrain_spec=retrain_spec,
            receipt=receipt,
        )

    # -- balance snapshot (call from a scheduled job or admin endpoint) ----
    def balance_report(self) -> BalanceReport:
        """Current class balance across all REVIEWED labels."""
        counts = self._queue.count_by_category(LabelStatus.REVIEWED)
        return compute_balance_report(counts)

    def queue_stats(self) -> dict:
        return self._queue.count_by_status()


# ---------------------------------------------------------------------------
# Factory — builds the loop from paths (used in the composition root)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ActiveLearningConfig:
    """Paths and thresholds injected at startup."""

    queue_dir: str
    version_dir: str
    job_dir: str
    queue_name: str = "active_learning"
    dataset_target: str = "active-learning/pending"
    retrain_thresholds: RetrainThresholds = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.retrain_thresholds is None:
            object.__setattr__(self, "retrain_thresholds", RetrainThresholds())


def build_active_learning_loop(config: ActiveLearningConfig) -> ActiveLearningLoop:
    """Composition root for the active-learning subsystem."""
    queue = LabelQueue(config.queue_dir, config.queue_name)
    gate = LabelQualityGate()
    versioning = VersioningManager(config.version_dir)
    scheduler = RetrainScheduler(
        queue=queue,
        job_dir=config.job_dir,
        thresholds=config.retrain_thresholds,
    )
    return ActiveLearningLoop(
        queue=queue,
        gate=gate,
        scheduler=scheduler,
        versioning=versioning,
        dataset_target=config.dataset_target,
    )


__all__ = [
    "ProcessingResult",
    "ActiveLearningLoop",
    "ActiveLearningConfig",
    "build_active_learning_loop",
]
