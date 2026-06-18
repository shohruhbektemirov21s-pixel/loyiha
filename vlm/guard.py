"""Language guard — enforces Uzbek Latin output quality on every model response.

Qwen3-VL is trained predominantly on Chinese and English. Uzbek is low-resource
for it, and the model will drift toward:
  * Cyrillic (Uzbek was written in Cyrillic until 1993; Qwen has seen both)
  * Russian (grammatically similar, well-represented in training)
  * English (dominant pre-training language)

These are not cosmetic issues — an operator who reads Uzbek Latin script cannot
reliably parse Cyrillic or Russian in a high-stress inspection context. This
module is the hard gate that blocks every slot output before it reaches the
``OperatorVerdict`` wire message.

Guard design:
* **Fail-closed, not silent.** Every rejection returns a named ``GuardViolation``
  so the caller can log it, count it by type, and decide whether to retry or fall
  back to the template default.
* **Ordered checks.** Cyrillic check first (cheap byte scan); if it passes,
  check for forbidden clearance language; then minimum/maximum length.
* **Retryable vs. non-retryable.** Cyrillic drift (the model ran away) is
  worth one retry at the same temperature.  Forbidden clearance phrases are
  NOT retried — they indicate the model is not safe for this slot.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from enum import Enum

# ---------------------------------------------------------------------------
# Violation taxonomy
# ---------------------------------------------------------------------------
class ViolationKind(str, Enum):
    CYRILLIC_DETECTED     = "cyrillic_detected"
    RUSSIAN_DETECTED      = "russian_detected"       # Russian stopwords (non-Cyrillic rare; belt+suspenders)
    ENGLISH_DRIFT         = "english_drift"
    FORBIDDEN_CLEARANCE   = "forbidden_clearance"    # model implied the object is OK to pass
    TOO_SHORT             = "too_short"
    TOO_LONG              = "too_long"
    EMPTY                 = "empty"


@dataclass(frozen=True)
class GuardViolation:
    kind: ViolationKind
    detail: str
    retryable: bool

    def __str__(self) -> str:
        return f"[{self.kind.value}] {self.detail}"


@dataclass(frozen=True)
class GuardResult:
    passed: bool
    text: str                         # original text (unchanged)
    violations: tuple[GuardViolation, ...]

    @property
    def should_retry(self) -> bool:
        return any(v.retryable for v in self.violations)

    def first_violation(self) -> GuardViolation | None:
        return self.violations[0] if self.violations else None


# ---------------------------------------------------------------------------
# Cyrillic detection
# ---------------------------------------------------------------------------
_CYRILLIC_RANGE = re.compile(r"[\u0400-\u04FF\u0500-\u052F]")


def _has_cyrillic(text: str) -> bool:
    return bool(_CYRILLIC_RANGE.search(text))


# ---------------------------------------------------------------------------
# Russian stopword list (Latin transliteration — belt+suspenders for edge cases
# where the model outputs Latin-script Russian rather than Uzbek)
# ---------------------------------------------------------------------------
_RUSSIAN_LATIN_STOPWORDS = frozenset({
    "eto", "eti", "eta", "etot", "etikh",
    "ne", "da", "net", "ili", "no",
    "kak", "tak", "zdes", "tut",
    "mozhno", "nelzya",
    "ochen", "vse", "vsye",
    "eto ", "nichego",
})

_RUSSIAN_LATIN_RE = re.compile(
    r"\b(" + "|".join(re.escape(w) for w in _RUSSIAN_LATIN_STOPWORDS) + r")\b",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# English drift detection (common English function words)
# ---------------------------------------------------------------------------
_ENGLISH_STOPWORDS = frozenset({
    "the", "this", "that", "is", "are", "was", "were",
    "it", "an", "and", "or", "not", "no",
    "detected", "found", "object", "item", "suspicious",
    "region", "area", "image",
})

_ENGLISH_RE = re.compile(
    r"\b(" + "|".join(re.escape(w) for w in _ENGLISH_STOPWORDS) + r")\b",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Forbidden clearance phrases — the model MUST NOT suggest passing the cargo
# ---------------------------------------------------------------------------
_FORBIDDEN_UZ = [
    r"yoʻl\s+qoʻying",      # "let through" (Uzbek)
    r"yo.l\s+q.ying",        # variant spellings
    r"xavfsiz",              # "safe"
    r"ruxsat\s+bering",      # "permit/allow"
    r"oʻtkazib\s+yubo",      # "let pass"
    r"tashvishlanmang",      # "don't worry"
    r"muammo\s+yoʻq",        # "no problem"
    r"hech\s+narsa\s+yoʻq",  # "nothing there"
    r"xatar\s+yoʻq",         # "no danger"
    r"xavf\s+yoʻq",          # "no risk"
    r"tozadir",              # "clean/clear" (colloquial)
]

_FORBIDDEN_RE = re.compile(
    "(" + "|".join(_FORBIDDEN_UZ) + ")",
    re.IGNORECASE | re.UNICODE,
)


# ---------------------------------------------------------------------------
# Length bounds (applied to individual slots, not the assembled rationale)
# ---------------------------------------------------------------------------
_MIN_SLOT_CHARS: int = 10
_MAX_SLOT_CHARS: int = 600


# ---------------------------------------------------------------------------
# The guard
# ---------------------------------------------------------------------------
class LanguageGuard:
    """Run all checks on a model-generated text slot.

    Usage::

        guard = LanguageGuard()
        result = guard.check(raw_model_output)
        if not result.passed:
            if result.should_retry:
                # retry the model call
            else:
                # use the fallback template value
    """

    def check(self, text: str) -> GuardResult:
        violations: list[GuardViolation] = []

        # 1. Empty
        stripped = text.strip()
        if not stripped:
            violations.append(GuardViolation(
                ViolationKind.EMPTY, "Slot text is empty.", retryable=True,
            ))
            return GuardResult(passed=False, text=text, violations=tuple(violations))

        # 2. Length bounds (cheap, before regex scans)
        if len(stripped) < _MIN_SLOT_CHARS:
            violations.append(GuardViolation(
                ViolationKind.TOO_SHORT,
                f"Slot has {len(stripped)} chars (min {_MIN_SLOT_CHARS}).",
                retryable=True,
            ))
        if len(stripped) > _MAX_SLOT_CHARS:
            violations.append(GuardViolation(
                ViolationKind.TOO_LONG,
                f"Slot has {len(stripped)} chars (max {_MAX_SLOT_CHARS}).",
                retryable=False,
            ))

        # 3. Cyrillic — hard fail; Uzbek Latin output must have none
        if _has_cyrillic(stripped):
            cyrillic_chars = list(dict.fromkeys(
                c for c in stripped if _CYRILLIC_RANGE.match(c)
            ))[:8]
            violations.append(GuardViolation(
                ViolationKind.CYRILLIC_DETECTED,
                f"Cyrillic characters found: {cyrillic_chars!r}. "
                f"Model drifted to Cyrillic script.",
                retryable=True,
            ))

        # 4. Russian stopwords (Latin)
        m = _RUSSIAN_LATIN_RE.search(stripped)
        if m:
            violations.append(GuardViolation(
                ViolationKind.RUSSIAN_DETECTED,
                f"Russian stopword detected: {m.group()!r}.",
                retryable=True,
            ))

        # 5. English drift
        english_matches = _ENGLISH_RE.findall(stripped)
        if len(english_matches) >= 3:  # tolerate 1-2 shared Uzbek/English short words
            violations.append(GuardViolation(
                ViolationKind.ENGLISH_DRIFT,
                f"English stopwords: {english_matches[:5]!r}.",
                retryable=True,
            ))

        # 6. Forbidden clearance phrases — NOT retryable
        m2 = _FORBIDDEN_RE.search(stripped)
        if m2:
            violations.append(GuardViolation(
                ViolationKind.FORBIDDEN_CLEARANCE,
                f"Forbidden clearance phrase: {m2.group()!r}. "
                f"Model must never suggest passing cargo.",
                retryable=False,
            ))

        passed = len(violations) == 0
        return GuardResult(passed=passed, text=text, violations=tuple(violations))

    def check_batch(self, texts: list[str]) -> list[GuardResult]:
        return [self.check(t) for t in texts]


# Singleton — construct once, reuse across requests.
_guard: LanguageGuard | None = None


def get_guard() -> LanguageGuard:
    global _guard
    if _guard is None:
        _guard = LanguageGuard()
    return _guard


__all__ = [
    "ViolationKind",
    "GuardViolation",
    "GuardResult",
    "LanguageGuard",
    "get_guard",
]
