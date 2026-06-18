"""Continuous camera stream endpoints — auth + schema + fail-safe (BO'SHLIQ-7).

Covers the new continuous-video API:
    POST /v1/camera/stream/start | stop   — operator JWT required
    GET  /v1/camera/stream/status         — operator JWT required, status schema
    GET  /v1/camera/live.mjpg             — ?token= auth, 409 when not running

No real camera exists in CI, so:
  * status is asserted on the idle (not-running) manager;
  * start is asserted to FAIL CLOSED (503) when cv2 / a camera is unavailable —
    it must never pretend to have started.

All assertions hold in stub mode (no DB, no GPU). The module-level stream
manager is reset after each test so state never leaks between tests.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def _reset_stream_manager():
    """Ensure each test starts/ends with an idle stream manager (no leaks)."""
    from app.api.v1.camera import get_stream_manager
    mgr = get_stream_manager()
    mgr._analyzer = None
    mgr._capture = None
    yield
    mgr._analyzer = None
    mgr._capture = None


# ---------------------------------------------------------------------------
# Auth: every lifecycle endpoint requires a token
# ---------------------------------------------------------------------------
class TestStreamEndpointsRequireAuth:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("path", [
        "/v1/camera/stream/start",
        "/v1/camera/stream/stop",
    ])
    async def test_post_requires_auth(self, client, path):
        resp = await client.post(path, json={})
        assert resp.status_code == 401, f"{path}: expected 401, got {resp.status_code}"

    @pytest.mark.asyncio
    async def test_status_requires_auth(self, client):
        resp = await client.get("/v1/camera/stream/status")
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_live_mjpg_rejects_missing_token(self, client):
        # No ?token= -> the JWT decode fails -> 401 (browsers can't set headers
        # on <img>, hence the query-param auth).
        resp = await client.get("/v1/camera/live.mjpg")
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_live_mjpg_rejects_bad_token(self, client):
        resp = await client.get("/v1/camera/live.mjpg?token=garbage.token.here")
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Status schema (idle)
# ---------------------------------------------------------------------------
class TestStreamStatusSchema:
    @pytest.mark.asyncio
    async def test_idle_status_shape(self, client, auth_headers):
        resp = await client.get("/v1/camera/stream/status", headers=auth_headers)
        assert resp.status_code == 200
        body = resp.json()
        # Idle manager status contract.
        assert body["running"] is False
        for key in ("device", "cadence_s", "last_analysis_ts", "frames_analyzed", "recording"):
            assert key in body, f"status missing {key!r}"
        assert body["frames_analyzed"] == 0


# ---------------------------------------------------------------------------
# Fail-safe start (no camera in CI)
# ---------------------------------------------------------------------------
class TestStreamStartFailsClosed:
    @pytest.mark.asyncio
    async def test_start_when_camera_open_fails_returns_503(self, client, auth_headers, monkeypatch):
        # Deterministic regardless of whether a (virtual) camera exists on the
        # runner: force VideoStreamCapture.open to raise, then assert the start
        # endpoint fails CLOSED with 503 — it must never report a phantom stream.
        import camera.stream as stream_mod

        def _boom(self):
            raise stream_mod.CameraOpenError("no camera on this box (test)")

        monkeypatch.setattr(stream_mod.VideoStreamCapture, "open", _boom)
        monkeypatch.setattr(stream_mod.VideoStreamCapture, "close", lambda self: None)

        resp = await client.post("/v1/camera/stream/start", json={}, headers=auth_headers)
        assert resp.status_code == 503, (
            f"Start with a failing camera must be 503, got {resp.status_code}: {resp.text}"
        )
        # And the manager must remain idle.
        status_resp = await client.get("/v1/camera/stream/status", headers=auth_headers)
        assert status_resp.json()["running"] is False

    @pytest.mark.asyncio
    async def test_live_mjpg_409_when_not_running(self, client, auth_headers, operator_token):
        # A valid token but no running stream -> 409 (start it first), not 200.
        resp = await client.get(f"/v1/camera/live.mjpg?token={operator_token}")
        assert resp.status_code == 409


# ---------------------------------------------------------------------------
# Stop is idempotent / safe when idle
# ---------------------------------------------------------------------------
class TestStreamStopIdle:
    @pytest.mark.asyncio
    async def test_stop_when_idle_returns_not_running(self, client, auth_headers):
        resp = await client.post("/v1/camera/stream/stop", json={}, headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["running"] is False
