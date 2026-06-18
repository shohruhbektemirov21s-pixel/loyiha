"""Lane-level RBAC isolation over REAL scan rows (BO'SHLIQ-1, requires_db).

The backend was hardened so an operator assigned to lane-1 cannot reach a
lane-2 scan. These tests confirm the invariant against actual DB rows (the
``_check_lane_access`` / list-filter SQL only has teeth when there is data):

  * GET /v1/scans/{id}      — cross-lane scan -> 403; own-lane -> 200
  * GET /v1/scans           — list returns ONLY the operator's lane
  * GET /v1/scans/{id}/audit — supervisor-only; cross-lane still denied
  * an unassigned operator (no lanes) sees nothing

Supervisors/admins see all lanes. Skipped cleanly when XRAY_TEST_DB_URL unset.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.db.models import OperatorRole
from tests.integration.conftest import requires_db

pytestmark = [pytest.mark.security, requires_db]


def _token(role: OperatorRole, lanes: list[str]) -> str:
    from app.auth.backend import create_access_token
    return create_access_token(
        operator_id=str(uuid4()), username=f"u-{role.value}", role=role, lane_ids=lanes,
    )


def _h(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


class TestCrossLaneScanAccessDenied:
    @pytest.mark.asyncio
    async def test_operator_cannot_get_other_lane_scan(self, db_client, seed_scan):
        sid = await seed_scan(lane_id="lane-2")
        op1 = _token(OperatorRole.OPERATOR, ["lane-1"])
        resp = await db_client.get(f"/v1/scans/{sid}", headers=_h(op1))
        assert resp.status_code in (403, 404), (
            f"lane-1 operator reached a lane-2 scan: {resp.status_code} {resp.text}"
        )

    @pytest.mark.asyncio
    async def test_operator_can_get_own_lane_scan(self, db_client, seed_scan):
        sid = await seed_scan(lane_id="lane-1")
        op1 = _token(OperatorRole.OPERATOR, ["lane-1"])
        resp = await db_client.get(f"/v1/scans/{sid}", headers=_h(op1))
        assert resp.status_code == 200, resp.text
        assert resp.json()["lane_id"] == "lane-1"

    @pytest.mark.asyncio
    async def test_supervisor_sees_any_lane(self, db_client, seed_scan):
        sid = await seed_scan(lane_id="lane-2")
        sup = _token(OperatorRole.SUPERVISOR, ["lane-1"])
        resp = await db_client.get(f"/v1/scans/{sid}", headers=_h(sup))
        assert resp.status_code == 200, resp.text


class TestListScansLaneFiltered:
    @pytest.mark.asyncio
    async def test_list_returns_only_own_lane(self, db_client, seed_scan):
        await seed_scan(lane_id="lane-1")
        await seed_scan(lane_id="lane-1")
        await seed_scan(lane_id="lane-2")
        op1 = _token(OperatorRole.OPERATOR, ["lane-1"])
        resp = await db_client.get("/v1/scans", headers=_h(op1))
        assert resp.status_code == 200, resp.text
        items = resp.json()["items"]
        assert items, "operator should see their own-lane scans"
        assert all(it["lane_id"] == "lane-1" for it in items), (
            f"list leaked a non-lane-1 scan: {[it['lane_id'] for it in items]}"
        )

    @pytest.mark.asyncio
    async def test_unassigned_operator_sees_nothing(self, db_client, seed_scan):
        await seed_scan(lane_id="lane-1")
        await seed_scan(lane_id="lane-2")
        op0 = _token(OperatorRole.OPERATOR, [])  # no lanes
        resp = await db_client.get("/v1/scans", headers=_h(op0))
        assert resp.status_code == 200, resp.text
        assert resp.json()["items"] == [], "an unassigned operator must see no scans"

    @pytest.mark.asyncio
    async def test_operator_filter_to_foreign_lane_denied(self, db_client, seed_scan):
        await seed_scan(lane_id="lane-1")
        op1 = _token(OperatorRole.OPERATOR, ["lane-1"])
        resp = await db_client.get("/v1/scans?lane_id=lane-2", headers=_h(op1))
        assert resp.status_code == 403


class TestAuditEndpointLaneIsolation:
    @pytest.mark.asyncio
    async def test_audit_requires_supervisor(self, db_client, seed_scan):
        sid = await seed_scan(lane_id="lane-1")
        op1 = _token(OperatorRole.OPERATOR, ["lane-1"])
        resp = await db_client.get(f"/v1/scans/{sid}/audit", headers=_h(op1))
        assert resp.status_code == 403  # operator role insufficient

    @pytest.mark.asyncio
    async def test_supervisor_audit_for_any_lane(self, db_client, seed_scan):
        sid = await seed_scan(lane_id="lane-2")
        sup = _token(OperatorRole.SUPERVISOR, ["lane-1"])
        resp = await db_client.get(f"/v1/scans/{sid}/audit", headers=_h(sup))
        assert resp.status_code == 200, resp.text
        assert isinstance(resp.json(), list)
