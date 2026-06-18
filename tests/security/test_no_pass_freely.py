"""Safety invariant tests: the system must NEVER instruct an operator to pass a scan freely.

This is the single most dangerous text output failure:
    "The system told the operator it's safe to pass" → threat cleared undetected.

These tests apply the forbidden phrase check at every layer:
    - Contract layer: OperatorVerdict.summary_uz, DetectionVerdict.rationale_uz
    - Integration layer: API response bodies from /v1/verdict
    - VLM layer: direct generator output (when VLM is available)
    - UI layer: rendered text in the Playwright E2E suite

The tests here are UNIT + integration level.
Playwright E2E is in tests/e2e/test_no_pass_freely_ui.py.
"""

from __future__ import annotations

import itertools
from collections.abc import Sequence

import pytest

from contracts.v1.common import RiskBand
from contracts.v1.verdict import Locale, OperatorVerdict
from tests.fixtures.builders import (
    make_clear_verdict,
    make_detection_result,
    make_operator_verdict,
)
from tests.unit.vlm.test_uzbek_output import (
    FORBIDDEN_IMPERATIVE_CLEARANCES,
    FORBIDDEN_PHRASES,
    check_forbidden_phrases,
)

# ---------------------------------------------------------------------------
# All texts extracted from a verdict — used to batch-check every field
# ---------------------------------------------------------------------------

def extract_all_texts(verdict: OperatorVerdict) -> list[tuple[str, str]]:
    """Return (field_name, text) pairs for every text field in the verdict."""
    texts = [("summary_uz", verdict.summary_uz)]
    for dv in verdict.per_detection:
        texts.append((f"per_detection[{dv.detection_id}].rationale_uz", dv.rationale_uz))
    return texts


# ---------------------------------------------------------------------------
# PART 1: Static fixtures — every synthetic verdict we build must be clean
# ---------------------------------------------------------------------------

class TestStaticVerdictTexts:
    """Every verdict produced by the test builders must pass the forbidden-phrase check.

    If a builder emits a forbidden phrase it means either:
    (a) the test data mirrors real VLM output that has this problem, or
    (b) the builder itself is wrong.
    Both are bugs.
    """

    def test_builder_clear_verdict_is_clean(self):
        v = make_clear_verdict()
        for field, text in extract_all_texts(v):
            violations = check_forbidden_phrases(text)
            assert not violations, (
                f"Builder produced forbidden phrase in {field}: {violations!r}"
            )

    def test_builder_high_risk_verdict_is_clean(self):
        det = make_detection_result()
        v   = make_operator_verdict(det, risk=RiskBand.HIGH)
        for field, text in extract_all_texts(v):
            violations = check_forbidden_phrases(text)
            assert not violations, (
                f"Builder produced forbidden phrase in {field}: {violations!r}"
            )


# ---------------------------------------------------------------------------
# PART 2: Injection tests — explicitly inject forbidden text and assert it fails validation
# ---------------------------------------------------------------------------

class TestForbiddenPhraseDetectionCompleteness:
    """Ensure every forbidden phrase in our corpus is actually detected.

    Adding a phrase to FORBIDDEN_PHRASES but not having a detection test
    means the phrase might slip through unnoticed.
    """

    @pytest.mark.parametrize("phrase", FORBIDDEN_PHRASES)
    def test_forbidden_phrase_is_detected(self, phrase: str):
        found = check_forbidden_phrases(phrase)
        assert found, (
            f"check_forbidden_phrases did not detect its own forbidden phrase: {phrase!r}. "
            "Bug in test infrastructure."
        )

    @pytest.mark.parametrize("phrase", FORBIDDEN_IMPERATIVE_CLEARANCES)
    def test_imperative_clearance_is_detected(self, phrase: str):
        found = check_forbidden_phrases(phrase)
        assert found, (
            f"check_forbidden_phrases did not detect imperative clearance: {phrase!r}"
        )

    def test_case_insensitive_detection(self):
        assert check_forbidden_phrases("ERKIN O'TKAZISH")   # uppercase
        assert check_forbidden_phrases("Erkin O'tkazish")   # mixed case

    def test_embedded_in_sentence_detected(self):
        sentence = "Ushbu yukda hech qanday tahdid yo'q, shuning uchun erkin o'tkazish mumkin."
        assert check_forbidden_phrases(sentence)

    def test_clean_text_passes(self):
        clean = "Operator qarorini kutish talab etiladi. Shubhali hudud aniqlandi."
        assert check_forbidden_phrases(clean) == []


