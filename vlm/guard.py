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
* **Normalize first.** Every slot is NFKC-normalized before any regex runs, so
  compatibility codepoints (fullwidth Latin, ligatures, combining forms) cannot
  smuggle a forbidden phrase past the literal matchers. The original text is
  preserved in the result; normalization is for matching only.
* **Ordered checks.** Cyrillic scan first (cheap byte scan); then mixed-script
  (homoglyph) detection; then forbidden clearance language; then length bounds.
* **Retryable vs. non-retryable.** Cyrillic drift (the model ran away) is
  worth one retry at the same temperature.  Forbidden clearance phrases and
  mixed-script (homoglyph) tokens are NOT retried — they indicate the output is
  not safe for this slot and a clean retry would not reproduce an attack anyway.
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
    MIXED_SCRIPT          = "mixed_script"           # homoglyph attack: Latin + Cyrillic in one token/text
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
# Latin letters (ASCII + the few Latin-Extended chars Uzbek Latin uses, e.g. o\u02BB
# is plain "o" + U+02BB; g\u02BB likewise \u2014 the modifier letters are not in this set
# because they are script-neutral punctuation).
_LATIN_RANGE = re.compile(r"[A-Za-z\u00C0-\u024F]")
# A token that mixes scripts (a homoglyph attack: Cyrillic \u00AB\u0430/\u043E/\u0441/\u0435\u00BB smuggled into
# a Latin word so the Cyrillic check sees too little to fire and the word still
# *reads* as Uzbek to an operator). We split on whitespace and inspect each token.
_TOKEN_SPLIT = re.compile(r"\s+")


def _has_cyrillic(text: str) -> bool:
    return bool(_CYRILLIC_RANGE.search(text))


def _has_latin(text: str) -> bool:
    return bool(_LATIN_RANGE.search(text))


def _mixed_script_tokens(text: str) -> list[str]:
    """Return tokens that contain BOTH Latin and Cyrillic letters.

    This catches the homoglyph evasion that a whole-text Cyrillic scan alone can
    miss in spirit: e.g. ``xavfsiz`` written with a Cyrillic \u00AB\u0430\u00BB so the word
    still looks Uzbek-Latin to a human but is no longer the string the forbidden
    list matches. Any such token is a hard violation \u2014 legitimate Uzbek Latin
    never mixes scripts inside one word.
    """
    hits: list[str] = []
    for tok in _TOKEN_SPLIT.split(text):
        if tok and _has_latin(tok) and _has_cyrillic(tok):
            hits.append(tok)
    return hits


# ---------------------------------------------------------------------------
# Russian stopword list (Latin transliteration — belt+suspenders for edge cases
# where the model outputs Latin-script Russian rather than Uzbek)
# ---------------------------------------------------------------------------
# NOTE: only words that are unambiguously Russian-Latin and do NOT collide with
# valid Uzbek Latin. Domain nouns like "vagon"/"narkotik"/"skaner" are *also*
# Uzbek and must NOT live here or every clean report would false-positive — the
# Cyrillic and mixed-script checks already catch real Russian-script drift.
_RUSSIAN_LATIN_STOPWORDS = frozenset({
    "eto", "eti", "eta", "etot", "etikh", "etom", "etogo", "etu",
    "ne", "net", "ili", "zhe", "by",
    "kak", "zdes", "tut", "tam", "gde", "kogda", "potomu",
    "mozhno", "nelzya", "nuzhno", "dolzhen", "nado",
    "ochen", "vse", "vsye", "vsego", "vsekh",
    "nichego", "chto", "chtoby", "kotoryy", "kotoraya",
    "yavlyaetsya", "imeetsya", "vidno", "viden", "vidna",
    "predmet", "obekt", "opasnost", "opasno", "opasnyy",
    "oruzhie", "veshchestvo",
    "izobrazhenie", "dosmotr",
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
# Forbidden phrases. Uzbek is agglutinative, so a single stem appears with many
# suffixes (-dir, -roq, -ku, plural -lar, case endings). We anchor on the STEM
# and allow an optional Uzbek suffix tail so we catch the morphological family,
# not just the dictionary form. ``[\sʻ’'-]?`` between words tolerates the apostrophe
# variants (oʻ / o' / o’) and hyphenation the model emits.
_FORBIDDEN_UZ = [
    r"yo[ʻ’'`]?l\s+qo[ʻ’'`]?ying",     # "let through" + apostrophe variants
    r"yo.l\s+q.ying",                   # loose variant spellings
    r"xavfsiz\w*",                      # "safe" + suffixes (xavfsizdir, xavfsizroq)
    r"xavf(li)?\s+emas\w*",             # "not dangerous / not a risk" (xavfli emas, xavf emas)
    r"xatar(li)?\s+emas\w*",            # "not a danger"
    r"ruxsat\s+ber\w*",                 # "permit/allow" (bering, berilsin, berish mumkin)
    r"o[ʻ’'`]?tkaz\w*\s+(yubor\w*|bo[ʻ’'`]?l\w*)",  # "let pass / can be passed through"
    r"o[ʻ’'`]?tkaz(sa|ish)\s+bo[ʻ’'`]?l\w*",        # "oʻtkazsa boʻladi" — "can be let through"
    r"bemalol\s+o[ʻ’'`]?t\w*",          # "passes freely" (bemalol oʻtadi/oʻtkazsa)
    r"tashvish(lan\w*|siz\w*)",         # "don't worry / worry-free" (tashvishlanmang, tashvishsiz)
    r"xavotir(lan\w*|siz\w*)",          # "no anxiety / don't be anxious" (xavotirsiz, xavotirlanmang)
    r"muammo\s+yo[ʻ’'`]?q\w*",          # "no problem"
    r"hech\s+(narsa|qanday\s+xavf)\s+yo[ʻ’'`]?q\w*",  # "nothing there / no danger at all"
    r"xatar\s+yo[ʻ’'`]?q\w*",           # "no danger"
    r"xavf\s+yo[ʻ’'`]?q\w*",            # "no risk"
    r"toza(dir|\b)\w*",                 # "clean/clear" (tozadir, toza)
    r"normal\w*\s+yuk",                 # "ordinary cargo" used to clear (normal yuk)
    r"o[ʻ’'`]?tkaz(ish|sa)\s+mumkin",   # "may be passed through"
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

        # 0. Unicode normalization (NFKC). Adversarial / sloppy model output can
        #    smuggle the same glyph in via compatibility codepoints (fullwidth
        #    Latin, ligatures, combining forms). NFKC folds those to their
        #    canonical form so every downstream regex sees ONE representation —
        #    closing an evasion path against the forbidden-clearance list. The
        #    GuardResult still carries the ORIGINAL text unchanged; normalization
        #    is for matching only.
        norm = unicodedata.normalize("NFKC", text)

        # 1. Empty
        stripped = norm.strip()
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

        # 3b. Mixed-script (homoglyph) — a token with BOTH Latin and Cyrillic
        #     letters is a script-confusion attack: a Cyrillic «а/о/с/е» hidden in
        #     a Latin word so the text still reads as Uzbek but evades the literal
        #     forbidden-phrase match. NOT retryable — it is never benign drift;
        #     a clean Uzbek retry would not reproduce it, and treating it as a
        #     soft error would let a poisoned crop bypass the clearance gate.
        mixed = _mixed_script_tokens(stripped)
        if mixed:
            violations.append(GuardViolation(
                ViolationKind.MIXED_SCRIPT,
                f"Mixed-script (homoglyph) tokens: {mixed[:5]!r}. "
                f"Latin and Cyrillic letters in one word — possible evasion.",
                retryable=False,
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
