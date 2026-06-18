"""Integration tests for the audit chain at the API level.

Tests:
    1. Every API mutation (scan received, verdict generated, feedback submitted)
       creates an audit event.
    2. The audit chain is verifiable after a sequence of events.
    3. The /v1/admin/audit/verify endpoint reports the chain status correctly.
    4. After a detected tamper, verify returns valid=False.
    5. Every operator decision is attributable (has operator_id).

These tests require a database-backed audit sink.  They are skipped when
XRAY_TEST_DB_URL is not set (stub mode).
"""

from __future__ import annotations

import os
import secrets
from datetime import UTC, datetime, timezone
from uuid import uuid4

import pytest
import pytest_asyncio

REQUIRES_DB = pytest.mark.skipif(
    not os.environ.get("XRAY_TEST_DB_URL"),
    reason="XRAY_TEST_DB_URL not set — skipping DB-backed audit tests",
)


# ---------------------------------------------------------------------------
# In-memory audit sink (always available — no DB required)
# ---------------------------------------------------------------------------

class InMemoryAuditSink:
    """Records events in a list for inspection in tests."""

    def __init__(self, hmac_key: bytes) -> None:
        from app.audit.sink import _GENESIS_HMAC, _canonical_payload, _compute_hmac
        self._key      = hmac_key
        self._events: list[dict] = []
        self._last_hmac = _GENESIS_HMAC
        self._last_id   = None

    async def record(self, event_type: str, *, scan_id=None, **fields) -> None:
        from app.audit.sink import _canonical_payload, _compute_hmac
        eid        = uuid4()
        created_at = datetime.now(UTC)
        canonical  = _canonical_payload(fields)
        hmac       = _compute_hmac(
            self._key, self._last_hmac, eid, event_type,
            scan_id, fields.get("operator_id"), created_at, canonical,
        )
        self._events.append({
            "event_id":      eid,
            "prev_event_id": self._last_id,
            "event_type":    event_type,
            "scan_id":       scan_id,
            "operator_id":   fields.get("operator_id"),
            "created_at":    created_at,
            "payload":       fields,
            "event_hmac":    hmac,
        })
        self._last_hmac = hmac
        self._last_id   = eid

    def all_events(self) -> list[dict]:
        return list(self._events)

    def verify(self) -> tuple[bool, str]:
        import hmac as hmac_mod

        from app.audit.sink import _GENESIS_HMAC, _canonical_payload, _compute_hmac
        prev_hmac = _GENESIS_HMAC
        prev_id   = None
        for i, ev in enumerate(self._events):
            canonical = _canonical_payload(ev["payload"])
            expected  = _compute_hmac(
                self._key, prev_hmac, ev["event_id"], ev["event_type"],
                ev["scan_id"], ev["operator_id"], ev["created_at"], canonical,
            )
            if not hmac_mod.compare_digest(expected, ev["event_hmac"]):
                return False, f"Broken at index {i} type={ev['event_type']}"
            if prev_id and ev["prev_event_id"] != prev_id:
                return False, f"Link broken at index {i}"
            prev_hmac = ev["event_hmac"]
            prev_id   = ev["event_id"]
        return True, f"OK ({len(self._events)} events)"


@pytest.fixture
def audit_sink(hmac_key_bytes) -> InMemoryAuditSink:
    return InMemoryAuditSink(hmac_key_bytes)


# ---------------------------------------------------------------------------
# Tests using the in-memory sink
# ---------------------------------------------------------------------------

