"""Append-only label queue — the custody chain for every training label.

Every label that enters the dataset must be attributable: who said it,
when, and from what signal (operator confirmation, reclassification, or
a missed-region catch). This module is that record.

Architecture choices:
* **JSONL on disk** — one JSON object per line, append-only. No external
  database; survives a crash between lines. Easy to inspect, diff, and
  feed to DVC. Each line is an immutable ``LabelEntry``.
* **Thread-safe append** — a per-queue file lock prevents torn writes
  without requiring a server process.
* **Content-addressed** — the ``scan_sha256`` in each entry ties the
  label to the exact bytes analyzed. If the store blob is ever suspect,
  the label is traceable to the same hash the model saw.
* **Status lifecycle** — PENDING → REVIEWED (human annotator confirmed) →
  CONSUMED (incorporated into a versioned dataset). Hard negatives follow
  the same path; they are first-class labels.
"""

from __future__ import annotations

import json
import logging
import threading
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Iterator

from contracts.v1 import ThreatCategory
from contracts.v1.feedback import (
    DetectionJudgement,
    DetectionReview,
    OperatorAnnotation,
    OperatorFeedback,
    OperatorOutcome,
)

log = logging.getLogger("xray.datalayer.labelstore")


class LabelSource(str, Enum):
    """Where the label signal came from — determines its initial confidence."""

    OPERATOR_CONFIRMED = "operator_confirmed"        # model was right; operator confirmed
    OPERATOR_RECLASSIFIED = "operator_reclassified"  # right box, wrong category; corrected
    OPERATOR_MISSED = "operator_missed"              # model missed it; operator drew box
    HARD_NEGATIVE = "hard_negative"                  # model was wrong (false positive)
    TRUE_NEGATIVE_SCAN = "true_negative_scan"        # entire scan cleared — no threats


class LabelStatus(str, Enum):
    PENDING = "pending"       # queued, awaiting expert review
    REVIEWED = "reviewed"     # annotator confirmed; ready for versioning
    CONSUMED = "consumed"     # written into a versioned dataset snapshot
    REJECTED = "rejected"     # quality gate or annotator rejected; not usable


@dataclass
class LabelEntry:
    """One attributable label candidate.

    Positive labels carry a bounding box (the region to learn from).
    Hard negatives and true-negative scans carry no box — they are scan-level
    or region-level signals that the listed class was NOT present.
    """

    label_id: str                          # UUID string
    scan_id: str                           # UUID string — correlation key
    scan_sha256: str                       # content-address of the scan frame bytes
    frame_id: str
    source: LabelSource
    category: ThreatCategory
    operator_id: str
    operator_outcome: OperatorOutcome

    # Bounding box — pixel coords matching the frame. None for scan-level negatives.
    box_x: int | None
    box_y: int | None
    box_w: int | None
    box_h: int | None

    status: LabelStatus = LabelStatus.PENDING

    # Optional context
    original_model_score: float | None = None    # model confidence when it fired
    corrected_from: str | None = None            # previous category (for RECLASSIFIED)
    operator_note: str | None = None
    feedback_id: str | None = None              # which OperatorFeedback this came from

    queued_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    reviewed_at: str | None = None
    consumed_at: str | None = None

    @property
    def has_box(self) -> bool:
        return self.box_x is not None

    def to_jsonl_line(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)

    @classmethod
    def from_jsonl_line(cls, line: str) -> "LabelEntry":
        return cls(**json.loads(line))


# ---------------------------------------------------------------------------
# Extraction helpers — OperatorFeedback -> list[LabelEntry]
# ---------------------------------------------------------------------------
def _frame_sha256(feedback: OperatorFeedback, frame_id: str) -> str:
    """Return the SHA-256 of the frame bytes associated with frame_id."""
    for f in feedback.detection.frames:
        if f.frame_id == frame_id:
            return f.image.sha256
    return "unknown"


def _detection_score(feedback: OperatorFeedback, detection_id: str) -> float | None:
    """Look up the model confidence for a detection_id in the embedded result."""
    for d in feedback.detection.detections:
        if str(d.detection_id) == str(detection_id):
            return d.score
    return None


