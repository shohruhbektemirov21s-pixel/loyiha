"""Acquisition composition root.

Reads XRAY_ACQ_* environment variables and wires together:
    driver → ConnectionWatchdog → IngestPipeline → AcquisitionPipeline

All heavy imports (pydicom, opencv) are deferred inside the driver constructors
so this module is safe to import on any box.

Environment variables
---------------------
See CaptureConfig docstring in protocol.py for the full list.
Key variables:
    XRAY_ACQ_DRIVER         dicos | vendor_sdk | framegrab (default: dicos)
    XRAY_ACQ_SCANNER_ID     e.g. "smiths-lane-1"
    XRAY_ACQ_LANE_ID        e.g. "lane-1"
    XRAY_ACQ_MODALITY       dual_energy | single_energy | multi_view
    XRAY_ACQ_SUBJECT        baggage | cargo | vehicle | parcel | other
    XRAY_ACQ_DICOS_WATCH_DIR  /var/lib/xray/incoming
    XRAY_ACQ_GRAB_DEVICE    0  (device index or /dev/video0)
    XRAY_ACQ_GRAB_ROI       "0,0,1920,1080" (x,y,w,h)
    XRAY_ACQ_SDK_HOST       localhost
    XRAY_ACQ_SDK_PORT       5000
    XRAY_ACQ_API_BASE_URL   http://127.0.0.1:8000
    XRAY_STORE_KEY          64-hex-char AES key for SecureImageStore

Fail-closed
-----------
If driver_type is vendor_sdk or dicos and the respective library is not
installed, build_acquisition_pipeline() raises immediately (at startup)
rather than silently falling back to framegrab. The operator must make an
explicit environment decision to use the lower-fidelity path.

Vendor SDK dispatch
-------------------
XRAY_ACQ_SDK_VENDOR selects which vendor SDK driver to load:
    smiths  → SmithsDetectionDriver
    l3      → L3LeidosDriver
    (unset) → raises with instructions to set the variable
"""

from __future__ import annotations

import logging
import os

from contracts.v1 import ImageModality, ScanSubject
from acquisition.protocol import CaptureConfig, DriverType

log = logging.getLogger("xray.acquisition.composition")


def _env(key: str, default: str | None = None) -> str | None:
    return os.environ.get(key, default)


def _require_env(key: str) -> str:
    val = os.environ.get(key)
    if not val:
        raise RuntimeError(
            f"Required environment variable {key} is not set. "
            f"Set it before starting the acquisition pipeline."
        )
    return val


def _parse_roi(raw: str | None) -> tuple[int, int, int, int] | None:
    if not raw:
        return None
    try:
        parts = [int(p.strip()) for p in raw.split(",")]
        if len(parts) != 4:
            raise ValueError("Expected 4 values")
        return tuple(parts)  # type: ignore[return-value]
    except ValueError as exc:
        raise RuntimeError(
            f"XRAY_ACQ_GRAB_ROI must be 'x,y,w,h' integers, got: {raw!r}"
        ) from exc


def _build_config(
    scanner_id_override: str | None,
    lane_id_override: str | None,
    driver_override: str | None,
) -> CaptureConfig:
    driver_raw = (
        driver_override
        or _env("XRAY_ACQ_DRIVER", "dicos")
    ).lower()

    try:
        driver_type = DriverType(driver_raw)
    except ValueError:
        raise RuntimeError(
            f"XRAY_ACQ_DRIVER={driver_raw!r} is not valid. "
            f"Choose from: {', '.join(d.value for d in DriverType)}"
        )

    try:
        modality = ImageModality(_env("XRAY_ACQ_MODALITY", "dual_energy"))
    except ValueError:
        raise RuntimeError(
            f"XRAY_ACQ_MODALITY={_env('XRAY_ACQ_MODALITY')!r} is not valid."
        )

    try:
        subject = ScanSubject(_env("XRAY_ACQ_SUBJECT", "baggage"))
    except ValueError:
        raise RuntimeError(
            f"XRAY_ACQ_SUBJECT={_env('XRAY_ACQ_SUBJECT')!r} is not valid."
        )

    return CaptureConfig(
        driver_type=driver_type,
        scanner_id=scanner_id_override or _env("XRAY_ACQ_SCANNER_ID", "scanner-01"),
        lane_id=lane_id_override or _env("XRAY_ACQ_LANE_ID"),
        modality=modality,
        subject=subject,
        operator_id=_env("XRAY_ACQ_OPERATOR_ID"),
        dicos_watch_dir=_env("XRAY_ACQ_DICOS_WATCH_DIR", "/var/lib/xray/incoming"),
        dicos_move_dir=_env("XRAY_ACQ_DICOS_MOVE_DIR"),
        dicos_poll_s=float(_env("XRAY_ACQ_DICOS_POLL_S", "0.5")),
        grab_device=int(_env("XRAY_ACQ_GRAB_DEVICE", "0"))
            if _env("XRAY_ACQ_GRAB_DEVICE", "0").isdigit()
            else _env("XRAY_ACQ_GRAB_DEVICE", "0"),
        grab_fps=int(_env("XRAY_ACQ_GRAB_FPS", "30")),
        grab_roi=_parse_roi(_env("XRAY_ACQ_GRAB_ROI")),
        grab_stable_frames=int(_env("XRAY_ACQ_GRAB_STABLE_FRAMES", "8")),
        grab_stable_thresh=int(_env("XRAY_ACQ_GRAB_STABLE_THRESH", "6")),
        grab_scan_timeout_s=float(_env("XRAY_ACQ_GRAB_SCAN_TIMEOUT", "30.0")),
        sdk_host=_env("XRAY_ACQ_SDK_HOST", "localhost"),
        sdk_port=int(_env("XRAY_ACQ_SDK_PORT", "5000")),
        reconnect_attempts=int(_env("XRAY_ACQ_RECONNECT_ATTEMPTS", "10")),
        reconnect_delay_s=float(_env("XRAY_ACQ_RECONNECT_DELAY_S", "5.0")),
        frame_timeout_s=float(_env("XRAY_ACQ_FRAME_TIMEOUT_S", "60.0")),
        api_base_url=_env("XRAY_ACQ_API_BASE_URL", "http://127.0.0.1:8000"),
        api_timeout_s=float(_env("XRAY_ACQ_API_TIMEOUT_S", "30.0")),
    )


