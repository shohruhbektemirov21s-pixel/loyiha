"""`/v1/feedback` — Hop 4 serving boundary (Operator console -> Data layer).

This is the endpoint that closes the active-learning loop. The operator's
ground truth (confirmations, rejections, missed regions) arrives here and
is routed into the label queue via the ``FeedbackSink`` seam.

The response is a ``FeedbackReceipt``: the operator console can show the
operator that their correction was banked, not silently dropped. If the
loop is not wired (FeedbackSink stub), it returns 501 — it never pretends
to have stored data it hasn't.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, status

from contracts.v1.feedback import FeedbackReceipt, OperatorFeedback

from app.deps import (
    AuditSink,
    FeedbackSink,
    ServiceNotImplemented,
    ScanStore,
    provide_audit_sink,
    provide_feedback_sink,
    provide_scan_store,
)
from app.errors import not_implemented

router = APIRouter(tags=["feedback"])


@router.post(
    "/feedback",
    response_model=FeedbackReceipt,
    status_code=status.HTTP_200_OK,
    summary="Bank operator feedback as labeled training data (active-learning loop)",
    responses={501: {"description": "Data layer / active-learning loop not wired in yet."}},
)
async def submit_feedback(
    feedback: OperatorFeedback,
    sink: FeedbackSink = Depends(provide_feedback_sink),
    audit: AuditSink = Depends(provide_audit_sink),
    store: ScanStore = Depends(provide_scan_store),
) -> FeedbackReceipt:
    """Close the active-learning loop for one scan.

    Accepts an operator's ground-truth judgements and routes them into
    the label queue. Returns a receipt confirming how many label
    candidates were banked.

    Signal value:
    - ``labels_queued``: confirmed + reclassified detections + missed regions
    - ``hard_negatives_queued``: rejected (false-positive) detections
    An empty receipt (both zero) from a CLEARED scan is valid — it is a
    confirmed true-negative and is still stored.
    """
    await audit.record(
        "feedback.received",
        scan_id=feedback.scan_id,
        operator_id=feedback.operator_id,
        outcome=feedback.outcome.value,
        n_reviews=len(feedback.reviews),
        n_missed=len(feedback.missed),
    )
    try:
        receipt = await sink.record(feedback)
    except ServiceNotImplemented as exc:
        raise not_implemented(exc)

    await store.record_feedback(feedback, receipt)
    await audit.record(
        "feedback.banked",
        scan_id=feedback.scan_id,
        operator_id=str(feedback.operator_id),
        feedback_id=str(feedback.feedback_id),
        labels_queued=receipt.labels_queued,
        hard_negatives_queued=receipt.hard_negatives_queued,
        dataset_target=receipt.dataset_target,
    )
    # Notify connected supervisors that a decision was made.
    from app.api.v1.ws import notify_scan_decided
    scan_row = await store.get_scan(feedback.scan_id)
    lane_id = scan_row.lane_id if scan_row else None
    await notify_scan_decided(
        scan_id=str(feedback.scan_id),
        lane_id=lane_id,
        outcome=feedback.outcome.value,
        operator_id=str(feedback.operator_id),
    )
    return receipt
