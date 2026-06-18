"""WebSocket endpoint — real-time scan notifications to the operator console.

Protocol:
  Client connects:  GET /v1/ws?token=<jwt>
  Server sends JSON messages when scan state changes.
  Client sends:     {"type": "ping"} → server replies {"type": "pong"}

Message schema (server → client):
  {"type": "scan.flagged",  "scan_id": "...", "risk_band": "HIGH", "lane_id": "..."}
  {"type": "scan.analyzed", "scan_id": "...", "lane_id": "..."}
  {"type": "scan.decided",  "scan_id": "...", "outcome": "SEIZED", "operator_id": "..."}
  {"type": "pong"}

Delivery:
  * Operators only receive events for their assigned lanes (claims.lane_ids).
    An empty lane_ids list = all lanes (supervisor/admin pattern).
  * Events are fanout: all connected operators for a lane receive every event.
  * No persistence: messages are best-effort delivery. Clients that are
    disconnected miss events and must poll /v1/scans on reconnect.
  * One process: asyncio hub. For multi-process, replace with a Redis
    pub/sub backend (the API surface is the same, only the hub internals change).
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections import defaultdict
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect

from app.auth.dependencies import TokenClaims, ws_claims

log = logging.getLogger("xray.ws")
router = APIRouter(tags=["notifications"])


# ---------------------------------------------------------------------------
# Hub (module-level singleton; init'd in app lifespan)
# ---------------------------------------------------------------------------
class NotificationHub:
    """Fan-out WebSocket hub. Thread-safe via asyncio (single event loop)."""

    def __init__(self) -> None:
        # lane_id -> set of (connection, claims) pairs
        # "ALL" is a special lane key meaning "subscribed to all lanes".
        self._subscribers: dict[str, set[tuple[WebSocket, TokenClaims]]] = defaultdict(set)

    def _lane_keys(self, lane_ids: list[str]) -> list[str]:
        return lane_ids if lane_ids else ["ALL"]

    async def connect(self, ws: WebSocket, claims: TokenClaims) -> None:
        await ws.accept()
        for lane in self._lane_keys(claims.lane_ids):
            self._subscribers[lane].add((ws, claims))
        log.info("WS connected: user=%s role=%s lanes=%s", claims.username, claims.role, claims.lane_ids)

    def disconnect(self, ws: WebSocket, claims: TokenClaims) -> None:
        for lane in self._lane_keys(claims.lane_ids):
            self._subscribers[lane].discard((ws, claims))
        log.info("WS disconnected: user=%s", claims.username)

    async def broadcast_lane(self, lane_id: str | None, message: dict[str, Any]) -> None:
        """Send to all subscribers of this lane AND all-lane subscribers."""
        data = json.dumps(message)
        targets: set[tuple[WebSocket, TokenClaims]] = set()
        if lane_id:
            targets |= self._subscribers.get(lane_id, set())
        targets |= self._subscribers.get("ALL", set())  # supervisors/admins

        dead: list[tuple[str, WebSocket, TokenClaims]] = []
        for ws, claims in targets:
            try:
                await ws.send_text(data)
            except Exception:
                dead.append((lane_id or "ALL", ws, claims))

        for lane, ws, claims in dead:
            self._subscribers[lane].discard((ws, claims))

    async def broadcast_all(self, message: dict[str, Any]) -> None:
        """Send to every connected client (e.g. system alerts)."""
        data = json.dumps(message)
        all_ws: set[WebSocket] = {
            ws
            for subs in self._subscribers.values()
            for ws, _ in subs
        }
        for ws in all_ws:
            try:
                await ws.send_text(data)
            except Exception:
                pass


# Module-level hub — created by the lifespan, used by endpoints and the ScanStore.
_hub: NotificationHub | None = None


def get_hub() -> NotificationHub:
    if _hub is None:
        raise RuntimeError("NotificationHub not initialised. Check lifespan setup.")
    return _hub


def init_hub() -> NotificationHub:
    global _hub
    _hub = NotificationHub()
    return _hub


# ---------------------------------------------------------------------------
# WebSocket endpoint
# ---------------------------------------------------------------------------
@router.websocket("/ws")
async def websocket_endpoint(
    ws:     WebSocket,
    claims: TokenClaims = Depends(ws_claims),
) -> None:
    hub = get_hub()
    await hub.connect(ws, claims)
    try:
        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if msg.get("type") == "ping":
                await ws.send_text(json.dumps({"type": "pong"}))
    except WebSocketDisconnect:
        pass
    finally:
        hub.disconnect(ws, claims)


# ---------------------------------------------------------------------------
# Helpers called by the ScanStore / audit pipeline to push events
# ---------------------------------------------------------------------------
async def notify_scan_flagged(
    scan_id: str,
    lane_id: str | None,
    risk_band: str,
    n_detections: int,
) -> None:
    if _hub is None:
        return
    await _hub.broadcast_lane(lane_id, {
        "type": "scan.flagged",
        "scan_id": scan_id,
        "lane_id": lane_id,
        "risk_band": risk_band,
        "n_detections": n_detections,
    })


async def notify_scan_analyzed(scan_id: str, lane_id: str | None) -> None:
    if _hub is None:
        return
    await _hub.broadcast_lane(lane_id, {
        "type": "scan.analyzed",
        "scan_id": scan_id,
        "lane_id": lane_id,
    })


async def notify_scan_decided(
    scan_id: str,
    lane_id: str | None,
    outcome: str,
    operator_id: str,
) -> None:
    if _hub is None:
        return
    await _hub.broadcast_lane(lane_id, {
        "type": "scan.decided",
        "scan_id": scan_id,
        "lane_id": lane_id,
        "outcome": outcome,
        "operator_id": operator_id,
    })


__all__ = [
    "NotificationHub", "get_hub", "init_hub",
    "notify_scan_flagged", "notify_scan_analyzed", "notify_scan_decided",
]