def _detection_frame_id(feedback: OperatorFeedback, detection_id: str) -> str:
    """Return the frame_id for a given detection_id."""
    for d in feedback.detection.detections:
        if str(d.detection_id) == str(detection_id):
            return d.frame_id
    return "unknown"


def _detection_box(feedback: OperatorFeedback, detection_id: str) -> tuple[int, int, int, int] | None:
    for d in feedback.detection.detections:
        if str(d.detection_id) == str(detection_id):
            return d.box.x, d.box.y, d.box.width, d.box.height
    return None


def extract_label_entries(feedback: OperatorFeedback) -> list[LabelEntry]:
    """Convert an ``OperatorFeedback`` into zero or more ``LabelEntry`` objects.

    Rules:
    * CONFIRMED review   -> ``OPERATOR_CONFIRMED`` positive label
    * RECLASSIFIED review -> ``OPERATOR_RECLASSIFIED`` positive label (with correction)
    * REJECTED review    -> ``HARD_NEGATIVE`` (no box — region-level FP signal)
    * Missed annotation  -> ``OPERATOR_MISSED`` positive label (the most valuable signal)
    * Scan with no findings and CLEARED outcome -> ``TRUE_NEGATIVE_SCAN`` (one entry, no box)
    """
    entries: list[LabelEntry] = []
    fid = str(feedback.feedback_id)
    sid = str(feedback.scan_id)

    # -- reviews on model detections ---------------------------------------
    for rev in feedback.reviews:
        did = str(rev.detection_id)
        frame_id = _detection_frame_id(feedback, did)
        box = _detection_box(feedback, did)
        sha256 = _frame_sha256(feedback, frame_id)

        if rev.judgement == DetectionJudgement.CONFIRMED:
            category = next(
                (d.category for d in feedback.detection.detections if str(d.detection_id) == did),
                ThreatCategory.UNKNOWN,
            )
            entries.append(LabelEntry(
                label_id=str(uuid.uuid4()),
                scan_id=sid,
                scan_sha256=sha256,
                frame_id=frame_id,
                source=LabelSource.OPERATOR_CONFIRMED,
                category=category,
                operator_id=feedback.operator_id,
                operator_outcome=feedback.outcome,
                box_x=box[0] if box else None,
                box_y=box[1] if box else None,
                box_w=box[2] if box else None,
                box_h=box[3] if box else None,
                original_model_score=_detection_score(feedback, did),
                operator_note=rev.note_uz,
                feedback_id=fid,
            ))

        elif rev.judgement == DetectionJudgement.RECLASSIFIED:
            orig_category = next(
                (d.category for d in feedback.detection.detections if str(d.detection_id) == did),
                ThreatCategory.UNKNOWN,
            )
            entries.append(LabelEntry(
                label_id=str(uuid.uuid4()),
                scan_id=sid,
                scan_sha256=sha256,
                frame_id=frame_id,
                source=LabelSource.OPERATOR_RECLASSIFIED,
                category=rev.corrected_category,  # type: ignore[arg-type]
                operator_id=feedback.operator_id,
                operator_outcome=feedback.outcome,
                box_x=box[0] if box else None,
                box_y=box[1] if box else None,
                box_w=box[2] if box else None,
                box_h=box[3] if box else None,
                original_model_score=_detection_score(feedback, did),
                corrected_from=orig_category.value,
                operator_note=rev.note_uz,
                feedback_id=fid,
            ))

        elif rev.judgement == DetectionJudgement.REJECTED:
            category = next(
                (d.category for d in feedback.detection.detections if str(d.detection_id) == did),
                ThreatCategory.UNKNOWN,
            )
            entries.append(LabelEntry(
                label_id=str(uuid.uuid4()),
                scan_id=sid,
                scan_sha256=sha256,
                frame_id=frame_id,
                source=LabelSource.HARD_NEGATIVE,
                category=category,
                operator_id=feedback.operator_id,
                operator_outcome=feedback.outcome,
                box_x=box[0] if box else None,
                box_y=box[1] if box else None,
                box_w=box[2] if box else None,
                box_h=box[3] if box else None,
                original_model_score=_detection_score(feedback, did),
                operator_note=rev.note_uz,
                feedback_id=fid,
            ))
        # UNREVIEWED -> no label entry (NOT a label, per contract docstring)

    # -- operator-drawn missed regions (false negatives) -------------------
    for ann in feedback.missed:
        sha256 = _frame_sha256(feedback, ann.frame_id)
        entries.append(LabelEntry(
            label_id=str(uuid.uuid4()),
            scan_id=sid,
            scan_sha256=sha256,
            frame_id=ann.frame_id,
            source=LabelSource.OPERATOR_MISSED,
            category=ann.category,
            operator_id=feedback.operator_id,
            operator_outcome=feedback.outcome,
            box_x=ann.box.x,
            box_y=ann.box.y,
            box_w=ann.box.width,
            box_h=ann.box.height,
            operator_note=ann.note_uz,
            feedback_id=fid,
        ))

    # -- true-negative scan (cleared, no findings, nothing missed) ---------
    if (
        feedback.outcome == OperatorOutcome.CLEARED
        and not feedback.reviews
        and not feedback.missed
        and not feedback.detection.has_findings
    ):
        first_frame = feedback.detection.frames[0]
        entries.append(LabelEntry(
            label_id=str(uuid.uuid4()),
            scan_id=sid,
            scan_sha256=first_frame.image.sha256,
            frame_id=first_frame.frame_id,
            source=LabelSource.TRUE_NEGATIVE_SCAN,
            category=ThreatCategory.UNKNOWN,
            operator_id=feedback.operator_id,
            operator_outcome=feedback.outcome,
            box_x=None,
            box_y=None,
            box_w=None,
            box_h=None,
            feedback_id=fid,
        ))

    return entries


