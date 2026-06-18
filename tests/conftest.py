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
from collections.abc import AsyncGenerator, Generator
from datetime import UTC
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
# Strip test-only XRAY_* env vars from os.environ at import time.
# ---------------------------------------------------------------------------
# app.settings.Settings uses extra="forbid" on the XRAY_ prefix. Test-runner
# conventions inject XRAY_*-named vars that are NOT declared Settings fields —
# XRAY_TEST_DB_URL, XRAY_GATE_*, XRAY_WS_TESTS, XRAY_PERF_TESTS, XRAY_E2E_ENABLED.
# Any test (or imported module) that constructs Settings() while these are present
# aborts at collection with a forbid ValidationError. They are read by the test
# harness directly (e.g. TEST_DB_URL below, the model gate envs), so we capture
# them and remove them from os.environ here — before app.settings is imported.
# (See BUG note in the QA report: the same forbid rule rejects any stray XRAY_*
# var in a real deployment too.)
# Only strip vars that appear in jobs which ALSO build the app (and so would hit
# the forbid rule). The model-gate XRAY_GATE_*/XRAY_BASELINE_* overrides live in
# a separate job that never constructs Settings, so they are left untouched and
# keep working as CI overrides.
_TEST_ONLY_XRAY_PREFIXES = ("XRAY_TEST_",)
_TEST_ONLY_XRAY_NAMES = {
    "XRAY_WS_TESTS", "XRAY_PERF_TESTS", "XRAY_E2E_ENABLED",
    "XRAY_E2E_BASE_URL", "XRAY_TEST_BASE_URL",
}
_CAPTURED_TEST_ENV: dict[str, str] = {}
for _k in list(os.environ):
    if _k.startswith(_TEST_ONLY_XRAY_PREFIXES) or _k in _TEST_ONLY_XRAY_NAMES:
        _CAPTURED_TEST_ENV[_k] = os.environ.pop(_k)


def get_test_env(name: str, default: str = "") -> str:
    """Read a test-only XRAY_* var that was stripped from os.environ at import."""
    return _CAPTURED_TEST_ENV.get(name, default)

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


# ---------------------------------------------------------------------------
# DB-backed fixtures (requires_db) — available to every tests/ subpackage
# ---------------------------------------------------------------------------
# Wire a REAL Postgres (XRAY_TEST_DB_URL) so tests that must observe production
# code paths — the PostgreSQL HMAC audit sink, the PostgresScanStore CAS,
# lane-level RBAC over real rows — run against the same models/SQL the server
# uses. Postgres-specific (JSONB, PG_UUID, pg_advisory_xact_lock, ON CONFLICT) so
# SQLite cannot substitute. Without a DSN the dependent tests SKIP cleanly.
# Read from the captured map (XRAY_TEST_DB_URL was popped from os.environ above so
# Settings()'s extra="forbid" doesn't reject it).
TEST_DB_URL = get_test_env("XRAY_TEST_DB_URL", "")

requires_db = pytest.mark.skipif(
    not TEST_DB_URL,
    reason="XRAY_TEST_DB_URL not set — skipping DB-backed integration tests",
)


@pytest_asyncio.fixture(scope="function")
async def db_app(hmac_key_hex):
    """A fresh FastAPI app wired to the real test DB, tables (re)created per test."""
    if not TEST_DB_URL:
        pytest.skip("XRAY_TEST_DB_URL not set")

    os.environ["XRAY_DB_URL"] = TEST_DB_URL
    os.environ["XRAY_ENVIRONMENT"] = "test"
    os.environ["XRAY_AUTH_BYPASS"] = "false"
    os.environ.setdefault("XRAY_JWT_SECRET", secrets.token_hex(32))
    os.environ["XRAY_AUDIT_HMAC_KEY"] = hmac_key_hex

    # Settings uses extra="forbid" on the XRAY_ prefix: test-only XRAY_* vars
    # (XRAY_TEST_DB_URL, XRAY_WS_TESTS, XRAY_GATE_*, …) are not declared fields,
    # so leaving them in os.environ aborts Settings() boot. They were already read
    # at import time; drop them while the app builds. (See BUG note in QA report.)
    _stripped: dict[str, str] = {}
    for k in list(os.environ):
        if k.startswith("XRAY_") and (
            k.startswith("XRAY_TEST_") or k.startswith("XRAY_GATE_")
            or k in ("XRAY_WS_TESTS", "XRAY_PERF_TESTS", "XRAY_E2E_ENABLED")
        ):
            _stripped[k] = os.environ.pop(k)

    import app.settings as settings_mod
    settings_mod._settings = None
    from sqlalchemy.ext.asyncio import create_async_engine

    import app.db.session as session_mod
    import app.deps as deps_mod
    from app.db.models import Base

    engine = create_async_engine(TEST_DB_URL)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    await engine.dispose()

    from app.main import create_app
    app_obj = create_app()

    from contextlib import AsyncExitStack
    async with AsyncExitStack() as stack:
        await stack.enter_async_context(app_obj.router.lifespan_context(app_obj))
        yield app_obj

    await session_mod.close_db()
    deps_mod._db_enabled = False
    settings_mod._settings = None
    os.environ["XRAY_DB_URL"] = ""
    for k, v in _stripped.items():
        os.environ[k] = v


@pytest_asyncio.fixture(scope="function")
async def db_client(db_app) -> AsyncGenerator[AsyncClient, None]:
    async with AsyncClient(
        transport=ASGITransport(app=db_app, raise_app_exceptions=False),
        base_url="http://testserver",
        timeout=30.0,
    ) as c:
        yield c


@pytest_asyncio.fixture(scope="function")
async def seed_scan(db_app):
    """Factory: insert a Scan row directly; returns the new scan_id."""
    from datetime import datetime, timezone

    from app.db.models import Scan, ScanState
    from app.db.session import get_session_factory

    async def _seed(*, lane_id: str | None = "lane-1",
                    state: str = ScanState.VERDICTED.value,
                    overall_risk: str | None = "high"):
        sid = uuid4()
        factory = get_session_factory()
        async with factory() as session:
            session.add(Scan(
                scan_id=sid, scanner_id="sc-test", lane_id=lane_id,
                subject="baggage", modality="single_energy", state=state,
                overall_risk=overall_risk, acquired_at=datetime.now(UTC),
            ))
            await session.commit()
        return sid

    return _seed
