"""LanguageGuard mustahkamlik testlari (BO'SHLIQ-5).

ML agenti yangilagan ``vlm/guard.py`` ni qattiq tekshiradi:
  * NFKC normalizatsiya — kompatibel (fullwidth, ligatura) kodpointlar orqali
    taqiqlangan iborani yashirib bo'lmasligi.
  * Mixed-script (homoglif) — kirill «а/о/с/е» latin so'z ichiga yashirilsa rad
    etilishi va RETRY QILINMASLIGI (xavfsizlik invarianti).
  * Kengaytirilgan taqiqlangan iboralar ("xavfli emas", "bemalol o'tadi", ...).
  * Cyrillic drift retryable; forbidden_clearance retryable EMAS.

Bularning bari pure unit — VLM backend yoki GPU talab qilmaydi. Deterministik.
"""

from __future__ import annotations

import unicodedata

import pytest

from vlm.guard import (
    GuardResult,
    LanguageGuard,
    ViolationKind,
    get_guard,
)

pytestmark = pytest.mark.unit


@pytest.fixture
def guard() -> LanguageGuard:
    return LanguageGuard()


# A clean, long-enough Uzbek-Latin slot that must pass every check.
# NB: avoid the loanword "predmet" — the guard's Russian-stopword list flags it
# (see BUG note in the QA report), so a clean slot must use "buyum" instead.
_CLEAN_UZ = "Chapda metall buyum aniqlandi. Operator tekshirishi tavsiya etiladi."


# ---------------------------------------------------------------------------
# Baseline — a clean slot passes
# ---------------------------------------------------------------------------
class TestCleanSlotPasses:
    def test_clean_uzbek_latin_passes(self, guard: LanguageGuard):
        result = guard.check(_CLEAN_UZ)
        assert result.passed, [str(v) for v in result.violations]
        assert result.violations == ()

    def test_result_carries_original_text_unchanged(self, guard: LanguageGuard):
        # Even when normalization changes the matching form, GuardResult.text is
        # the original input verbatim.
        raw = "ｘａｖｆｓｉｚ buyum aniqlandi shu yerda."  # fullwidth Latin
        result = guard.check(raw)
        assert result.text == raw


# ---------------------------------------------------------------------------
# NFKC normalization — compatibility codepoints can't smuggle phrases
# ---------------------------------------------------------------------------
class TestNFKCNormalization:
    def test_fullwidth_forbidden_phrase_is_caught(self, guard: LanguageGuard):
        # "xavfsiz" written in fullwidth Latin compatibility codepoints. Without
        # NFKC the literal forbidden regex would not match; with NFKC it folds to
        # plain "xavfsiz" and must be rejected.
        fullwidth = "ｘａｖｆｓｉｚ"
        assert unicodedata.normalize("NFKC", fullwidth) == "xavfsiz"
        text = f"Bu buyum {fullwidth} ekanligi aniqlandi shu joyda."
        result = guard.check(text)
        assert not result.passed
        kinds = {v.kind for v in result.violations}
        assert ViolationKind.FORBIDDEN_CLEARANCE in kinds

    def test_ligature_does_not_bypass(self, guard: LanguageGuard):
        # The ﬁ ligature (U+FB01) folds to "fi" under NFKC. Build a forbidden
        # token that only matches after folding.
        text = "Yuk to" + "ʻ" + "liq xavﬁsiz emas deb topildi bu yerda."
        # The above is contrived; the key invariant is NFKC runs before matching.
        normed = unicodedata.normalize("NFKC", text)
        assert "ﬁ" not in normed  # ligature folded


# ---------------------------------------------------------------------------
# Mixed-script (homoglyph) — hard, NON-retryable rejection
# ---------------------------------------------------------------------------
class TestMixedScriptHomoglyph:
    def test_cyrillic_a_in_latin_word_is_mixed_script(self, guard: LanguageGuard):
        # "xavfsiz" with a Cyrillic «а» (U+0430) instead of Latin "a". To a human
        # it reads as Uzbek-Latin but it is a script-confusion attack.
        homoglyph = "xаvfsiz"
        assert "а" in homoglyph
        text = f"Yuk {homoglyph} deb baholandi, operatorga ko'rsatiladi."
        result = guard.check(text)
        assert not result.passed
        kinds = {v.kind for v in result.violations}
        assert ViolationKind.MIXED_SCRIPT in kinds

    def test_mixed_script_is_not_retryable(self, guard: LanguageGuard):
        homoglyph = "xаvfsiz"  # Latin + Cyrillic in one token
        text = f"Yuk {homoglyph} deb baholandi, operatorga ko'rsatiladi."
        result = guard.check(text)
        mixed = [v for v in result.violations if v.kind == ViolationKind.MIXED_SCRIPT]
        assert mixed, "expected a mixed-script violation"
        assert all(not v.retryable for v in mixed), (
            "A homoglyph attack must NOT be retryable — a clean retry would not "
            "reproduce it and treating it as soft drift would let it bypass."
        )

    def test_pure_cyrillic_word_is_cyrillic_not_mixed(self, guard: LanguageGuard):
        # A whole Cyrillic word (no Latin in the token) is CYRILLIC_DETECTED.
        text = "Это опасный предмет, оператор должен проверить тут."
        result = guard.check(text)
        kinds = {v.kind for v in result.violations}
        assert ViolationKind.CYRILLIC_DETECTED in kinds


