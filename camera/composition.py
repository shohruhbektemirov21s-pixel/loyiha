"""Camera composition root — reads XRAY_CAM_* env vars and builds a driver.

Environment variables
---------------------
XRAY_CAM_DEVICE         Device index or path (default: 0)
XRAY_CAM_WIDTH          Capture width  (default: 1280)
XRAY_CAM_HEIGHT         Capture height (default: 720)
XRAY_CAM_FPS            FPS (default: 30)
XRAY_CAM_ROI            "x,y,w,h" crop region (optional)
XRAY_CAM_ENCODE_QUAL    JPEG quality 1-95 (default: 90)
XRAY_CAM_MOTION_THRESH  Motion detection threshold (default: 20)
XRAY_CAM_STABLE_FRAMES  Stable frames before snapshot (default: 6)
XRAY_CAM_SCAN_TIMEOUT   Max seconds to wait for snapshot (default: 30)
XRAY_CAM_OUT_DIR        Folder where captured frames are saved
                        (default: <repo>/camera/captures)

Example
-------
    # List available cameras
    python -c "from camera.driver import list_cameras; print(list_cameras())"

    # Run capture loop:
    XRAY_CAM_DEVICE=0 XRAY_CAM_ROI="0,0,1280,720" python -m camera.cli
"""

from __future__ import annotations

import logging
import os
import pathlib

from camera.driver import CameraConfig, USBCameraDriver

log = logging.getLogger("xray.camera.composition")

# Default capture folder shipped with the repo (camera/captures).
_DEFAULT_OUT_DIR = pathlib.Path(__file__).resolve().parent / "captures"


def _env(key: str, default: str) -> str:
    return os.environ.get(key, default)


def capture_out_dir() -> pathlib.Path:
    """Resolve the folder where camera frames are written and ensure it exists.

    Controlled by XRAY_CAM_OUT_DIR; defaults to ``<repo>/camera/captures``.
    """
    raw = os.environ.get("XRAY_CAM_OUT_DIR", "").strip()
    out = pathlib.Path(raw) if raw else _DEFAULT_OUT_DIR
    out.mkdir(parents=True, exist_ok=True)
    return out


def _parse_roi(raw: str | None) -> tuple[int, int, int, int] | None:
    if not raw:
        return None
    try:
        parts = [int(p.strip()) for p in raw.split(",")]
        if len(parts) != 4:
            raise ValueError
        return tuple(parts)  # type: ignore[return-value]
    except ValueError:
        raise RuntimeError(
            f"XRAY_CAM_ROI must be 'x,y,w,h' integers, got: {raw!r}"
        )


def build_camera_config() -> CameraConfig:
    """Build CameraConfig from XRAY_CAM_* environment variables."""
    device_raw = _env("XRAY_CAM_DEVICE", "0")
    device: int | str = int(device_raw) if device_raw.isdigit() else device_raw

    return CameraConfig(
        device=device,
        width=int(_env("XRAY_CAM_WIDTH",          "1280")),
        height=int(_env("XRAY_CAM_HEIGHT",         "720")),
        fps=int(_env("XRAY_CAM_FPS",               "30")),
        roi=_parse_roi(_env("XRAY_CAM_ROI",        "")),
        encode_quality=int(_env("XRAY_CAM_ENCODE_QUAL",    "90")),
        motion_thresh=int(_env("XRAY_CAM_MOTION_THRESH",   "20")),
        stable_frames=int(_env("XRAY_CAM_STABLE_FRAMES",   "6")),
        scan_timeout_s=float(_env("XRAY_CAM_SCAN_TIMEOUT", "30.0")),
    )


def build_camera_driver() -> USBCameraDriver:
    """Build and open a USBCameraDriver from environment configuration.

    Raises ``CameraOpenError`` if the device cannot be opened.
    """
    cfg    = build_camera_config()
    driver = USBCameraDriver(cfg)

    log.info(
        "Camera driver built: device=%s %dx%d fps=%d roi=%s",
        cfg.device, cfg.width, cfg.height, cfg.fps, cfg.roi,
    )
    return driver


__all__ = ["build_camera_config", "build_camera_driver", "capture_out_dir"]
