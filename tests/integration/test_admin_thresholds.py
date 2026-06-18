"""Admin threshold bounds at the API level (BO'SHLIQ-9, requires_db).

``PUT /v1/admin/thresholds/{category}`` must reject out-of-[0,1] thresholds and
the auto_clear > alert inversion with 422, and accept a valid update. Admin role
is required. Runs against the real DB (the endpoint reads/writes threshold_configs).
Skipped cleanly when XRAY_TEST_DB_URL is unset.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.db.models import OperatorRole
from tests.integration.conftest import requires_db

pytestmark = [pytest.mark.integration, requires_db]


def _token(role: OperatorRole) -> str:
    from app.auth.backend import create_access_token
    return create_access_token(
        operator_id=str(uuid4()), username=f"u-{role.value}", role=role,
        lane_ids=["lane-1", "lane-2"],
    )


def _h(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


class TestThresholdBounds:
    @pytest.mark.asyncio
    async def test_valid_update_accepted(self, db_client):
        admin = _token(OperatorRole.ADMIN)
        resp = await db_client.put(
            "/v1/admin/thresholds/firearm",
            json={"alert_threshold": 0.6, "auto_clear_threshold": 0.2},
            headers=_h(admin),
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["alert_threshold"] == 0.6
        assert body["auto_clear_threshold"] == 0.2

    @pytest.mark.asyncio
    @pytest.mark.parametrize("alert,clear", [
        (1.5, 0.2),    # alert > 1
        (0.6, -0.1),   # clear < 0
    ])
    async def test_out_of_range_rejected_422(self, db_client, alert, clear):
        admin = _token(OperatorRole.ADMIN)
        resp = await db_client.put(
            "/v1/admin/thresholds/firearm",
            json={"alert_threshold": alert, "auto_clear_threshold": clear},
            headers=_h(admin),
        )
        assert resp.status_code == 422, resp.text

    @pytest.mark.asyncio
    async def test_inverted_thresholds_rejected_422(self, db_client):
        admin = _token(OperatorRole.ADMIN)
        resp = await db_client.put(
            "/v1/admin/thresholds/firearm",
            json={"alert_threshold": 0.2, "auto_clear_threshold": 0.6},  # clear > alert
            headers=_h(admin),
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_non_admin_denied(self, db_client):
        op = _token(OperatorRole.OPERATOR)
        resp = await db_client.put(
            "/v1/admin/thresholds/firearm",
            json={"alert_threshold": 0.6, "auto_clear_threshold": 0.2},
            headers=_h(op),
        )
        assert resp.status_code == 403
