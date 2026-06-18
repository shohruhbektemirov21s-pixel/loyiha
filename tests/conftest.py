"""Top-level pytest fixtures shared across every test module.

Scope hierarchy:
    session  — one-time: HMAC key, app factory, DB setup/teardown
    function — per-test:  async HTTP client, clean DB state

Environment variables consumed (all optional — tests fall back to stubs):
    XRAY_TEST_DB_URL     — asyncpg DSN for integration tests; defaults to in-memory SQLite stub
    XRAY_AUDIT_HMAC_KEY  — 64-hex audit HMAC key; generated if absent
    XRAY_TEST_BASE_URL   — base URL for E2E / performance tests against a live stack
"""

from __future__ import annotations

import asyncio
import os
import secrets
from typing import AsyncGenerator, Generator
from uuid import uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

# ---------------------------------------------------------------------------
# Test-environment isolation (must run before app.settings is imported)
# ---------------------------------------------------------------------------
# The dev `.env` at the repo root ships XRAY_DB_URL (the app's *runtime* Postgres
# DSN). In the test harness the DB is opt-in via XRAY_TEST_DB_URL; when that is
# absent we run every seam in stub mode. If the dev DSN leaks in, the first test
# that runs the app lifespan (e.g. the WebSocket suite) wires a phantom DB and
# flips the module-level _db_enabled flag for the whole session — every later
# stub-mode test then 500s on a DB that was never really initialised. Neutralise
# it here (an explicit env var overrides the .env value in pydantic-settings).
if not os.environ.get("XRAY_TEST_DB_URL"):
    os.environ["XRAY_DB_URL"] = ""

# The security/RBAC suite asserts that auth is *enforced* (401/403). The dev
# `.env` ships XRAY_AUTH_BYPASS=true for manual testing; if it leaks in, every
# auth assertion silently passes the bypass admin. This MUST be set at module
# import time (before any test calls get_settings(), which caches the result)
# so the whole session sees auth enforced regardless of collection order.
os.environ["XRAY_AUTH_BYPASS"] = "false"

# ---------------------------------------------------------------------------
# Event loop — single loop for the whole test session
# ---------------------------------------------------------------------------
@pytest.fixture(scope="session")
def event_loop_policy():
    return asyncio.DefaultEventLoopPolicy()


@pytest.fixture(scope="session")
def event_loop():
    policy = asyncio.DefaultEventLoopPolicy()
    loop   = policy.new_event_loop()
    yield  loop
    loop.close()


# ---------------------------------------------------------------------------
# HMAC key — generate a fresh one if not set; tests that need it get it clean
# ---------------------------------------------------------------------------
@pytest.fixture(scope="session")
def hmac_key_hex() -> str:
    """32-byte (64 hex char) HMAC key for audit chain tests."""
    env = os.environ.get("XRAY_AUDIT_HMAC_KEY", "")
    if env and len(env) == 64:
        return env
    key = secrets.token_hex(32)
    os.environ["XRAY_AUDIT_HMAC_KEY"] = key
    return key


@pytest.fixture(scope="session")
def hmac_key_bytes(hmac_key_hex: str) -> bytes:
    return bytes.fromhex(hmac_key_hex)


# ---------------------------------------------------------------------------
# FastAPI application (stub mode — no GPU, no real DB by default)
# ---------------------------------------------------------------------------
@pytest.fixture(scope="session")
def app():
    """Return the FastAPI app with all ML seams in stub mode."""
    os.environ.setdefault("XRAY_ENVIRONMENT",       "test")
    os.environ.setdefault("XRAY_DETECTOR_ENABLED",  "false")
    os.environ.setdefault("XRAY_VLM_ENABLED",       "false")
    os.environ.setdefault("XRAY_DOCS_URL",          "/docs")
    # Use a unique secret per test session
    os.environ.setdefault("XRAY_JWT_SECRET",        secrets.token_hex(32))

    from app.main import create_app
    return create_app()


@pytest_asyncio.fixture(scope="function")
async def client(app) -> AsyncGenerator[AsyncClient, None]:
    """Async HTTP client pointed at the test app. Fresh per test."""
    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://testserver",
        timeout=30.0,
    ) as c:
        yield c


# ---------------------------------------------------------------------------
# JWT helpers
# ---------------------------------------------------------------------------
@pytest.fixture(scope="session")
def operator_token(app) -> str:
    """Minted operator JWT for authenticated requests."""
    from app.auth.backend import create_access_token
    from app.db.models import OperatorRole
    return create_access_token(
        operator_id=str(uuid4()),
        username="test_operator",
        role=OperatorRole.OPERATOR,
        lane_ids=["lane-1"],
    )


@pytest.fixture(scope="session")
def supervisor_token(app) -> str:
    """Supervisor JWT (elevated role)."""
    from app.auth.backend import create_access_token
    from app.db.models import OperatorRole
    return create_access_token(
        operator_id=str(uuid4()),
        username="test_supervisor",
        role=OperatorRole.SUPERVISOR,
        lane_ids=["lane-1", "lane-2"],
    )


@pytest.fixture(scope="session")
def admin_token(app) -> str:
    """Admin JWT for endpoints requiring admin role."""
    from app.auth.backend import create_access_token
    from app.db.models import OperatorRole
    return create_access_token(
        operator_id=str(uuid4()),
        username="test_admin",
        role=OperatorRole.ADMIN,
        lane_ids=["lane-1", "lane-2"],
    )


@pytest.fixture
def auth_headers(operator_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {operator_token}"}


@pytest.fixture
def supervisor_headers(supervisor_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {supervisor_token}"}


@pytest.fixture
def admin_headers(admin_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {admin_token}"}


# ---------------------------------------------------------------------------
# Scan / UUID factories (imported from builders for convenience)
# ---------------------------------------------------------------------------
@pytest.fixture
def scan_id() -> str:
    return str(uuid4())


@pytest.fixture
def make_scan_id() -> Generator:
    def _make() -> str:
        return str(uuid4())
    return _make
