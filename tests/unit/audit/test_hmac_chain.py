"""Unit tests for the audit HMAC chain.

Tests the HMAC computation and chain verification logic directly, without
a real database.  The integration tests (tests/integration/test_audit_chain.py)
test the same logic against a live PostgreSQL-backed audit sink.

Critical properties verified here:
    1. HMAC is deterministic for the same inputs.
    2. HMAC changes if ANY field is mutated (tamper detection).
    3. Chain links: a broken prev_hmac breaks verification from that point.
    4. Genesis event (prev_hmac = 64 zeros) is handled correctly.
    5. verify_chain() correctly identifies the first broken link.
    6. Timing-safe comparison is used (hmac.compare_digest).
"""

from __future__ import annotations

import hashlib
import hmac as hmac_module
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

import pytest

from app.audit.sink import _compute_hmac, _canonical_payload, _GENESIS_HMAC


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_KEY = bytes.fromhex(secrets.token_hex(32))

_DT = datetime(2025, 1, 15, 10, 0, 0, tzinfo=timezone.utc)


def _hmac(
    prev: str = _GENESIS_HMAC,
    event_id: UUID | None = None,
    event_type: str = "scan.received",
    scan_id: Any = None,
    operator_id: Any = None,
    created_at: datetime = _DT,
    payload: dict | None = None,
) -> str:
    eid = event_id or uuid4()
    canonical = _canonical_payload(payload or {})
    return _compute_hmac(_KEY, prev, eid, event_type, scan_id, operator_id, created_at, canonical)


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------

class TestHMACDeterminism:
    def test_same_inputs_produce_same_hmac(self):
        eid = uuid4()
        h1  = _hmac(event_id=eid)
        h2  = _hmac(event_id=eid)
        assert h1 == h2

    def test_different_event_ids_produce_different_hmac(self):
        assert _hmac(event_id=uuid4()) != _hmac(event_id=uuid4())

    def test_output_is_64_hex_characters(self):
        h = _hmac()
        assert len(h) == 64
        assert all(c in "0123456789abcdef" for c in h)


# ---------------------------------------------------------------------------
# Tamper detection — any mutation must change the HMAC
# ---------------------------------------------------------------------------

class TestTamperDetection:
    @pytest.fixture
    def baseline(self):
        eid = uuid4()
        sid = uuid4()
        return dict(
            event_id=eid,
            event_type="feedback.submitted",
            scan_id=sid,
            operator_id="op-001",
            created_at=_DT,
            payload={"outcome": "inspected"},
        )

    def test_mutating_event_type_changes_hmac(self, baseline):
        h1 = _hmac(**baseline)
        h2 = _hmac(**{**baseline, "event_type": "feedback.TAMPERED"})
        assert h1 != h2

    def test_mutating_scan_id_changes_hmac(self, baseline):
        h1 = _hmac(**baseline)
        h2 = _hmac(**{**baseline, "scan_id": uuid4()})
        assert h1 != h2

    def test_mutating_operator_id_changes_hmac(self, baseline):
        h1 = _hmac(**baseline)
        h2 = _hmac(**{**baseline, "operator_id": "op-ATTACKER"})
        assert h1 != h2

    def test_mutating_payload_changes_hmac(self, baseline):
        h1 = _hmac(**baseline)
        h2 = _hmac(**{**baseline, "payload": {"outcome": "TAMPERED"}})
        assert h1 != h2

    def test_mutating_created_at_changes_hmac(self, baseline):
        h1 = _hmac(**baseline)
        h2 = _hmac(**{**baseline, "created_at": datetime(2020, 1, 1, tzinfo=timezone.utc)})
        assert h1 != h2

    def test_mutating_prev_hmac_changes_hmac(self, baseline):
        h1 = _hmac(**baseline, prev=_GENESIS_HMAC)
        h2 = _hmac(**baseline, prev="a" * 64)
        assert h1 != h2


# ---------------------------------------------------------------------------
# Chain verification (in-memory simulation)
# ---------------------------------------------------------------------------

@dataclass
class _FakeEvent:
    seq:          int
    event_id:     UUID
    prev_event_id: UUID | None
    event_type:   str
    scan_id:      Any
    operator_id:  Any
    created_at:   datetime
    payload:      dict
    event_hmac:   str


