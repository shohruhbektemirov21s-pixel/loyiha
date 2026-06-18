"""Settings validation tests (BO'SHLIQ-6).

The Settings model is a security boundary at boot:

  * ``extra="forbid"`` with the XRAY_-prefix filter: a *mistyped* XRAY_* env key
    aborts boot (a wrong setting on a customs box is a defect, not something to
    swallow), while a NON-XRAY key in a shared .env is ignored.
  * Fail-closed prod invariants: environment=prod with no DB url, or auth bypass
    on in prod, or a too-short JWT/HMAC secret when secrets are required, all
    abort boot rather than silently degrading to a null store / logging-only audit.

All tests construct ``Settings`` directly with controlled env (``_env_file=None``
so the repo .env never leaks in) and assert boot succeeds or raises. Deterministic.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.settings import Settings

pytestmark = pytest.mark.unit


_GOOD_JWT = "a" * 64          # >= 32 chars
_GOOD_HMAC = "ab" * 32        # 64 hex chars = 32 bytes


def _build(monkeypatch, **xray_env) -> Settings:
    """Construct Settings with ONLY the given XRAY_* env, no .env file."""
    # Clear every XRAY_* var so a leaked test-session var doesn't contaminate.
    import os
    for k in list(os.environ):
        if k.startswith("XRAY_"):
            monkeypatch.delenv(k, raising=False)
    for k, v in xray_env.items():
        monkeypatch.setenv(f"XRAY_{k}", v)
    return Settings(_env_file=None)  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# extra="forbid" — typo detection
# ---------------------------------------------------------------------------
class TestForbidTypo:
    def test_mistyped_xray_key_aborts_boot(self, monkeypatch):
        # XRAY_DETECTOR_ENABLD (missing E) maps to no field -> forbid must reject.
        with pytest.raises(ValidationError):
            _build(monkeypatch, ENVIRONMENT="dev", DETECTOR_ENABLD="true")

    def test_valid_xray_key_is_accepted(self, monkeypatch):
        s = _build(monkeypatch, ENVIRONMENT="dev", DETECTOR_ENABLED="true")
        assert s.detector_enabled is True

    def test_non_xray_key_is_ignored(self, monkeypatch):
        # A non-prefixed key (a sibling tool's var) must NOT abort boot.
        monkeypatch.setenv("OLLAMA_NO_ANALYTICS", "1")
        monkeypatch.setenv("DO_NOT_TRACK", "1")
        s = _build(monkeypatch, ENVIRONMENT="dev")
        assert s.environment == "dev"

    def test_known_sibling_xray_key_is_tolerated(self, monkeypatch):
        # XRAY_CAM_* is declared as a shared sibling key, so it must not be a typo.
        s = _build(monkeypatch, ENVIRONMENT="dev", CAM_DEVICE="/dev/video0")
        assert s.cam_device == "/dev/video0"


# ---------------------------------------------------------------------------
# Fail-closed prod invariants
# ---------------------------------------------------------------------------
class TestProdInvariants:
    def test_prod_without_db_url_aborts(self, monkeypatch):
        with pytest.raises(ValidationError) as exc:
            _build(monkeypatch, ENVIRONMENT="prod", JWT_SECRET=_GOOD_JWT, AUDIT_HMAC_KEY=_GOOD_HMAC)
        assert "XRAY_DB_URL" in str(exc.value)

    def test_prod_with_auth_bypass_aborts(self, monkeypatch):
        with pytest.raises(ValidationError) as exc:
            _build(
                monkeypatch,
                ENVIRONMENT="prod",
                AUTH_BYPASS="true",
                DB_URL="postgresql+asyncpg://u:p@localhost/db",
                JWT_SECRET=_GOOD_JWT,
                AUDIT_HMAC_KEY=_GOOD_HMAC,
            )
        assert "AUTH_BYPASS" in str(exc.value)

    def test_prod_with_db_and_secrets_boots(self, monkeypatch):
        s = _build(
            monkeypatch,
            ENVIRONMENT="prod",
            DB_URL="postgresql+asyncpg://u:p@localhost/db",
            JWT_SECRET=_GOOD_JWT,
            AUDIT_HMAC_KEY=_GOOD_HMAC,
        )
        assert s.environment == "prod"
        assert s.db_url

    def test_db_wired_requires_jwt_secret_min_length(self, monkeypatch):
        with pytest.raises(ValidationError) as exc:
            _build(
                monkeypatch,
                ENVIRONMENT="dev",
                DB_URL="postgresql+asyncpg://u:p@localhost/db",
                JWT_SECRET="short",
                AUDIT_HMAC_KEY=_GOOD_HMAC,
            )
        assert "JWT_SECRET" in str(exc.value)

    def test_db_wired_requires_valid_hex_hmac(self, monkeypatch):
        with pytest.raises(ValidationError) as exc:
            _build(
                monkeypatch,
                ENVIRONMENT="dev",
                DB_URL="postgresql+asyncpg://u:p@localhost/db",
                JWT_SECRET=_GOOD_JWT,
                AUDIT_HMAC_KEY="not-hex-zz",
            )
        assert "HMAC" in str(exc.value)

    def test_dev_without_db_needs_no_secrets(self, monkeypatch):
        # The contract-only / dev box runs in stub mode with no secrets — boot ok.
        s = _build(monkeypatch, ENVIRONMENT="dev")
        assert s.jwt_secret == ""
        assert s.db_url is None
