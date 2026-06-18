"""Aggregates all v1 routers under the `/v1` prefix."""

from __future__ import annotations

from fastapi import APIRouter

from . import detect, feedback, verdict, scans, camera
from .admin import admin_router, auth_router
from .ws import router as ws_router

api_v1 = APIRouter(prefix="/v1")
api_v1.include_router(detect.router)
api_v1.include_router(verdict.router)
api_v1.include_router(feedback.router)
api_v1.include_router(scans.router)
api_v1.include_router(camera.router)
api_v1.include_router(auth_router)
api_v1.include_router(admin_router)
api_v1.include_router(ws_router)

__all__ = ["api_v1"]
