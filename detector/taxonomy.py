"""Native detector label -> shared ``ThreatCategory`` taxonomy.

This module is the **single source of truth** for three things the detector owns:

1. ``WEAPON_CLASSES`` — the unified class list the model is *trained* on. The
   list order IS the YOLO class-index order; ``dataset.yaml`` and the training
   code import it from here so the index<->name contract can never drift.
2. ``to_category`` — how a native (fine-grained, model-specific) label is
   normalized onto the shared ``contracts.v1.ThreatCategory`` vocabulary that
   the VLM, console, and audit analytics all reason over.
3. ``NET_CONF`` / ``NET_IOU`` — the model's emission defaults (the wide net),
   imported by training, serving, and the profiler so they can't drift apart.
   These are *not* the deploy thresholds — those are calibrated, per-class, and
   live in the adapter.

Why a *separate* native label and category (the contract already models both):
the model speaks a richer dialect than downstream needs ("folding_knife",
"straight_knife"), and we want to retrain / add classes without forcing a
contract change. The category mapping is the stable seam; the native label is
free to evolve. Keeping the map here (not in the serving code) means an eval
run and a live request normalize labels *identically*.

Weapons-first, by mandate: a missed weapon is the worst failure, so the primary
classes (firearm, bladed weapon) are explicit and never silently dropped. Items
the public sets label but that aren't the v1 mission (powerbank, lighter,
sprayer, handcuffs) are listed in ``DROPPED_NATIVE_LABELS`` so the data-prep
step *deliberately* excludes them rather than mislabelling background.
"""

from __future__ import annotations

import logging

from contracts.v1 import ThreatCategory

log = logging.getLogger("xray.detector.taxonomy")


# ---------------------------------------------------------------------------
# Unified training classes  (index == YOLO class id — order is load-bearing)
# ---------------------------------------------------------------------------
# Kept deliberately small and weapons-centric for v1. Tools (wrench/pliers/
# hammer) are trained-in not because they're contraband but because the public
# sets label them densely; teaching the model their appearance reduces
# weapon<->tool confusion (a wrench read as a gun is a false positive; a gun
# read as a wrench is a *false negative* — the failure we most fear). Downstream
# they surface as METALLIC_ANOMALY, advisory only.
WEAPON_CLASSES: tuple[str, ...] = (
    "gun",        # 0  -> FIREARM        (primary)
    "knife",      # 1  -> BLADED_WEAPON  (primary)
    "scissors",   # 2  -> BLADED_WEAPON  (primary, dual-use; recall-first => flag it)
    "wrench",     # 3  -> METALLIC_ANOMALY
    "pliers",     # 4  -> METALLIC_ANOMALY
    "hammer",     # 5  -> METALLIC_ANOMALY
)

# Categories we hold ourselves accountable for on recall. The eval harness
# reports miss-rate on these explicitly; everything else is secondary.
PRIMARY_WEAPON_CATEGORIES: frozenset[ThreatCategory] = frozenset(
    {ThreatCategory.FIREARM, ThreatCategory.BLADED_WEAPON}
)


# ---------------------------------------------------------------------------
# Model emission defaults  (single source of truth — train / serve / profile)
# ---------------------------------------------------------------------------
# These govern what the *model* emits, NOT the deploy decision. The real,
# calibrated, per-class operating thresholds are chosen after training by
# eval/recall_eval.py and applied in the adapter. We keep these here so training
# (val recall), serving (UltralyticsPredictor), and the latency profiler can
# never silently disagree the way three independent literals would.
#
# NET_CONF is intentionally low: the model casts a wide net and lets the adapter
# threshold. Anything dropped at the model is an unrecoverable false negative —
# the failure this system most fears.
NET_CONF: float = 0.05
# NMS IoU for box-based models (YOLO). NMS-free models (RT-DETR) ignore it.
NET_IOU: float = 0.6


# ---------------------------------------------------------------------------
# Unified class  -> shared taxonomy
# ---------------------------------------------------------------------------
_CLASS_TO_CATEGORY: dict[str, ThreatCategory] = {
    "gun": ThreatCategory.FIREARM,
    "knife": ThreatCategory.BLADED_WEAPON,
    "scissors": ThreatCategory.BLADED_WEAPON,
    "wrench": ThreatCategory.METALLIC_ANOMALY,
    "pliers": ThreatCategory.METALLIC_ANOMALY,
    "hammer": ThreatCategory.METALLIC_ANOMALY,
}


# ---------------------------------------------------------------------------
# Raw dataset labels -> unified class
# ---------------------------------------------------------------------------
# The public sets each spell things differently and at different granularity.
# This table is consumed by the data-prep step (remap every source annotation
# onto a unified class before training) AND, defensively, at inference time so a
# model accidentally shipped with a raw label still normalizes correctly.
# Keys are lower-cased on lookup, so casing/spacing in the source is forgiven.
_NATIVE_ALIASES: dict[str, str] = {
    # --- firearms ---
    "gun": "gun", "pistol": "gun", "revolver": "gun", "firearm": "gun",
    "handgun": "gun", "rifle": "gun",
    # --- bladed: OPIXray is all knives at fine granularity ---
    "knife": "knife", "folding_knife": "knife", "straight_knife": "knife",
    "utility_knife": "knife", "multi-tool_knife": "knife", "multi_tool_knife": "knife",
    "blade": "knife",
    # --- scissors (SIXray/PIDray) ---
    "scissors": "scissors", "scissor": "scissors",
    # --- tools (context / confusion suppression) ---
    "wrench": "wrench", "spanner": "wrench",
    "pliers": "pliers", "plier": "pliers",
    "hammer": "hammer",
}

# Items present in the public sets that v1 intentionally does NOT train on.
# Data-prep drops boxes with these labels (it must not fold them into
# background, which would teach the model to suppress real objects).
DROPPED_NATIVE_LABELS: frozenset[str] = frozenset(
    {"powerbank", "lighter", "sprayer", "handcuffs", "bat", "baton",
     "battery", "laptop", "phone"}
)


def normalize_native(raw_label: str) -> str | None:
    """Map a raw dataset/model label onto a unified class name.

    Returns the unified class (one of ``WEAPON_CLASSES``), or ``None`` if the
    label is one we deliberately drop / don't recognize. Callers decide what a
    ``None`` means in their context (data-prep skips it; inference falls back).
    """
    return _NATIVE_ALIASES.get(raw_label.strip().lower())


def to_category(native_label: str) -> ThreatCategory:
    """Normalize a detector's native label to the shared ``ThreatCategory``.

    Fail-*open* on the recall side: an unrecognized label is surfaced as
    ``UNKNOWN`` (operator still sees the box) rather than dropped. Silently
    discarding a detection because its label didn't match a table would be a
    self-inflicted false negative — exactly what this system must not do.
    """
    unified = normalize_native(native_label)
    if unified is None:
        log.warning("unmapped native label %r -> UNKNOWN (surfaced, not dropped)", native_label)
        return ThreatCategory.UNKNOWN
    return _CLASS_TO_CATEGORY[unified]


def is_primary_weapon(category: ThreatCategory) -> bool:
    """True for the categories we report recall/miss-rate on by mandate."""
    return category in PRIMARY_WEAPON_CATEGORIES


__all__ = [
    "WEAPON_CLASSES",
    "PRIMARY_WEAPON_CATEGORIES",
    "NET_CONF",
    "NET_IOU",
    "DROPPED_NATIVE_LABELS",
    "normalize_native",
    "to_category",
    "is_primary_weapon",
]