def _build_driver(cfg: CaptureConfig):
    """Instantiate the driver for the configured path."""
    if cfg.driver_type == DriverType.DICOS:
        from acquisition.dicos import DICOSDriver
        return DICOSDriver(cfg)

    if cfg.driver_type == DriverType.FRAMEGRAB:
        log.warning(
            "FRAMEGRAB path selected. This captures the rendered RGB display "
            "output only — NOT raw dual-energy data. Material discrimination "
            "is limited. See acquisition/README.md §RGB-vs-raw."
        )
        from acquisition.framegrab import FrameGrabDriver
        return FrameGrabDriver(cfg)

    if cfg.driver_type == DriverType.VENDOR_SDK:
        vendor = _env("XRAY_ACQ_SDK_VENDOR", "").lower()
        if vendor == "smiths":
            from acquisition.sdk.smiths import SmithsDetectionDriver
            return SmithsDetectionDriver(cfg)
        if vendor in ("l3", "leidos"):
            from acquisition.sdk.l3 import L3LeidosDriver
            return L3LeidosDriver(cfg)
        raise RuntimeError(
            f"XRAY_ACQ_SDK_VENDOR={vendor!r} is not recognised. "
            f"Set it to 'smiths' or 'l3'. "
            f"For other vendors, implement a VendorSDKBase subclass in "
            f"acquisition/sdk/ and register it here."
        )

    raise RuntimeError(f"Unhandled driver type: {cfg.driver_type}")


def _build_ingest_pipeline(cfg: CaptureConfig):
    """Build IngestPipeline backed by SecureImageStore from env config."""
    from datalayer.storage import SecureImageStore
    from datalayer.ingestion import IngestConfig, IngestPipeline

    store_key = _env("XRAY_STORE_KEY")
    if not store_key:
        raise RuntimeError(
            "XRAY_STORE_KEY is not set. "
            "Provide a 64-hex-char AES-256 key for the encrypted image store."
        )

    store_dir = _env("XRAY_STORE_DIR", "/var/lib/xray/store")
    store = SecureImageStore(store_dir, bytes.fromhex(store_key))

    ingest_cfg = IngestConfig(
        scanner_id=cfg.scanner_id,
        lane_id=cfg.lane_id or cfg.scanner_id,
        modality=cfg.modality,
        subject=cfg.subject,
        operator_id=cfg.operator_id,
    )
    return IngestPipeline(store=store, config=ingest_cfg)


def build_acquisition_pipeline(
    scanner_id_override: str | None = None,
    lane_id_override: str | None = None,
    driver_override: str | None = None,
):
    """Composition root: build and connect a fully wired AcquisitionPipeline.

    Call at process startup. Raises on any misconfiguration so the process
    fails fast rather than running with a broken seam.
    """
    from acquisition.watchdog import ConnectionWatchdog
    from acquisition.pipeline import AcquisitionPipeline

    cfg = _build_config(scanner_id_override, lane_id_override, driver_override)
    log.info(
        "Building acquisition pipeline: driver=%s scanner=%s lane=%s",
        cfg.driver_type.value, cfg.scanner_id, cfg.lane_id,
    )

    driver  = _build_driver(cfg)
    dog     = ConnectionWatchdog(driver, cfg)
    ingest  = _build_ingest_pipeline(cfg)

    dog.connect()   # fail-closed: raises ScannerConnectionError on bad config

    return AcquisitionPipeline(
        watchdog=dog,
        ingest=ingest,
        cfg=cfg,
        api_retries=int(_env("XRAY_ACQ_API_RETRIES", "3")),
    )


__all__ = ["build_acquisition_pipeline"]