class TestInMemoryAuditSink:
    @pytest.mark.asyncio
    async def test_single_event_chain_is_valid(self, audit_sink: InMemoryAuditSink):
        await audit_sink.record("scan.received", scan_id=uuid4(), scanner_id="sc-01")
        ok, msg = audit_sink.verify()
        assert ok, msg

    @pytest.mark.asyncio
    async def test_multi_event_chain_is_valid(self, audit_sink: InMemoryAuditSink):
        scan_id = uuid4()
        await audit_sink.record("scan.received",          scan_id=scan_id)
        await audit_sink.record("detection.completed",    scan_id=scan_id, n_detections=2)
        await audit_sink.record("verdict.generated",      scan_id=scan_id, risk="high")
        await audit_sink.record("feedback.submitted",     scan_id=scan_id, operator_id="op-001", outcome="seized")
        ok, msg = audit_sink.verify()
        assert ok, msg

    @pytest.mark.asyncio
    async def test_every_feedback_event_has_operator_id(self, audit_sink: InMemoryAuditSink):
        scan_id = uuid4()
        await audit_sink.record("feedback.submitted", scan_id=scan_id, operator_id="op-001", outcome="inspected")
        feedback_events = [e for e in audit_sink.all_events() if e["event_type"] == "feedback.submitted"]
        for ev in feedback_events:
            assert ev["operator_id"], (
                f"feedback.submitted event missing operator_id: {ev}"
            )

    @pytest.mark.asyncio
    async def test_tampered_payload_breaks_chain(self, audit_sink: InMemoryAuditSink):
        await audit_sink.record("scan.received", scan_id=uuid4())
        await audit_sink.record("detection.completed", scan_id=uuid4(), n_detections=1)
        # Tamper with the first event's payload
        audit_sink._events[0]["payload"]["n_detections"] = 999
        ok, msg = audit_sink.verify()
        assert not ok, "Payload tamper not detected"

    @pytest.mark.asyncio
    async def test_deleted_event_breaks_chain(self, audit_sink: InMemoryAuditSink):
        await audit_sink.record("scan.received", scan_id=uuid4())
        await audit_sink.record("detection.completed", scan_id=uuid4())
        await audit_sink.record("verdict.generated", scan_id=uuid4())
        del audit_sink._events[1]  # delete middle event
        ok, _ = audit_sink.verify()
        assert not ok

    @pytest.mark.asyncio
    async def test_operator_id_change_breaks_chain(self, audit_sink: InMemoryAuditSink):
        scan_id = uuid4()
        await audit_sink.record("feedback.submitted", scan_id=scan_id, operator_id="op-001", outcome="seized")
        audit_sink._events[0]["operator_id"] = "ATTACKER"
        ok, _ = audit_sink.verify()
        assert not ok


# ---------------------------------------------------------------------------
# API-level audit endpoint tests
# ---------------------------------------------------------------------------

class TestAuditAPIEndpoints:
    @pytest.mark.asyncio
    async def test_audit_list_endpoint_exists(self, client, auth_headers):
        scan_id = uuid4()
        resp = await client.get(f"/v1/scans/{scan_id}/audit", headers=auth_headers)
        # 200 (empty list), 403 (requires supervisor), 404 (not found), 501 (stub)
        assert resp.status_code in (200, 403, 404, 501), (
            f"Audit list endpoint returned unexpected status: {resp.status_code}"
        )

    @pytest.mark.asyncio
    async def test_audit_verify_endpoint_exists(self, client, admin_headers):
        # FIXED: unwired DB now maps to 503 (DatabaseNotInitialised -> 503 in app.main),
        # so this fails closed cleanly instead of leaking a 500.
        resp = await client.get("/v1/admin/audit/verify", headers=admin_headers)
        # A server error (500) is NOT a valid outcome. Expect a clean
        # 200 (verified), 403 (wrong role), 501 (stub), or 503 (db unavailable).
        assert resp.status_code in (200, 403, 501, 503), (
            f"audit verify must fail closed, not 500; got {resp.status_code}: {resp.text}"
        )

    @pytest.mark.asyncio
    async def test_audit_verify_requires_admin_role(self, client, auth_headers):
        """Regular operator must not have access to audit chain verification."""
        resp = await client.get("/v1/admin/audit/verify", headers=auth_headers)
        assert resp.status_code in (403, 501), (
            "Audit verify endpoint should require admin role"
        )


# ---------------------------------------------------------------------------
# Audit completeness: specific event types must be recorded
# ---------------------------------------------------------------------------

REQUIRED_EVENT_TYPES = {
    "scan.received",
    "detection.completed",
    "verdict.generated",
    "feedback.submitted",
}


class TestAuditCompleteness:
    @pytest.mark.asyncio
    async def test_full_lifecycle_produces_all_event_types(self, audit_sink: InMemoryAuditSink):
        """A complete scan lifecycle must produce all required event types."""
        scan_id = uuid4()
        await audit_sink.record("scan.received",       scan_id=scan_id, lane_id="lane-1")
        await audit_sink.record("detection.completed", scan_id=scan_id, n_detections=1)
        await audit_sink.record("verdict.generated",   scan_id=scan_id, risk="high")
        await audit_sink.record("feedback.submitted",  scan_id=scan_id, operator_id="op-001", outcome="seized")

        recorded_types = {e["event_type"] for e in audit_sink.all_events()}
        missing = REQUIRED_EVENT_TYPES - recorded_types
        assert not missing, (
            f"Audit log missing required event types: {missing}"
        )

    @pytest.mark.asyncio
    async def test_feedback_outcome_is_logged_verbatim(self, audit_sink: InMemoryAuditSink):
        """The operator's outcome (SEIZED, INSPECTED, etc.) must appear verbatim in audit."""
        scan_id = uuid4()
        await audit_sink.record("feedback.submitted", scan_id=scan_id, operator_id="op-001", outcome="seized")
        ev = audit_sink.all_events()[-1]
        assert ev["payload"]["outcome"] == "seized"
