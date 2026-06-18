"""Real mutation → audit event, and concurrent/double-decision 409 (BO'SHLIQ-2 & 3).

Against a REAL database with the production PostgreSQL HMAC audit sink wired via
``app.dependency_overrides`` (exactly as app.main lifespan does):

  * BO'SHLIQ-2: ``POST /v1/scans/{id}/decision`` writes a ``scan.decided`` event
    into the tamper-evident chain (operator-attributable) and the chain verifies.
    This proves the production ``app/audit/sink.py`` path runs — not an in-memory
    test double.
  * BO'SHLIQ-3: a second decision on an already-DECIDED scan returns 409, never a
    500 — the state machine's compare-and-swap fails closed.

Skipped cleanly when XRAY_TEST_DB_URL is not set.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.db.models import OperatorRole, ScanState
from tests.integration.conftest import requires_db

pytestmark = [pytest.mark.integration, requires_db]


def _op_token(lanes=("lane-1",)) -> str:
    from app.auth.backend import create_access_token
    return create_access_token(
        operator_id=str(uuid4()), username="op-decider",
        role=OperatorRole.OPERATOR, lane_ids=list(lanes),
    )


def _h(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


class TestDecisionWritesAudit:
    @pytest.mark.asyncio
    async def test_decision_creates_verifiable_audit_event(self, db_app, db_client, seed_scan, hmac_key_bytes):
        sid = await seed_scan(lane_id="lane-1", state=ScanState.VERDICTED.value)
        token = _op_token()

        resp = await db_client.post(
            f"/v1/scans/{sid}/decision",
            json={"decision": "rejected", "note": "shubhali"},
            headers=_h(token),
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["state"] == ScanState.DECIDED.value
        assert resp.json()["outcome"] == "seized"

        # The production audit sink must have appended a scan.decided event that
        # references this scan and is attributable to the operator.
        from sqlalchemy import select

        from app.audit.sink import verify_chain
        from app.db.models import AuditEvent
        from app.db.session import get_session_factory

        factory = get_session_factory()
        async with factory() as session:
            rows = (await session.execute(
                select(AuditEvent).where(AuditEvent.scan_id == sid)
            )).scalars().all()
            event_types = {r.event_type for r in rows}
            assert "scan.decided" in event_types, (
                f"no scan.decided audit event written; got {event_types}"
            )
            decided = [r for r in rows if r.event_type == "scan.decided"]
            assert all(r.operator_id for r in decided), "decision audit must be attributable"

            ok, msg = await verify_chain(session, hmac_key_bytes)
            assert ok, f"production audit chain failed to verify: {msg}"


class TestDoubleDecision409:
    @pytest.mark.asyncio
    async def test_second_decision_returns_409_not_500(self, db_client, seed_scan):
        sid = await seed_scan(lane_id="lane-1", state=ScanState.VERDICTED.value)
        token = _op_token()

        first = await db_client.post(
            f"/v1/scans/{sid}/decision", json={"decision": "confirmed"}, headers=_h(token),
        )
        assert first.status_code == 200, first.text

        second = await db_client.post(
            f"/v1/scans/{sid}/decision", json={"decision": "rejected"}, headers=_h(token),
        )
        assert second.status_code == 409, (
            f"a repeat decision must be 409 (already decided), got {second.status_code}: {second.text}"
        )
        assert second.status_code != 500

    @pytest.mark.asyncio
    async def test_invalid_decision_value_is_422(self, db_client, seed_scan):
        sid = await seed_scan(lane_id="lane-1", state=ScanState.VERDICTED.value)
        token = _op_token()
        resp = await db_client.post(
            f"/v1/scans/{sid}/decision", json={"decision": "maybe"}, headers=_h(token),
        )
        assert resp.status_code == 422