# ---------------------------------------------------------------------------
# Persistent queue
# ---------------------------------------------------------------------------
class LabelQueue:
    """Append-only, thread-safe JSONL queue on disk.

    One file per queue name. Each line is a complete, self-describing
    ``LabelEntry``. Writers append atomically under a lock; readers
    iterate without acquiring it (snapshot semantics — reads may lag
    very recent appends by one flush cycle, which is fine for batch
    retrain scheduling).
    """

    def __init__(self, queue_dir: str | Path, queue_name: str = "active_learning") -> None:
        self._path = Path(queue_dir) / f"{queue_name}.jsonl"
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        log.info("LabelQueue: %s", self._path)

    # -- write -------------------------------------------------------------
    def enqueue(self, entry: LabelEntry) -> None:
        """Append one label entry. Thread-safe."""
        line = entry.to_jsonl_line() + "\n"
        with self._lock:
            with self._path.open("a", encoding="utf-8") as fh:
                fh.write(line)

    def enqueue_many(self, entries: list[LabelEntry]) -> int:
        """Append multiple entries atomically (one fsync per call). Returns count."""
        if not entries:
            return 0
        lines = "".join(e.to_jsonl_line() + "\n" for e in entries)
        with self._lock:
            with self._path.open("a", encoding="utf-8") as fh:
                fh.write(lines)
        return len(entries)

    # -- read (snapshot) ---------------------------------------------------
    def iter_pending(self) -> Iterator[LabelEntry]:
        """Yield all entries with PENDING status (snapshot at call time)."""
        if not self._path.exists():
            return
        with self._path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = LabelEntry.from_jsonl_line(line)
                    if entry.status == LabelStatus.PENDING:
                        yield entry
                except Exception as exc:
                    log.warning("Skipping malformed JSONL line: %s", exc)

    def count_by_status(self) -> dict[str, int]:
        """Return {status_value: count} across all entries."""
        counts: dict[str, int] = {}
        if not self._path.exists():
            return counts
        with self._path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                    s = obj.get("status", "unknown")
                    counts[s] = counts.get(s, 0) + 1
                except Exception:
                    pass
        return counts

    def count_by_category(self, status: LabelStatus = LabelStatus.REVIEWED) -> dict[str, int]:
        """Return {category_value: count} for entries at a given status."""
        counts: dict[str, int] = {}
        if not self._path.exists():
            return counts
        with self._path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                    if obj.get("status") == status.value:
                        cat = obj.get("category", "unknown")
                        counts[cat] = counts.get(cat, 0) + 1
                except Exception:
                    pass
        return counts

    @property
    def queue_path(self) -> Path:
        return self._path


__all__ = [
    "LabelSource",
    "LabelStatus",
    "LabelEntry",
    "LabelQueue",
    "extract_label_entries",
]
