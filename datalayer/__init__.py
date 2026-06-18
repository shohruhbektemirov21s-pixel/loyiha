"""Data layer — the asset and the risk.

This package owns everything *after* the operator decides: it closes the
active-learning loop (Hop 4, ``OperatorFeedback`` -> ``FeedbackReceipt``) and is
the custodian of the project's single most valuable, most sensitive asset — the
labeled X-ray corpus. There is no usable public dataset for the mission classes
(narcotics above all), so in-house collection + expert annotation *is* the model.

Design stance, consistent with the contract spine:

* **Data is the #1 asset and the #1 risk.** Sensitive scans never leave the
  secure environment: the store is local, content-addressed, encrypted at rest,
  integrity-verified on read, and egress is refused at the type level
  (``storage.py``).
* **Every label is attributable.** ``OperatorFeedback.operator_id`` is required;
  the label store carries author + timestamp into the annotation queue so
  annotator disagreement is resolvable later (``labelstore.py``).
* **Inconsistent labels poison the model**, so a label candidate passes a
  pure-python quality gate before it can enter the dataset (``quality.py``), and
  the per-class annotation rules are written down, unambiguously
  (``GUIDELINES.md``).
* **The loop is architecture, not an afterthought.** Operator confirmations
  become queued labels become a versioned dataset (``versioning.py``) become a
  scheduled retrain *job spec* (``retrain.py``) — wired from day one.
* **Severe class imbalance is handled at the data level**, explicitly and with
  numbers, not by "training a little more" (``balance.py``).

What runs where (this box is contract + numpy only — see the detector README):
every module here is pure-python / numpy and runs on this box. The bytes of an
actual scan and the GPU retrain run on the data/GPU box; this layer emits the
specs and manifests that drive them.
"""

from __future__ import annotations

from datalayer.active_learning import (
    ActiveLearningConfig,
    ActiveLearningLoop,
    ProcessingResult,
    build_active_learning_loop,
)
from datalayer.balance import (
    BalanceReport,
    ClassStats,
    MIN_LABELS_PER_CLASS,
    augmentation_target,
    compute_balance_report,
)
from datalayer.ingestion import (
    IngestConfig,
    IngestPipeline,
    IngestValidationError,
    RawFrame,
)
from datalayer.labelstore import (
    LabelEntry,
    LabelQueue,
    LabelSource,
    LabelStatus,
    extract_label_entries,
)
from datalayer.quality import (
    CheckOutcome,
    CheckResult,
    LabelQualityGate,
    QualityReport,
)
from datalayer.retrain import (
    RetrainJobSpec,
    RetrainScheduler,
    RetrainThresholds,
    TriggerReason,
)
from datalayer.storage import (
    AesGcmEncryptor,
    DevPassthroughEncryptor,
    EgressRefused,
    SecureImageStore,
    StoreIntegrityError,
    sha256_hex,
)
from datalayer.versioning import DatasetVersion, VersioningManager

__all__: list[str] = [
    # ingestion
    "RawFrame", "IngestConfig", "IngestValidationError", "IngestPipeline",
    # storage
    "AesGcmEncryptor", "DevPassthroughEncryptor", "EgressRefused",
    "SecureImageStore", "StoreIntegrityError", "sha256_hex",
    # label store
    "LabelSource", "LabelStatus", "LabelEntry", "LabelQueue", "extract_label_entries",
    # quality
    "CheckResult", "CheckOutcome", "QualityReport", "LabelQualityGate",
    # balance
    "MIN_LABELS_PER_CLASS", "ClassStats", "BalanceReport",
    "compute_balance_report", "augmentation_target",
    # versioning
    "DatasetVersion", "VersioningManager",
    # retrain
    "TriggerReason", "RetrainThresholds", "RetrainJobSpec", "RetrainScheduler",
    # active learning
    "ProcessingResult", "ActiveLearningLoop", "ActiveLearningConfig",
    "build_active_learning_loop",
]
