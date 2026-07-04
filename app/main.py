"""FastAPI application factory for the X-ray assistant serving layer.

This is the unifying API between the four layers. Today it exposes the two
internal model-serving endpoints (`/v1/detect`, `/v1/verdict`) as typed
skeletons backed by not-yet-implemented seams, plus health. The NiceGUI operator
console will mount onto this same app later (it runs on FastAPI), so the whole
team stays one process, one language.

Run:  uvicorn app.main:app --reload
Docs: http://127.0.0.1:8000/docs   (OpenAPI is the live integration contract)
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from contracts.v1 import SCHEMA_VERSION

from app.api.v1 import api_v1
from app.deps import provide_detector, provide_verdict_generator
from app.settings import Settings, get_settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("xray.app")


def _wire_db(app: FastAPI, settings: Settings) -> None:
    """Initialise PostgreSQL, audit sink, threshold cache, and WebSocket hub.

    Off by default: when XRAY_DB_URL is unset the seams fall back to their
    stubs (null scan store, logging audit sink). Fail-closed when enabled:
    a bad DSN or missing HMAC key aborts startup.
    """
    from app.api.v1.ws import init_hub
    init_hub()
    log.info("WebSocket hub initialised.")

    if not settings.db_url:
        log.info("DB seam: disabled (XRAY_DB_URL not set) — stubs active.")
        return

    from app.db.session import init_db
    from app.db.session import get_session_factory
    from app.audit.sink import build_audit_sink
    from app.state.thresholds import ThresholdCache
    import app.deps as deps

    init_db(
        settings.db_url,
        echo=settings.db_echo,
        pool_size=settings.db_pool_size,
        ssl=settings.db_ssl,
    )

    session_factory = get_session_factory()

    # Build and wire the PostgreSQL audit sink.
    hmac_key = settings.audit_hmac_key or None
    audit_sink = build_audit_sink(session_factory, hmac_key)
    app.dependency_overrides[deps.provide_audit_sink] = lambda: audit_sink

    # Threshold cache (shared across requests).
    threshold_cache = ThresholdCache(session_factory)
    deps._threshold_cache = threshold_cache

    # Enable the per-request ScanStore.
    deps.enable_db()

    log.info("DB seam: LIVE (pool=%d ssl=%s)", settings.db_pool_size, settings.db_ssl)


def _wire_detector(app: FastAPI, settings: Settings) -> None:
    """Composition root for Hop 2. Builds the real detector and overrides the
    seam — but only when explicitly enabled (the GPU serving box). Fail-closed:
    if enabled and the model can't load, the exception propagates and startup
    aborts. We never boot into a silent 501 on a box that's supposed to detect.
    """
    if not settings.detector_enabled:
        log.info("detector seam: stub (501) — XRAY_DETECTOR_ENABLED is off")
        return

    # Import the ML-side composition root lazily so the API package never pulls
    # in torch/cv2 at import time on a box without the stack.
    from detector.serving.composition import DetectorConfig, build_detector

    cfg = DetectorConfig(
        weights=settings.detector_weights,
        device=settings.detector_device,
        imgsz=settings.detector_imgsz,
        conf=settings.detector_conf,
        iou=settings.detector_iou,
        name=settings.detector_name,
        version=settings.detector_version,
        runtime=settings.detector_runtime,
        calibration=settings.detector_calibration,
        verify_sha256=settings.detector_verify_sha256,
    )
    detector = build_detector(cfg)
    app.dependency_overrides[provide_detector] = lambda: detector
    log.info("detector seam: LIVE (%s v%s)", cfg.name, cfg.version)


def _wire_vlm(app: FastAPI, settings: Settings) -> None:
    """Composition root for Hop 3. Builds the Qwen3-VL generator and overrides
    the seam — only when XRAY_VLM_ENABLED is set (the VLM serving box).
    Fail-closed: if enabled and the backend is unreachable, startup aborts.
    We never boot into a silent 501 on a box that's supposed to generate verdicts.
    """
    if not settings.vlm_enabled:
        log.info("VLM seam: stub (501) — XRAY_VLM_ENABLED is off")
        return

    from vlm.composition import VLMConfig, build_vlm_generator

    is_local = settings.vlm_backend.lower() in ("transformers", "local", "direct")

    # For the transformers backend the "base_url" carries the model path;
    # startup probe is skipped (model loads lazily on first generate() call).
    effective_base_url = (
        settings.vlm_model_path if is_local else settings.vlm_base_url
    )
    effective_verify = settings.vlm_verify and not is_local

    # Propagate torch device/dtype to env so build_backend() picks them up.
    import os
    if is_local:
        os.environ.setdefault("XRAY_VLM_MODEL_PATH", settings.vlm_model_path)
        os.environ.setdefault("XRAY_VLM_DEVICE_MAP",  settings.vlm_device_map)
        os.environ.setdefault("XRAY_VLM_TORCH_DTYPE", settings.vlm_torch_dtype)
        os.environ.setdefault("HF_HUB_OFFLINE",        "1")
        os.environ.setdefault("TRANSFORMERS_OFFLINE",   "1")

    cfg = VLMConfig(
        backend_type=settings.vlm_backend,
        base_url=effective_base_url,
        model=settings.vlm_model,
        timeout_s=settings.vlm_timeout_s,
        temperature=settings.vlm_temperature,
        max_tokens=settings.vlm_max_tokens,
        name=settings.vlm_name,
        version=settings.vlm_version,
        store_root=settings.vlm_store_root,
        verify=effective_verify,
        describe=settings.vlm_describe,
    )
    generator = build_vlm_generator(cfg)
    app.dependency_overrides[provide_verdict_generator] = lambda: generator
    log.info("VLM seam: LIVE (%s v%s via %s)", cfg.name, cfg.version, cfg.backend_type)


def _wire_screener(app: FastAPI, settings: Settings) -> None:
    """Composition root: operator rasm-yuklash skrining oqimi (``/v1/screen``).

    XRAY_VLM_ENABLED bo'lsa Qwen3-VL backend (xuddi _wire_vlm dagi sozlamalar)
    bilan ``CargoScreener`` quriladi va ``provide_screener`` override qilinadi.
    O'chirilgan bo'lsa stub qoladi -> endpoint 501 (fail-closed), jim soxta emas.

    Bu _wire_vlm dan ALOHIDA: u OperatorVerdict generatorini (Hop 3, detektordan
    keyin matn) quradi; bu esa standalone rentgen rasm skriningini quradi. Ikkalasi
    bir xil backend sozlamalarini ishlatadi.
    """
    if not settings.vlm_enabled:
        log.info("screener seam: stub (501) — XRAY_VLM_ENABLED is off")
        return

    from vlm.backend import build_backend
    from vlm.screen import CargoScreener
    from app.deps import provide_screener

    is_local = settings.vlm_backend.lower() in ("transformers", "local", "direct")
    effective_base_url = (
        settings.vlm_model_path if is_local else settings.vlm_base_url
    )

    backend = build_backend(
        settings.vlm_backend,
        base_url=effective_base_url,
        model=settings.vlm_model,
        timeout_s=settings.vlm_timeout_s,
    )
    screener = CargoScreener(
        backend=backend,
        temperature=settings.vlm_temperature,
        max_tokens=settings.vlm_max_tokens,
        passes=max(1, settings.screen_passes),
    )
    app.dependency_overrides[provide_screener] = lambda: screener
    log.info(
        "screener seam: LIVE (%s via %s)", settings.vlm_model, settings.vlm_backend
    )


def _wire_datalayer(app: FastAPI, settings: Settings) -> None:
    """Composition root for Hop 4. Wires the ActiveLearningLoop as the FeedbackSink.

    OFF by default: when XRAY_DATALAYER_ENABLED is unset the FeedbackSink stays
    the 501 stub. Set it to 1 on any box that should close the active-learning loop.
    The data directories are created automatically.
    """
    if not settings.datalayer_enabled:
        log.info("datalayer seam: stub (501) — XRAY_DATALAYER_ENABLED is off")
        return

    from app.datalayer_wiring import build_feedback_sink
    import app.deps as deps

    data = settings.data_dir
    sink = build_feedback_sink(
        queue_dir=settings.queue_dir or f"{data}/queue",
        version_dir=settings.version_dir or f"{data}/versions",
        job_dir=settings.job_dir or f"{data}/jobs",
        dataset_target=settings.dataset_target,
    )
    deps._feedback_sink = sink
    log.info("datalayer seam: LIVE (queue=%s)", settings.queue_dir or f"{data}/queue")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    log.info("starting %s env=%s contract=v%s", settings.app_name, settings.environment, SCHEMA_VERSION)
    _wire_db(app, settings)
    _wire_detector(app, settings)
    _wire_vlm(app, settings)
    _wire_screener(app, settings)
    _wire_datalayer(app, settings)
    yield
    # Kamera uzluksiz oqimini to'xtatamiz — capture thread va VideoCapture
    # to'g'ri release qilinsin, thread leak bo'lmasin.
    try:
        from app.api.v1.camera import get_stream_manager
        await get_stream_manager().stop()
    except Exception as exc:  # noqa: BLE001 — shutdown'da xato boshqa tozalashni to'smasin
        log.warning("kamera oqimini to'xtatishda xato: %s", exc)
    from app.db.session import close_db
    await close_db()
    log.info("shutting down %s", settings.app_name)


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="X-ray Assistant API",
        version=f"contract-{SCHEMA_VERSION}",
        summary="Decision-support model-serving layer. Operator decides; system advises.",
        docs_url=settings.docs_url,
        redoc_url=None,
        openapi_url=settings.openapi_url,
        lifespan=lifespan,
    )

    if settings.cors_allow_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_allow_origins,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    app.include_router(api_v1)

    # Prometheus instrumentation (request middleware + /metrics endpoint).
    # Without this call the metrics module is dead code: the scrape target
    # defined in deploy/prometheus/prometheus.yml would 404. Best-effort: on a
    # box without prometheus-client (e.g. the contract-only box) we log and
    # carry on rather than refuse to boot over an observability dependency.
    try:
        from app.metrics import instrument_app
        instrument_app(app)
    except ImportError:
        log.warning("prometheus-client not installed — /metrics endpoint disabled.")

    # ---- Global exception handlers ----------------------------------------
    # Consistent error envelope: {"error": "<type>", "detail": "<message>"}
    # prevents leaking stack traces and gives clients a stable shape to parse.

    from sqlalchemy.exc import IntegrityError, OperationalError

    from app.db.session import DatabaseNotInitialised

    @app.exception_handler(DatabaseNotInitialised)
    async def _db_not_initialised(_req: Request, exc: DatabaseNotInitialised) -> JSONResponse:
        # A DB-backed route was hit while persistence is unwired (stub mode).
        # Fail-closed: 503 (unavailable), never 500 (which reads as "broken").
        log.warning("DB-backed route hit but persistence not wired: %s", exc)
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"error": "db_unavailable", "detail": "Persistence layer is not configured."},
        )

    @app.exception_handler(IntegrityError)
    async def _db_integrity(_req: Request, exc: IntegrityError) -> JSONResponse:
        log.warning("DB integrity error: %s", exc.orig)
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={"error": "conflict", "detail": "A record with this ID already exists."},
        )

    @app.exception_handler(OperationalError)
    async def _db_operational(_req: Request, exc: OperationalError) -> JSONResponse:
        log.error("DB operational error: %s", exc.orig, exc_info=True)
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"error": "db_unavailable", "detail": "Database temporarily unavailable."},
        )

    @app.exception_handler(Exception)
    async def _unhandled(_req: Request, exc: Exception) -> JSONResponse:
        log.error("Unhandled exception: %s", exc, exc_info=True)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"error": "internal_error", "detail": "An unexpected error occurred."},
        )

    @app.get("/health", tags=["meta"], summary="Liveness + contract version")
    async def health() -> dict:
        from app.deps import _db_enabled
        return {
            "status": "ok",
            "contract_version": SCHEMA_VERSION,
            "environment": settings.environment,
            "db": "live" if _db_enabled else "stub",
        }

    # ---- Operator console (co-hosted, same-origin) ------------------------
    # Serve the built React console from this same app so the frontend calls
    # its own origin's /v1/* — no separate host, no cross-origin URL to
    # configure, no CORS. Mounted LAST so /v1, /health, /docs, /openapi.json
    # keep precedence. Guarded: on a box without a build (contract-only / dev)
    # we simply skip it. The console is built with an empty VITE_API_URL so its
    # API base is the relative "/v1".
    from pathlib import Path as _Path

    _console_dist = _Path(__file__).resolve().parent.parent / "console" / "dist"
    if (_console_dist / "index.html").is_file():
        from fastapi.staticfiles import StaticFiles

        app.mount("/", StaticFiles(directory=str(_console_dist), html=True), name="console")
        log.info("operator console mounted at / (%s)", _console_dist)
    else:
        log.info("operator console not built (%s missing) — API-only.", _console_dist)

    return app


app = create_app()
