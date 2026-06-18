"""Hop 4 contract — Operator console -> Data layer (the active-learning loop).

This is the hop that closes the loop. The first three hops carry a scan *forward*
to a verdict; this one carries the operator's **ground truth** *back* so it
becomes labeled training data. Without it the model can never improve from
operations — every scan is forgotten the moment the operator decides.

The operator is the gold annotator at the coal face. Three things they tell us
are each worth labeled examples we otherwise have no source for:

* **Confirmed** detections  -> verified positive labels (the model was right).
* **Rejected** detections   -> hard negatives (the model cried wolf; teach it not to).
* **Missed** regions        -> the operator drew a box the model never produced.
  These are *false negatives* — the single most valuable signal we collect,
  because a missed weapon is the failure this whole system exists to prevent,
  and real positives for rare classes are exactly what we cannot buy or scrape.

Design, consistent with the rest of the spine:

* The message **embeds the originating ``DetectionResult``**, exactly as
  ``VerdictRequest`` does. That makes it *self-validating*: the operator cannot
  review a detection that was never produced, and a missed-region box is checked
  against the real frame bounds — no phantom labels can enter the dataset.
* Strict, frozen, fail-closed. A malformed feedback message is rejected at the
  wire, before it can poison the label store.
* ``operator_id`` is **required** here. On the acquisition hop the operator is
  audit-only; here they ARE the decision-maker and the author of the label, so
  every label is attributable (who said this, when) — essential for resolving
  annotator disagreement later.
"""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import Field, model_validator

from .common import (
    SCHEMA_VERSION,
    DetectionId,
    FeedbackId,
    FrameId,
    OperatorId,
    PixelBox,
    ScanId,
    StrictModel,
    ThreatCategory,
    VerdictId,
    datetime,
)
from .detection import DetectionResult


class DetectionJudgement(str, Enum):
    """The operator's verdict on one detection the model produced."""

    CONFIRMED = "confirmed"        # true positive: box + category were right
    REJECTED = "rejected"          # false positive: nothing of interest there
    RECLASSIFIED = "reclassified"  # right location, wrong category (-> corrected_category)
    UNREVIEWED = "unreviewed"      # operator did not assess this box (NOT a label)


class OperatorOutcome(str, Enum):
    """The physical action taken on the scanned subject.

    This is the *strength* of the ground-truth signal, not just metadata: a
    label backed by a physical SEIZED is as certain as labels get, whereas a
    CLEARED-on-sight scan is a weaker (still useful) negative.
    """

    CLEARED = "cleared"        # passed without physical inspection
    INSPECTED = "inspected"    # physically opened/searched, nothing actioned
    SEIZED = "seized"          # contraband found and seized — strongest positive ground truth
    ESCALATED = "escalated"    # handed to another authority; outcome pending


class DetectionReview(StrictModel):
    """The operator's judgement on one model-produced detection, keyed by id."""

    detection_id: DetectionId = Field(description="Must reference a detection in the embedded result.")
    judgement: DetectionJudgement
    corrected_category: ThreatCategory | None = Field(
        default=None,
        description="Required iff judgement is RECLASSIFIED; forbidden otherwise.",
    )
    note_uz: str | None = Field(default=None, max_length=2000, description="Optional operator note, operator's language.")

    @model_validator(mode="after")
    def _category_matches_judgement(self) -> "DetectionReview":
        if self.judgement == DetectionJudgement.RECLASSIFIED and self.corrected_category is None:
            raise ValueError("RECLASSIFIED review must carry a corrected_category.")
        if self.judgement != DetectionJudgement.RECLASSIFIED and self.corrected_category is not None:
            raise ValueError("corrected_category is only valid for a RECLASSIFIED review.")
        return self


class OperatorAnnotation(StrictModel):
    """A region the operator drew that the detector MISSED (a false negative).

    The gold of the loop. Coordinates are in the pixel frame of reference of the
    named frame, identical to a ``Detection``'s box, so this slots straight into
    the training set as a positive example once an annotator confirms it.
    """

    frame_id: FrameId = Field(description="Which frame in the embedded result this region belongs to.")
    box: PixelBox
    category: ThreatCategory = Field(description="What the operator says is there.")
    note_uz: str | None = Field(default=None, max_length=2000)


