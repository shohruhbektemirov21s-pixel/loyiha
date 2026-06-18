"""VLM output validation — Uzbek Latin script, format, and safety invariants.

Tests in this module verify:
    1. VLM output is in Uzbek Latin script (not Cyrillic, not Russian, not English).
    2. No forbidden "pass freely" phrases appear in any output.
    3. Rationale fields are non-empty and within length limits.
    4. CLEAR scan summary is cautious and correctly advisory.
    5. The verdict contract fields are all structurally valid.

The tests do NOT require a running VLM backend — they test the output contract
and the text validation utilities directly, then wrap the VLM generator with a
mock that returns fixture verdicts.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

import pytest

from contracts.v1.common import RiskBand, ThreatCategory
from contracts.v1.verdict import Locale, OperatorVerdict
from tests.fixtures.builders import (
    make_clear_verdict,
    make_detection_result,
    make_operator_verdict,
)

# ---------------------------------------------------------------------------
# Forbidden phrase corpus
# These phrases must NEVER appear in any VLM-generated text sent to operators.
# Adding a phrase here is a zero-cost safety improvement.
# ---------------------------------------------------------------------------

FORBIDDEN_PHRASES: list[str] = [
    # Uzbek Latin — "pass freely" and near-equivalents
    "erkin o'tkazish",
    "erkin o'tkazib",
    "erkin o'tkaz",
    "xavfsiz o'tkazish",
    "xavfsiz o'tkazib",
    "o'tkazish mumkin",
    "o'tkazing",              # imperative "pass it"
    "to'siqsiz o'tish",
    "tahdid yo'q",            # "no threat" as a clearance statement
    "xatar yo'q",             # "no danger"
    "xavf yo'q",              # "no risk"
    "xavfsiz",                # "safe" — when used as a standalone clearance

    # Russian equivalents
    "свободно пропустить",
    "безопасно пропустить",
    "пропустить свободно",
    "угрозы нет",
    "угроз нет",
    "опасности нет",
    "можно пропустить",

    # English equivalents (should never appear but guard defensively)
    "pass freely",
    "safe to pass",
    "no threat",
    "clear to pass",
    "can be cleared",
    "cleared for passage",
    "no danger",
    "free to go",
]

# Phrases that indicate the operator is receiving a direct instruction to clear
# (the system must only advise, never instruct)
FORBIDDEN_IMPERATIVE_CLEARANCES: list[str] = [
    "o'tkazing",
    "qo'ying",              # "let (it) through"
    "ruxsat bering",        # "give permission"
    "пропустите",
    "разрешите",
    "let through",
    "allow passage",
    "approve",
]


def check_forbidden_phrases(text: str) -> list[str]:
    """Return any forbidden phrases found in the text (case-insensitive)."""
    text_lower = text.lower()
    found = [p for p in FORBIDDEN_PHRASES if p.lower() in text_lower]
    found += [p for p in FORBIDDEN_IMPERATIVE_CLEARANCES if p.lower() in text_lower]
    return found


# ---------------------------------------------------------------------------
# Uzbek Latin script detection
# ---------------------------------------------------------------------------

UZBEK_LATIN_PATTERN = re.compile(
    r"[a-zA-ZʼʻOoGgSsHhCcNn'''\-]",  # include apostrophe variants for Uzbek
)

CYRILLIC_PATTERN = re.compile(r"[\u0400-\u04FF]")

def is_uzbek_latin(text: str, min_ratio: float = 0.60) -> bool:
    """Return True if the text appears to be Uzbek Latin script.

    Heuristic: Cyrillic characters are forbidden outright; of the alphabetic
    characters present, at least ``min_ratio`` must be Latin (a-z). Text with no
    letters at all (digits/punctuation only) is tolerated. Previously this
    function accepted ``min_ratio`` but ignored it and always returned True — it
    was dead code that could never catch a non-Latin alphabet; it now actually
    computes and applies the ratio.
    """
    if CYRILLIC_PATTERN.search(text):
        return False
    alpha = [c for c in text if c.isalpha()]
    if not alpha:
        return True   # numbers/punctuation only — tolerate
    latin = re.findall(r"[a-zA-Z]", text)
    ratio = len(latin) / len(alpha)
    return ratio >= min_ratio

def has_cyrillic(text: str) -> bool:
    return bool(CYRILLIC_PATTERN.search(text))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def clear_verdict():
    return make_clear_verdict()


@pytest.fixture
def high_risk_verdict():
    det = make_detection_result()
    return make_operator_verdict(det, risk=RiskBand.HIGH)


@pytest.fixture
def all_verdicts(clear_verdict, high_risk_verdict):
    return [clear_verdict, high_risk_verdict]


# ---------------------------------------------------------------------------
# ── Safety: No forbidden phrases ── (ABSOLUTE invariant)
# ---------------------------------------------------------------------------

class TestNoForbiddenPhrases:
    """The most critical text test.  No VLM output must ever tell an operator
    that a scan is safe to pass.  This is tested on fixture verdicts here; the
    same check is applied to live VLM output in the integration and E2E suites.
    """

    def _assert_no_forbidden(self, text: str, context: str = ""):
        violations = check_forbidden_phrases(text)
        assert not violations, (
            f"SAFETY VIOLATION in {context}: forbidden phrase(s) found: {violations!r}\n"
            f"Full text: {text!r}"
        )

    def test_clear_verdict_summary_has_no_forbidden_phrases(self, clear_verdict):
        self._assert_no_forbidden(clear_verdict.summary_uz, "clear verdict summary")

    def test_high_risk_verdict_summary_has_no_forbidden_phrases(self, high_risk_verdict):
        self._assert_no_forbidden(high_risk_verdict.summary_uz, "high risk verdict summary")

    def test_per_detection_rationale_has_no_forbidden_phrases(self, high_risk_verdict):
        for dv in high_risk_verdict.per_detection:
            self._assert_no_forbidden(dv.rationale_uz, f"detection verdict {dv.detection_id}")

    @pytest.mark.parametrize("phrase", FORBIDDEN_PHRASES[:5])
    def test_forbidden_phrase_detection_works(self, phrase: str):
        """Self-test: the detection function itself must catch the phrases."""
        test_text = f"Bu odam {phrase} kerak."
        found = check_forbidden_phrases(test_text)
        assert phrase.lower() in [f.lower() for f in found], (
            f"Bug in check_forbidden_phrases: failed to detect {phrase!r}"
        )

    def test_empty_string_has_no_forbidden_phrases(self):
        assert check_forbidden_phrases("") == []


# ---------------------------------------------------------------------------
# ── Script validation: Uzbek Latin only ──
# ---------------------------------------------------------------------------

class TestUzbekLatinScript:
    def test_clear_verdict_summary_has_no_cyrillic(self, clear_verdict):
        assert not has_cyrillic(clear_verdict.summary_uz), (
            f"Cyrillic characters found in clear verdict summary: {clear_verdict.summary_uz!r}"
        )

    def test_high_risk_verdict_summary_has_no_cyrillic(self, high_risk_verdict):
        assert not has_cyrillic(high_risk_verdict.summary_uz)

    def test_rationale_has_no_cyrillic(self, high_risk_verdict):
        for dv in high_risk_verdict.per_detection:
            assert not has_cyrillic(dv.rationale_uz), (
                f"Cyrillic found in rationale: {dv.rationale_uz!r}"
            )

    def test_locale_is_uz_latn_by_default(self):
        v = make_operator_verdict()
        assert v.locale == Locale.UZ_LATN


# ---------------------------------------------------------------------------
# ── Clear scan advisory language ──
# ---------------------------------------------------------------------------

class TestIsUzbekLatinHeuristic:
    """Exercise the is_uzbek_latin helper itself (it was previously dead code —
    accepted min_ratio but never applied it)."""

    def test_pure_latin_passes(self):
        assert is_uzbek_latin("Chapda metall buyum aniqlandi")

    def test_cyrillic_fails(self):
        assert not is_uzbek_latin("Бу ерда буюм")

    def test_digits_and_punct_only_tolerated(self):
        assert is_uzbek_latin("123 — 45.6 (78)")

    def test_min_ratio_is_applied(self):
        # A string that is mostly non-Latin alphabetic (Greek) but with no
        # Cyrillic should fail a high ratio bar — proving min_ratio is honoured.
        text = "αβγδε ab"  # 5 Greek + 2 Latin alpha → 2/7 ≈ 0.29
        assert not is_uzbek_latin(text, min_ratio=0.60)
        assert is_uzbek_latin(text, min_ratio=0.20)


class TestClearScanAdvisoryLanguage:
    """A CLEAR verdict must be advisory — it cannot declare the scan safe.
    It should communicate uncertainty and preserve operator authority.
    """

    REQUIRED_ADVISORY_CUES = [
        # At least one of these must appear (case-insensitive)
        "operator",
        "qaror",       # decision
        "tekshir",     # check/inspect
        "tavsiya",     # recommend
        "diqqat",      # attention
        "avtomatik",   # automatic (framing as automated, not authoritative)
    ]

    def test_clear_summary_contains_advisory_language(self, clear_verdict):
        summary_lower = clear_verdict.summary_uz.lower()
        has_cue = any(cue in summary_lower for cue in self.REQUIRED_ADVISORY_CUES)
        assert has_cue, (
            "CLEAR verdict summary does not contain any advisory language. "
            f"At least one of {self.REQUIRED_ADVISORY_CUES} must appear. "
            f"Got: {clear_verdict.summary_uz!r}"
        )

    def test_clear_verdict_summary_is_non_empty(self, clear_verdict):
        assert clear_verdict.summary_uz.strip()

    def test_clear_verdict_risk_is_clear(self, clear_verdict):
        assert clear_verdict.overall_risk == RiskBand.CLEAR


# ---------------------------------------------------------------------------
# ── Rationale quality ──
# ---------------------------------------------------------------------------

class TestRationaleQuality:
    MIN_RATIONALE_LEN = 20   # characters — very low bar; catches empty/trivial output

    def test_per_detection_rationale_is_meaningful(self, high_risk_verdict):
        for dv in high_risk_verdict.per_detection:
            assert len(dv.rationale_uz.strip()) >= self.MIN_RATIONALE_LEN, (
                f"Rationale for {dv.category.value} is too short: {dv.rationale_uz!r}"
            )

    def test_per_detection_confidence_is_in_unit_interval(self, high_risk_verdict):
        for dv in high_risk_verdict.per_detection:
            assert 0.0 <= dv.confidence <= 1.0

    def test_detection_category_matches_original(self):
        det = make_detection_result()
        v   = make_operator_verdict(det)
        for dv in v.per_detection:
            # Each per-detection verdict must reference a detection_id from the result
            original_ids = {d.detection_id for d in det.detections}
            assert dv.detection_id in original_ids
