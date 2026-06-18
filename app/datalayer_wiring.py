"""Data-layer composition root — wires the ActiveLearningLoop into the API.

Called from ``app.main._wire_datalayer()`` during the FastAPI lifespan.
Kept in its own module so the heavy datalayer imports (labelstore, quality,
retrain, versioning) are only loaded on boxes where the data-layer is enabled
(``XRAY_DATALAYER_ENABLED=1``).

The ``DataLayerFeedbackSink`` is the adapter that bridges the async FastAPI
dependency protocol to the synchronous ``ActiveLearningLoop.process_feedback()``.
It runs the blocking processing call in the default thread-pool executor so the
async event loop is never stalled.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from contracts.v1.feedback import FeedbackReceipt, OperatorFeedback

log = logging.getLogger("xray.app.datalayer")


# ---------------------------------------------------------------------------
# Async adapter — satisfies app.deps.FeedbackSink protocol
# ---------------------------------------------------------------------------
class DataLayerFeedbackSink:
    """Async wrapper around the synchronous ActiveLearningLoop."""

    def __init__(self, loop) -> None:  # loop: ActiveLearningLoop
        self._loop = loop

    async def record(self, feedback: OperatorFeedback) -> FeedbackReceipt:
        """Run blocking label-processing in the thread-pool, return receipt."""
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None,
            self._loop.process_feedback,
            feedback,
        )
        return result.receipt


# ---------------------------------------------------------------------------
# Factory — builds sink from Settings and wires it into the app
# ---------------------------------------------------------------------------
def build_feedback_sink(
    queue_dir: str,
    version_dir: str,
    job_dir: str,
    *,
    dataset_target: str = "active-learning/pending",
) -> "DataLayerFeedbackSink":
    """Build the wired-up FeedbackSink from directory paths.

    Directories are created automatically if they do not exist.
    """
    from datalayer.active_learning import ActiveLearningConfig, build_active_learning_loop

    for d in (queue_dir, version_dir, job_dir):
        Path(d).mkdir(parents=True, exist_ok=True)

    cfg = ActiveLearningConfig(
        queue_dir=queue_dir,
        version_dir=version_dir,
        job_dir=job_dir,
        dataset_target=dataset_target,
    )
    al_loop = build_active_learning_loop(cfg)

    log.info(
        "ActiveLearningLoop wired: queue=%s versions=%s jobs=%s target=%s",
        queue_dir, version_dir, job_dir, dataset_target,
    )
    return DataLayerFeedbackSink(al_loop)


__all__ = ["DataLayerFeedbackSink", "build_feedback_sink"]