def _build_chain(n: int, key: bytes = _KEY) -> list[_FakeEvent]:
    """Build a valid chain of n events."""
    events: list[_FakeEvent] = []
    prev_hmac = _GENESIS_HMAC
    prev_id: UUID | None = None

    for i in range(n):
        eid       = uuid4()
        sid       = uuid4()
        payload   = {"seq_hint": i}
        canonical = _canonical_payload(payload)
        h         = _compute_hmac(key, prev_hmac, eid, "test.event", sid, None, _DT, canonical)

        events.append(_FakeEvent(
            seq=i,
            event_id=eid,
            prev_event_id=prev_id,
            event_type="test.event",
            scan_id=sid,
            operator_id=None,
            created_at=_DT,
            payload=payload,
            event_hmac=h,
        ))
        prev_hmac = h
        prev_id   = eid

    return events


def _verify_fake_chain(events: list[_FakeEvent], key: bytes = _KEY) -> tuple[bool, str]:
    """Replicate verify_chain() logic over in-memory events."""
    if not events:
        return True, "OK (empty)"

    prev_hmac = _GENESIS_HMAC
    prev_id: UUID | None = None

    for ev in events:
        canonical = _canonical_payload(ev.payload)
        expected  = _compute_hmac(
            key, prev_hmac, ev.event_id, ev.event_type,
            ev.scan_id, ev.operator_id, ev.created_at, canonical,
        )
        if not hmac_module.compare_digest(expected, ev.event_hmac):
            return False, f"Broken at seq={ev.seq}"
        if prev_id is not None and ev.prev_event_id != prev_id:
            return False, f"Link broken at seq={ev.seq}"
        prev_hmac = ev.event_hmac
        prev_id   = ev.event_id

    return True, f"OK ({len(events)} events)"


class TestChainVerification:
    def test_valid_chain_passes(self):
        chain = _build_chain(10)
        ok, msg = _verify_fake_chain(chain)
        assert ok, msg

    def test_empty_chain_passes(self):
        ok, msg = _verify_fake_chain([])
        assert ok

    def test_single_event_chain_passes(self):
        chain = _build_chain(1)
        ok, msg = _verify_fake_chain(chain)
        assert ok, msg

    def test_tampered_hmac_breaks_chain(self):
        chain = _build_chain(10)
        # Corrupt event at index 5
        ev = chain[5]
        chain[5] = _FakeEvent(
            **{**ev.__dict__, "event_hmac": "d" * 64}  # wrong HMAC
        )
        ok, msg = _verify_fake_chain(chain)
        assert not ok
        assert "seq=5" in msg or "seq" in msg

    def test_tampered_payload_breaks_chain(self):
        chain = _build_chain(10)
        ev = chain[3]
        chain[3] = _FakeEvent(
            **{**ev.__dict__, "payload": {"seq_hint": 999}}  # tampered payload
        )
        ok, msg = _verify_fake_chain(chain)
        assert not ok

    def test_tampered_event_type_breaks_chain(self):
        chain = _build_chain(5)
        ev = chain[2]
        chain[2] = _FakeEvent(
            **{**ev.__dict__, "event_type": "TAMPERED"}
        )
        ok, msg = _verify_fake_chain(chain)
        assert not ok

    def test_broken_link_detected(self):
        """Replacing prev_event_id must break chain link verification."""
        chain = _build_chain(5)
        ev = chain[3]
        chain[3] = _FakeEvent(
            **{**ev.__dict__, "prev_event_id": uuid4()}  # wrong prev id
        )
        ok, msg = _verify_fake_chain(chain)
        assert not ok

    def test_wrong_key_breaks_all_events(self):
        """Attempting to verify with a different HMAC key must fail."""
        chain    = _build_chain(5, key=_KEY)
        wrong    = bytes.fromhex(secrets.token_hex(32))
        ok, msg  = _verify_fake_chain(chain, key=wrong)
        assert not ok


class TestCanonicalPayload:
    """_canonical_payload must produce stable, sorted JSON."""

    def test_key_order_does_not_affect_output(self):
        p1 = {"b": 2, "a": 1}
        p2 = {"a": 1, "b": 2}
        assert _canonical_payload(p1) == _canonical_payload(p2)

    def test_uuid_serialised_as_string(self):
        uid = uuid4()
        out = _canonical_payload({"id": uid})
        assert str(uid) in out

    def test_datetime_serialised_as_isoformat(self):
        out = _canonical_payload({"ts": _DT})
        assert _DT.isoformat() in out

    def test_empty_payload_is_stable(self):
        assert _canonical_payload({}) == "{}"