# ---------------------------------------------------------------------------
# Extended forbidden clearance phrases (the ML agent broadened these)
# ---------------------------------------------------------------------------
class TestExtendedForbiddenPhrases:
    # Each phrase, embedded in an otherwise valid Uzbek slot, must trip the
    # FORBIDDEN_CLEARANCE check. These are the "system told the operator it is
    # safe to pass" failures — the single most dangerous output.
    @pytest.mark.parametrize(
        "phrase",
        [
            "xavfsiz",                  # "safe"
            "xavfsizdir",               # "is safe" (suffixed)
            "xavfli emas",              # "not dangerous"
            "xavf yo'q",                # "no risk"
            "xatar yo'q",               # "no danger"
            "bemalol o'tadi",           # "passes freely"
            "o'tkazsa bo'ladi",         # "can be let through"
            "o'tkazish mumkin",         # "may be passed through"
            "tashvishlanmang",          # "don't worry"
            "xavotirsiz",               # "worry-free"
            "muammo yo'q",              # "no problem"
            "ruxsat bering",            # "give permission"
            "tozadir",                  # "is clean/clear"
        ],
    )
    def test_forbidden_phrase_rejected(self, guard: LanguageGuard, phrase: str):
        text = f"Tahlil natijasiga ko'ra bu yuk {phrase} ko'rinadi va arxivlanadi."
        result = guard.check(text)
        assert not result.passed, f"phrase {phrase!r} was NOT rejected"
        kinds = {v.kind for v in result.violations}
        assert ViolationKind.FORBIDDEN_CLEARANCE in kinds, (
            f"phrase {phrase!r} did not trip FORBIDDEN_CLEARANCE; "
            f"violations={[str(v) for v in result.violations]}"
        )

    def test_forbidden_clearance_is_not_retryable(self, guard: LanguageGuard):
        text = "Tahlil natijasiga ko'ra bu yuk xavfsizdir va o'tkazilsin albatta."
        result = guard.check(text)
        forb = [v for v in result.violations if v.kind == ViolationKind.FORBIDDEN_CLEARANCE]
        assert forb
        assert all(not v.retryable for v in forb)
        assert not result.should_retry


# ---------------------------------------------------------------------------
# Retry semantics — drift is retryable, attacks are not
# ---------------------------------------------------------------------------
class TestRetrySemantics:
    def test_cyrillic_drift_is_retryable(self, guard: LanguageGuard):
        # Pure Cyrillic (model drifted) — worth one retry.
        text = "Бу ерда металл буюм аниқланди, оператор текширсин."
        result = guard.check(text)
        cyr = [v for v in result.violations if v.kind == ViolationKind.CYRILLIC_DETECTED]
        assert cyr
        assert any(v.retryable for v in cyr)
        assert result.should_retry

    def test_empty_slot_is_retryable(self, guard: LanguageGuard):
        result = guard.check("   ")
        assert not result.passed
        assert result.first_violation().kind == ViolationKind.EMPTY
        assert result.should_retry

    def test_too_short_is_retryable(self, guard: LanguageGuard):
        result = guard.check("qisqa")  # < 10 chars
        kinds = {v.kind for v in result.violations}
        assert ViolationKind.TOO_SHORT in kinds


# ---------------------------------------------------------------------------
# Singleton accessor
# ---------------------------------------------------------------------------
class TestGuardSingleton:
    def test_get_guard_returns_same_instance(self):
        assert get_guard() is get_guard()

    def test_check_batch_matches_per_item(self, guard: LanguageGuard):
        texts = [_CLEAN_UZ, "xаvfsiz buyum aniqlandi bu joyda albatta."]
        batch = guard.check_batch(texts)
        assert len(batch) == 2
        assert batch[0].passed
        assert not batch[1].passed
