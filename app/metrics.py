"""Prometheus instrumentation for the X-ray API.

Exposes /metrics endpoint using prometheus_client.
All metric names are prefixed with xray_ to avoid collision with
default process/python metrics.

Wire into FastAPI via app/main.py:
    from app.metrics import instrument_app
    instrument_app(app)
"""

from __future__ import annotations

import time
from typing import Callable

from fastapi import FastAPI, Request, Response
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
    REGISTRY,
)

# ---------------------------------------------------------------------------
# Metric definitions
# ---------------------------------------------------------------------------

# ── Scan / detection ─────────────────────────────────────────────────────────
DETECTIONS_TOTAL = Counter(
    "xray_detections_total",
    "Total number of detections emitted by the detector",
    ["category", "lane_id"],
)

DETECTION_SCORE = Histogram(
    "xray_detection_score",
    "Distribution of raw detector confidence scores",
    ["category"],
    buckets=[0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 0.95, 1.0],
)

SCAN_E2E_DURATION = Histogram(
    "xray_scan_e2e_duration_seconds",
    "End-to-end duration from acquisition receipt to verdict generation",
    buckets=[0.5, 1, 2, 5, 10, 20, 30, 60, 120],
)

# ── Operator feedback / judgements ───────────────────────────────────────────
FEEDBACK_TOTAL = Counter(
    "xray_feedback_total",
    "Total number of operator feedback submissions",
    ["outcome", "lane_id"],
)

DETECTION_JUDGEMENT_TOTAL = Counter(
    "xray_detection_judgement_total",
    "Operator judgements on individual detections",
    ["judgement", "lane_id"],
)

MISSED_REGION_TOTAL = Counter(
    "xray_missed_region_total",
    "Operator-annotated missed detections (false negatives)",
    ["category", "lane_id"],
)

# ── VLM / detector inference ─────────────────────────────────────────────────
VLM_INFERENCE_DURATION = Histogram(
    "xray_vlm_inference_duration_seconds",
    "VLM verdict generation wall-clock time",
    buckets=[0.5, 1, 2, 5, 10, 20, 30, 60],
)

DETECTOR_INFERENCE_DURATION = Histogram(
    "xray_detector_inference_duration_seconds",
    "Detector inference wall-clock time",
    buckets=[0.05, 0.1, 0.25, 0.5, 1, 2, 5],
)

# ── Auth ─────────────────────────────────────────────────────────────────────
AUTH_FAILURE_TOTAL = Counter(
    "xray_auth_failure_total",
    "Authentication failures",
    ["reason"],
)

# ── HTTP ─────────────────────────────────────────────────────────────────────
HTTP_REQUESTS_TOTAL = Counter(
    "xray_http_requests_total",
    "Total HTTP requests handled by the API",
    ["method", "path", "status"],
)

HTTP_REQUEST_DURATION = Histogram(
    "xray_http_request_duration_seconds",
    "HTTP request duration",
    ["method", "path"],
    buckets=[0.01, 0.05, 0.1, 0.25, 0.5, 1, 2, 5],
)

# ── Model integrity ──────────────────────────────────────────────────────────
MODEL_WEIGHT_VALID = Gauge(
    "xray_model_weight_valid",
    "1 if model weight SHA-256 checksum passed at startup, 0 if failed",
    ["model"],
)

# ── Audit chain ──────────────────────────────────────────────────────────────
AUDIT_CHAIN_VALID = Gauge(
    "xray_audit_chain_valid",
    "1 if last audit chain verification passed, 0 if failed",
)
AUDIT_CHAIN_VALID.set(1)   # assume valid until proven otherwise

# ── Backup ────────────────────────────────────────────────────────────────────
LAST_BACKUP_TIMESTAMP = Gauge(
    "xray_last_backup_timestamp_seconds",
    "Unix timestamp of last successful backup (pushed by backup.sh via pushgateway)",
)

# ---------------------------------------------------------------------------
# FastAPI middleware
# ---------------------------------------------------------------------------

_SKIP_PATHS = {"/metrics", "/health", "/favicon.ico"}

def _instrument_middleware(app: FastAPI) -> None:
    @app.middleware("http")
    async def prometheus_middleware(request: Request, call_next: Callable) -> Response:
        path = request.url.path

        # Normalise dynamic path segments to avoid high cardinality
        # e.g. /v1/scans/uuid → /v1/scans/{scan_id}
        norm_path = _normalise_path(path)

        if path in _SKIP_PATHS:
            return await call_next(request)

        start = time.perf_counter()
        response: Response = await call_next(request)
        duration = time.perf_counter() - start

        HTTP_REQUESTS_TOTAL.labels(
            method=request.method,
            path=norm_path,
            status=str(response.status_code),
        ).inc()

        HTTP_REQUEST_DURATION.labels(
            method=request.method,
            path=norm_path,
        ).observe(duration)

        return response


def _normalise_path(path: str) -> str:
    import re
    # Replace UUIDs with {id}
    path = re.sub(
        r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
        "{id}",
        path,
        flags=re.IGNORECASE,
    )
    return path


def _add_metrics_endpoint(app: FastAPI) -> None:
    @app.get("/metrics", include_in_schema=False)
    async def metrics() -> Response:
        return Response(
            content=generate_latest(REGISTRY),
            media_type=CONTENT_TYPE_LATEST,
        )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def instrument_app(app: FastAPI) -> None:
    """Wire Prometheus middleware and /metrics endpoint into a FastAPI app."""
    _instrument_middleware(app)
    _add_metrics_endpoint(app)


def record_model_integrity(model: str, valid: bool) -> None:
    """Call from startup to record model weight verification result."""
    MODEL_WEIGHT_VALID.labels(model=model).set(1 if valid else 0)


def record_auth_failure(reason: str = "invalid_credentials") -> None:
    AUTH_FAILURE_TOTAL.labels(reason=reason).inc()


def record_audit_chain_valid(valid: bool) -> None:
    AUDIT_CHAIN_VALID.set(1 if valid else 0)