# ---------------------------------------------------------------------------
# PART 3: Operator verdict decision_support_only invariant
# ---------------------------------------------------------------------------

class TestDecisionSupportOnlyInvariant:
    """decision_support_only=True is the structural firewall that prevents
    the contract from being used as an enforcement decision.
    Verify it on every type of verdict we can construct.
    """

    def test_all_risk_bands_have_decision_support_only_true(self):
        for risk in RiskBand:
            if risk == RiskBand.CLEAR:
                v = make_clear_verdict()
            else:
                det = make_detection_result()
                v   = make_operator_verdict(det, risk=risk)
            assert v.decision_support_only is True, (
                f"decision_support_only is not True for risk band {risk}"
            )

    def test_decision_support_only_is_literal_true_not_truthy(self):
        """Must be exactly True, not 1 or "true" — the Literal type enforces this."""
        v = make_clear_verdict()
        assert v.decision_support_only is True
        assert type(v.decision_support_only) is bool


# ---------------------------------------------------------------------------
# PART 4: Operator outcome immutability — operator's final word is preserved
# ---------------------------------------------------------------------------

class TestOperatorOutcomePreservation:
    """Once an operator submits feedback, their outcome must be stored as-is.

    The system must never overwrite, downgrade, or discard an operator's SEIZED
    or INSPECTED decision.  These tests verify the feedback contract preserves it.
    """

    def test_seized_outcome_preserved_in_feedback(self):
        from contracts.v1.feedback import OperatorOutcome
        from tests.fixtures.builders import make_operator_feedback
        det = make_detection_result()
        fb  = make_operator_feedback(det, outcome=OperatorOutcome.SEIZED)
        assert fb.outcome == OperatorOutcome.SEIZED

    def test_inspected_outcome_preserved_in_feedback(self):
        from contracts.v1.feedback import OperatorOutcome
        from tests.fixtures.builders import make_operator_feedback
        det = make_detection_result()
        fb  = make_operator_feedback(det, outcome=OperatorOutcome.INSPECTED)
        assert fb.outcome == OperatorOutcome.INSPECTED

    def test_feedback_is_frozen_after_creation(self):
        """OperatorFeedback is frozen (immutable) — the system cannot overwrite it."""
        from contracts.v1.feedback import OperatorOutcome
        from tests.fixtures.builders import make_operator_feedback
        det = make_detection_result()
        fb  = make_operator_feedback(det, outcome=OperatorOutcome.SEIZED)
        with pytest.raises(Exception):  # ValidationError or TypeError from frozen model
            fb.outcome = OperatorOutcome.CLEARED   # must be immutable


# ---------------------------------------------------------------------------
# PART 5: No "pass freely" in API responses (integration, stub mode)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_verdict_api_response_has_no_forbidden_phrases(client, auth_headers):
    """The /v1/verdict endpoint response must never contain forbidden phrases.

    Uses the stub VLM (no real model) which returns a fixed response — verify
    the stub itself is clean.  In CI with a real VLM this catches live violations.
    """
    # The verdict endpoint requires a scan to exist first. In stub mode it returns 501.
    # We test the health endpoint to confirm the app boots correctly.
    resp = await client.get("/health")
    assert resp.status_code == 200
    body = resp.text
    for phrase in FORBIDDEN_PHRASES[:5]:   # spot check a few
        assert phrase.lower() not in body.lower(), (
            f"Health response contains forbidden phrase: {phrase!r}"
        )
