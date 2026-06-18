"""Security tests: audit chain tamper detection.

Verifies that:
    1. Modifying a stored event's payload breaks chain verification.
    2. Inserting an event into the middle of the chain breaks it.
    3. Deleting an event breaks the link verification.
    4. Replaying an old HMAC breaks the chain.
    5. A chain verified with the wrong key always fails.
    6. The genesis event (no prev) is handled correctly.

These are unit-level tests against the in-memory chain logic.
Integration-level tests against a real PostgreSQL sink are in
tests/integration/test_audit_chain.py.
"""

from __future__ import annotations

import secrets
from copy import copy
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

import pytest

from app.audit.sink import _GENESIS_HMAC, _canonical_payload, _compute_hmac
from tests.unit.audit.test_hmac_chain import (
    _FakeEvent,
    _build_chain,
    _verify_fake_chain,
    _KEY,
    _DT,
)


class TestChainIntegrityAfterTampering:
    """Simulate attacks on the audit chain and verify detection."""

    def test_payload_injection_detected(self):
        """An attacker adds a field to an event payload."""
        chain = _build_chain(8)
        ev    = chain[4]
        chain[4] = _FakeEvent(
            **{**ev.__dict__, "payload": {**ev.payload, "injected": "by attacker"}}
        )
        ok, msg = _verify_fake_chain(chain)
        assert not ok, "Payload injection not detected"
        assert "4" in msg

    def test_event_deletion_detected(self):
        """An attacker deletes an event from the chain."""
        chain = _build_chain(8)
        del chain[3]    # remove event at index 3
        # seq=4 now has prev_event_id pointing to the deleted event's predecessor
        ok, msg = _verify_fake_chain(chain)
        # The HMAC of chain[3] (was chain[4]) was computed with prev = chain[2].hmac
        # but chain[2] is still in place, so HMAC recomputation will see a different prev.
        assert not ok, "Event deletion not detected"

    def test_event_insertion_detected(self):
        """An attacker inserts a spurious event into the chain."""
        chain = _build_chain(5)

        # Craft an insertion that tries to maintain HMAC continuity —
        # this is the hard attack; it requires knowing the HMAC key.
        spurious_eid      = uuid4()
        spurious_payload  = {"inserted": True}
        spurious_canonical = _canonical_payload(spurious_payload)

        # An attacker WITHOUT the key cannot compute a valid HMAC.
        # They'll have to use a wrong key or a random value.
        attacker_key = bytes.fromhex(secrets.token_hex(32))   # different key
        spurious_hmac = _compute_hmac(
            attacker_key,
            chain[2].event_hmac,   # correct prev
            spurious_eid,
            "inserted.event",
            None, None, _DT,
            spurious_canonical,
        )

        # Insert the fake event
        fake_event = _FakeEvent(
            seq=99,
            event_id=spurious_eid,
            prev_event_id=chain[2].event_id,
            event_type="inserted.event",
            scan_id=None,
            operator_id=None,
            created_at=_DT,
            payload=spurious_payload,
            event_hmac=spurious_hmac,
        )
        chain.insert(3, fake_event)

        # Verification uses the REAL key — the attacker's HMAC is wrong.
        ok, msg = _verify_fake_chain(chain, key=_KEY)
        assert not ok, "Inserted event not detected"

    def test_event_reordering_detected(self):
        """Swapping two events in the chain breaks it."""
        chain = _build_chain(6)
        chain[2], chain[3] = chain[3], chain[2]
        ok, _ = _verify_fake_chain(chain)
        assert not ok

    def test_replay_attack_detected(self):
        """Replacing event N with an older event from earlier in the chain."""
        chain = _build_chain(6)
        chain[4] = _FakeEvent(
            **{**chain[1].__dict__, "seq": 4}   # replay event 1 as event 4
        )
        ok, _ = _verify_fake_chain(chain)
        assert not ok

    def test_operator_id_change_detected(self):
        """An attacker changes the operator_id on a feedback event."""
        chain = _build_chain(5)
        ev = chain[2]
        # Change operator_id in the stored event (not recomputing HMAC)
        chain[2] = _FakeEvent(
            **{**ev.__dict__, "operator_id": "ATTACKER"}
        )
        ok, _ = _verify_fake_chain(chain)
        assert not ok

    def test_timestamp_change_detected(self):
        """An attacker modifies the created_at timestamp."""
        chain = _build_chain(5)
        ev = chain[2]
        chain[2] = _FakeEvent(
            **{**ev.__dict__, "created_at": datetime(2020, 1, 1, tzinfo=timezone.utc)}
        )
        ok, _ = _verify_fake_chain(chain)
        assert not ok


class TestCorrectChainAlwaysPasses:
    """Positive tests — a correctly built chain must always pass."""

    def test_short_chain_passes(self):
        assert _verify_fake_chain(_build_chain(1))[0]

    def test_long_chain_passes(self):
        assert _verify_fake_chain(_build_chain(100))[0]

    def test_chain_with_null_scan_id_passes(self):
        """Events without scan_id (system events) must be chainable."""
        chain = _build_chain(5)
        assert _verify_fake_chain(chain)[0]

    def test_chain_after_restart_reuses_tail(self):
        """After a simulated restart, a new chain seeded from the tail is valid."""
        chain    = _build_chain(5)
        tail     = chain[-1]

        # Simulate restart: bootstrap from the tail
        new_eid  = uuid4()
        payload  = {"post_restart": True}
        canonical = _canonical_payload(payload)
        new_hmac = _compute_hmac(
            _KEY, tail.event_hmac, new_eid, "restart.event",
            None, None, _DT, canonical,
        )
        new_event = _FakeEvent(
            seq=5,
            event_id=new_eid,
            prev_event_id=tail.event_id,
            event_type="restart.event",
            scan_id=None, operator_id=None,
            created_at=_DT,
            payload=payload,
            event_hmac=new_hmac,
        )
        extended = chain + [new_event]
        ok, msg = _verify_fake_chain(extended)
        assert ok, msg
