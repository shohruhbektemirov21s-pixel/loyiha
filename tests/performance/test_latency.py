"""Latency and throughput SLA gate tests.

SLA targets (adjustable via env vars):
    XRAY_SLA_DETECT_P95_MS   — detector inference p95 latency (default 2000 ms)
    XRAY_SLA_DETECT_P99_MS   — detector inference p99 latency (default 3000 ms)
    XRAY_SLA_VERDICT_P95_MS  — full verdict generation p95   (default 10000 ms)
    XRAY_SLA_E2E_P95_MS      — end-to-end acquisition→verdict p95 (default 15000 ms)
    XRAY_SLA_THROUGHPUT_RPS  — minimum scans per second the API must sustain (default 1.0)
    XRAY_SLA_FEEDBACK_P95_MS — feedback submission p95 (default 500 ms)

Tests are skipped when:
    - The real detector / VLM is not enabled (stub mode returns immediately)
    - XRAY_PERF_TESTS=false (opt-out for very slow CI environments)

In stub mode a lighter "API overhead only" check is performed to ensure the
framework itself (serialization, auth, routing) doesn't introduce unexpected latency.
"""

from __future__ import annotations

import asyncio
import os
import statistics
import time
from typing import Callable, Awaitable
from uuid import uuid4

import pytest
import pytest_asyncio

# ---------------------------------------------------------------------------
# SLA configuration
# ---------------------------------------------------------------------------
SLA_DETECT_P95_MS   = int(os.environ.get("XRAY_SLA_DETECT_P95_MS",    "2000"))
SLA_DETECT_P99_MS   = int(os.environ.get("XRAY_SLA_DETECT_P99_MS",    "3000"))
SLA_VERDICT_P95_MS  = int(os.environ.get("XRAY_SLA_VERDICT_P95_MS",   "10000"))
SLA_E2E_P95_MS      = int(os.environ.get("XRAY_SLA_E2E_P95_MS",       "15000"))
SLA_FEEDBACK_P95_MS = int(os.environ.get("XRAY_SLA_FEEDBACK_P95_MS",  "500"))
SLA_THROUGHPUT_RPS  = float(os.environ.get("XRAY_SLA_THROUGHPUT_RPS", "1.0"))

PERF_TESTS_ENABLED  = os.environ.get("XRAY_PERF_TESTS", "true").lower() != "false"
REAL_DETECTOR       = os.environ.get("XRAY_DETECTOR_ENABLED", "false").lower() == "true"
REAL_VLM            = os.environ.get("XRAY_VLM_ENABLED",      "false").lower() == "true"

requires_perf       = pytest.mark.skipif(not PERF_TESTS_ENABLED, reason="XRAY_PERF_TESTS=false")
# /v1/scans queries Postgres via get_db; without a wired test DB it 500s in stub
# mode. Gate the DB-backed perf checks on a real DSN being configured.
requires_db         = pytest.mark.skipif(
    not os.environ.get("XRAY_TEST_DB_URL"),
    reason="needs a wired DB (XRAY_TEST_DB_URL) — /v1/scans queries Postgres",
)
requires_real_stack = pytest.mark.skipif(
    not (REAL_DETECTOR and REAL_VLM),
    reason="Real detector + VLM required for SLA tests",
)

# ---------------------------------------------------------------------------
# Timing helpers
# ---------------------------------------------------------------------------

async def _timed(coro_fn: Callable[[], Awaitable], n: int = 20) -> list[float]:
    """Run coro_fn n times and return wall-clock durations in milliseconds."""
    durations: list[float] = []
    for _ in range(n):
        t0 = time.perf_counter()
        await coro_fn()
        durations.append((time.perf_counter() - t0) * 1000)
    return durations


def _pct(values: list[float], p: float) -> float:
    return statistics.quantiles(values, n=100)[int(p) - 1] if len(values) >= 2 else values[0]


# ---------------------------------------------------------------------------
# API overhead tests (always run — no GPU required)
# ---------------------------------------------------------------------------

@requires_perf
class TestAPIFrameworkOverhead:
    """Framework overhead (routing, auth, serialization) must be low.

    These run in stub mode where handlers return immediately.
    The latency measured is purely FastAPI + httpx overhead.
    """

    @pytest.mark.asyncio
    async def test_health_endpoint_p95_under_50ms(self, client):
        durations = await _timed(lambda: client.get("/health"), n=50)
        p95 = _pct(durations, 95)
        assert p95 < 50, (
            f"Health endpoint p95={p95:.1f}ms > 50ms. "
            "Framework overhead is unexpectedly high."
        )

    @pytest.mark.asyncio
    async def test_scan_list_p95_under_100ms(self, client, auth_headers):
        durations = await _timed(
            lambda: client.get("/v1/scans", headers=auth_headers), n=30
        )
        p95 = _pct(durations, 95)
        assert p95 < 100, (
            f"Scan list p95={p95:.1f}ms > 100ms in stub mode. "
            "Investigate routing or auth overhead."
        )

    @pytest.mark.asyncio
    async def test_feedback_submission_p95_under_sla(self, client, auth_headers):
        from tests.fixtures.builders import make_detection_result, make_operator_feedback
        from contracts.v1.feedback import OperatorOutcome
        det     = make_detection_result()
        fb      = make_operator_feedback(det, outcome=OperatorOutcome.INSPECTED)
        payload = fb.model_dump(mode="json")

        durations = await _timed(
            lambda: client.post("/v1/feedback", json=payload, headers=auth_headers),
            n=20,
        )
        p95 = _pct(durations, 95)
        assert p95 < SLA_FEEDBACK_P95_MS, (
            f"Feedback submission p95={p95:.1f}ms > SLA {SLA_FEEDBACK_P95_MS}ms"
        )