class OperatorFeedback(StrictModel):
    """Operator ground truth on a scan, fed back to become labeled data.

    Produced by: the operator console.  Consumed by: ``POST /v1/feedback``
    (the data layer). Embeds the ``DetectionResult`` it reacts to so every claim
    is checkable against what the model actually produced.

    An empty review/missed set is **valid and meaningful**: a CLEARED scan with
    no findings and nothing missed is a confirmed true-negative — a real label
    that helps drive down the false-positive rate.
    """

    schema_version: Literal["1.0"] = SCHEMA_VERSION
    feedback_id: FeedbackId
    scan_id: ScanId
    verdict_id: VerdictId | None = Field(
        default=None,
        description="The verdict the operator was reacting to, if any. Audit linkage only.",
    )
    operator_id: OperatorId = Field(description="The decision-maker and author of this label. Required — labels are attributable.")

    detection: DetectionResult = Field(description="The findings the operator reviewed. Embedded so feedback self-validates.")
    outcome: OperatorOutcome
    reviews: list[DetectionReview] = Field(
        default_factory=list,
        description="Per-detection judgements. At most one per detection_id.",
    )
    missed: list[OperatorAnnotation] = Field(
        default_factory=list,
        description="Regions the operator flagged that the detector did not produce (false negatives).",
    )

    decided_at: datetime = Field(description="When the operator made the decision (timezone-aware).")
    emitted_at: datetime = Field(description="When the console emitted this feedback (timezone-aware).")
    notes_uz: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def _check_consistency(self) -> "OperatorFeedback":
        # The embedded result must be about the same scan (fail-closed).
        if self.detection.scan_id != self.scan_id:
            raise ValueError("OperatorFeedback.scan_id must equal the embedded detection.scan_id.")

        known_detection_ids = {d.detection_id for d in self.detection.detections}
        frames_by_id = {f.frame_id: f for f in self.detection.frames}

        # Reviews: every review targets a real detection, at most once.
        seen: set = set()
        for r in self.reviews:
            if r.detection_id not in known_detection_ids:
                raise ValueError(f"Review references unknown detection_id {r.detection_id} (not in the embedded result).")
            if r.detection_id in seen:
                raise ValueError(f"Duplicate review for detection_id {r.detection_id}.")
            seen.add(r.detection_id)

        # Missed regions: must land in a real frame and fit its bounds — no phantom labels.
        for m in self.missed:
            frame = frames_by_id.get(m.frame_id)
            if frame is None:
                raise ValueError(f"Missed region references unknown frame_id {m.frame_id!r}.")
            if not m.box.fits_within(frame):
                raise ValueError(f"Missed region box exceeds frame {m.frame_id!r} bounds.")

        return self

    @property
    def n_gold_labels(self) -> int:
        """How many usable training labels this feedback yields.

        Confirmed/reclassified detections are verified positives; missed regions
        are new positives. Rejections are hard negatives — also labels, but
        counted separately because they don't add a positive box. (A clean
        true-negative scan yields 0 here yet is still valuable feedback.)
        """
        positive_reviews = sum(
            1 for r in self.reviews
            if r.judgement in (DetectionJudgement.CONFIRMED, DetectionJudgement.RECLASSIFIED)
        )
        return positive_reviews + len(self.missed)

    @property
    def n_hard_negatives(self) -> int:
        """Rejected detections — false positives to train against."""
        return sum(1 for r in self.reviews if r.judgement == DetectionJudgement.REJECTED)

    @property
    def is_false_negative_report(self) -> bool:
        """True when the operator caught something the model missed. The signal we prize most."""
        return bool(self.missed)


class FeedbackReceipt(StrictModel):
    """The data layer's acknowledgement that feedback was captured.

    Produced by: ``POST /v1/feedback``.  Consumed by: the operator console.
    Confirms the feedback landed durably and reports how many label candidates
    it seeded into the active-learning queue — the console can show the operator
    that their correction was banked, not silently dropped.
    """

    schema_version: Literal["1.0"] = SCHEMA_VERSION
    feedback_id: FeedbackId
    scan_id: ScanId
    labels_queued: int = Field(ge=0, description="Gold label candidates extracted into the annotation/retrain queue.")
    hard_negatives_queued: int = Field(ge=0, default=0, description="Rejected detections banked as hard negatives.")
    accepted_at: datetime = Field(description="When the data layer durably persisted this feedback.")
    dataset_target: str | None = Field(
        default=None,
        max_length=256,
        description="Where the candidates were routed, e.g. 'active-learning/pending'. Opaque to the contract.",
    )


__all__ = [
    "DetectionJudgement",
    "OperatorOutcome",
    "DetectionReview",
    "OperatorAnnotation",
    "OperatorFeedback",
    "FeedbackReceipt",
]