# ---------------------------------------------------------------------------
# Real detector latency gates (skipped in stub mode)
# ---------------------------------------------------------------------------

@requires_perf
@requires_real_stack
class TestDetectorLatencyGates:
    """Detector inference must meet the SLA on the target hardware."""

    @pytest.fixture(scope="class")
    def detector(self):
        from detector.serving.composition import DetectorConfig, build_detector
        return build_detector(DetectorConfig(
            weights=os.environ["XRAY_DETECTOR_WEIGHTS"],
            device=os.environ.get("XRAY_DETECTOR_DEVICE", "cuda"),
            name="xray-detector",
            version="test",
        ))

    @pytest.fixture(scope="class")
    def test_image(self):
        import numpy as np
        return np.random.randint(0, 255, (768, 1024, 3), dtype=np.uint8)

    def test_detector_p95_within_sla(self, detector, test_image):
        durations = []
        for _ in range(30):
            t0 = time.perf_counter()
            detector.run_on_array(test_image)
            durations.append((time.perf_counter() - t0) * 1000)

        p95 = _pct(durations, 95)
        p99 = _pct(durations, 99)

        assert p95 < SLA_DETECT_P95_MS, (
            f"Detector p95={p95:.0f}ms > SLA {SLA_DETECT_P95_MS}ms. "
            "Hardware may be under-provisioned or model is too large."
        )
        assert p99 < SLA_DETECT_P99_MS, (
            f"Detector p99={p99:.0f}ms > SLA {SLA_DETECT_P99_MS}ms."
        )

    def test_detector_throughput(self, detector, test_image):
        """Detector must sustain ≥ SLA_THROUGHPUT_RPS images per second."""
        n   = 10
        t0  = time.perf_counter()
        for _ in range(n):
            detector.run_on_array(test_image)
        elapsed = time.perf_counter() - t0
        rps = n / elapsed

        assert rps >= SLA_THROUGHPUT_RPS, (
            f"Detector throughput {rps:.2f} RPS < SLA {SLA_THROUGHPUT_RPS} RPS."
        )


# ---------------------------------------------------------------------------
# VLM verdict generation latency (skipped in stub mode)
# ---------------------------------------------------------------------------

@requires_perf
@requires_real_stack
class TestVLMLatencyGates:
    """VLM verdict generation must meet the SLA on the target hardware."""

    @pytest.fixture(scope="class")
    def generator(self):
        from vlm.composition import VLMConfig, build_vlm_generator
        cfg = VLMConfig(
            backend_type=os.environ.get("XRAY_VLM_BACKEND", "llama_cpp"),
            base_url=os.environ.get("XRAY_VLM_BASE_URL", "http://localhost:8080"),
            model=os.environ.get("XRAY_VLM_MODEL", "qwen3-vl-7b-q4_k_m.gguf"),
            name="qwen3-vl",
            version="test",
            verify=False,
        )
        return build_vlm_generator(cfg)

    @pytest.fixture
    def verdict_request(self):
        from tests.fixtures.builders import make_verdict_request, make_detection_result
        return make_verdict_request(make_detection_result())

    @pytest.mark.asyncio
    async def test_vlm_verdict_p95_within_sla(self, generator, verdict_request):
        durations = []
        for _ in range(10):
            t0 = time.perf_counter()
            await generator.generate(verdict_request)
            durations.append((time.perf_counter() - t0) * 1000)

        p95 = _pct(durations, 95)
        assert p95 < SLA_VERDICT_P95_MS, (
            f"VLM verdict p95={p95:.0f}ms > SLA {SLA_VERDICT_P95_MS}ms. "
            "Consider smaller model, better quantisation, or more GPU layers."
        )


# ---------------------------------------------------------------------------
# Concurrent request handling
# ---------------------------------------------------------------------------

@requires_perf
class TestConcurrency:
    """The API must handle concurrent requests without serializing them."""

    @pytest.mark.asyncio
    async def test_concurrent_health_checks_do_not_degrade(self, client):
        """10 concurrent health checks must complete in < 500ms total."""
        t0 = time.perf_counter()
        await asyncio.gather(*[client.get("/health") for _ in range(10)])
        elapsed_ms = (time.perf_counter() - t0) * 1000

        assert elapsed_ms < 500, (
            f"10 concurrent health checks took {elapsed_ms:.0f}ms > 500ms. "
            "Server may be serializing requests."
        )

    @requires_db
    @pytest.mark.asyncio
    async def test_concurrent_scan_list_requests(self, client, auth_headers):
        """5 concurrent authenticated requests must all succeed."""
        responses = await asyncio.gather(*[
            client.get("/v1/scans", headers=auth_headers) for _ in range(5)
        ])
        non_5xx = [r for r in responses if r.status_code >= 500]
        assert not non_5xx, (
            f"{len(non_5xx)} out of 5 concurrent requests returned 5xx"
        )
