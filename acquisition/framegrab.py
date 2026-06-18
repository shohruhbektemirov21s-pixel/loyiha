"""HDMI frame grabber driver — OpenCV capture fallback path.

⚠️  RGB-ONLY / RENDERED-DISPLAY LIMITATION — READ BEFORE DEPLOYING
====================================================================
This path captures the X-ray scanner's **operator monitor output** via an
HDMI frame grabber device (e.g., Magewell USB Capture, Epiphan DVI2USB,
AVerMedia).  What arrives is the **scanner's rendered RGB image** — the
visual representation the scanner software already produced for human eyes.

This is NOT the raw sensor data. Specific implications:

  1. No dual-energy separation. The scanner internally computed a blended
     or colourised display. High-energy and low-energy channels are
     irreversibly merged into the RGB image.
  2. No raw attenuation coefficients. Material decomposition algorithms
     (e.g., organic vs. inorganic discrimination by Hounsfield-equivalent)
     are not possible on this data.
  3. Colour mapping is scanner-model-specific and setting-dependent.
     Smiths Detection, L3, and Leidos each use different colour palettes.
     A model trained on Smiths output may not generalise to L3 output.
  4. 8-bit depth per channel. Scanner raw data is typically 12–16 bit.
     Quantisation during display lossily compresses high-contrast regions.
  5. Any on-screen overlays (alarm boxes, operator annotations, timestamps)
     are burned into the image. The detector must be tolerant of them.

This path exists so the system is not blocked by closed hardware. Always
prefer the DICOS or vendor SDK path when the scanner grants data access.

The ``CaptureMetadata.is_raw_dual_energy`` flag is structurally set to
``False`` for every frame produced by this driver — this is non-negotiable
and cannot be overridden by configuration.
====================================================================

Frame capture state machine
----------------------------
WAITING   → conveyor is empty / scanner idle (consecutive frames are static)
SCANNING  → object on belt (frames are changing)
STABLE    → object has passed; display is stable for N consecutive frames
CAPTURED  → best stable frame grabbed; waiting for display to change again

A scan is emitted on STABLE → CAPTURED.  The "stable frame" is the median
of the stable window (reduces noise from brief display redraws).

ROI crop
--------
Set XRAY_ACQ_GRAB_ROI="x,y,w,h" to crop the scanner display area out of the
full-screen capture.  Without an ROI the full frame grabber resolution is
ingested, which may include taskbars, window decorations, and other monitors.

OpenCV is lazy-imported; the module is safe to import on any box.
"""

from __future__ import annotations

import hashlib
import io
import logging
import threading
import time
from dataclasses import dataclass
from enum import Enum, auto
from typing import TYPE_CHECKING

from acquisition.protocol import (
    CaptureConfig,
    CaptureMetadata,
    DriverType,
    FrameLabel,
    ScanBundle,
    ScannerConnectionError,
    ScannerFrameError,
    ScannerTimeoutError,
)
from datalayer.ingestion import RawFrame

log = logging.getLogger("xray.acquisition.framegrab")

# Mandatory fidelity note appended to every frame produced by this driver.
_FIDELITY_NOTE = (
    "FRAMEGRAB: rendered RGB display capture. "
    "Raw dual-energy data unavailable. "
    "Material discrimination limited to scanner-assigned colour palette. "
    "See acquisition/README.md §RGB-vs-raw."
)


# ---------------------------------------------------------------------------
# Capture state machine
# ---------------------------------------------------------------------------
class _CaptureState(Enum):
    WAITING  = auto()   # belt empty; image is static background
    SCANNING = auto()   # object detected; image is changing
    STABLE   = auto()   # image stable for N frames → candidate scan
    CAPTURED = auto()   # frame grabbed; waiting for belt to clear


@dataclass
class _FrameWindow:
    """Sliding window of recent captured frames for stability detection."""
    frames: list  # list of numpy arrays
    max_size: int = 8

    def push(self, frame) -> None:
        self.frames.append(frame)
        if len(self.frames) > self.max_size:
            self.frames.pop(0)

    def mean_diff(self) -> float:
        """Mean absolute pixel difference between consecutive frames."""
        import numpy as np
        if len(self.frames) < 2:
            return 255.0
        diffs = [
            float(np.mean(np.abs(self.frames[i].astype(np.int32) - self.frames[i-1].astype(np.int32))))
            for i in range(1, len(self.frames))
        ]
        return sum(diffs) / len(diffs)

    def is_full(self) -> bool:
        return len(self.frames) >= self.max_size

    def median_frame(self):
        """Element-wise median across the window — suppresses transient noise."""
        import numpy as np
        return np.median(np.stack(self.frames, axis=0), axis=0).astype(np.uint8)


# ---------------------------------------------------------------------------
# Frame grabber driver
# ---------------------------------------------------------------------------
class FrameGrabDriver:
    """Captures scans from an HDMI frame grabber via OpenCV VideoCapture.

    ⚠ Every frame produced carries ``is_raw_dual_energy=False`` and the
    mandatory ``_FIDELITY_NOTE``.  This is not configurable.
    """

    def __init__(self, cfg: CaptureConfig) -> None:
        self._cfg = cfg
        self._cap = None          # cv2.VideoCapture
        self._lock = threading.Lock()
        self._connected = False

    @property
    def driver_type(self) -> DriverType:
        return DriverType.FRAMEGRAB

    @property
    def is_connected(self) -> bool:
        return self._connected and self._cap is not None and self._cap.isOpened()

    def connect(self) -> None:
        try:
            import cv2
        except ImportError as exc:
            raise ScannerConnectionError(
                "opencv-python-headless is not installed. "
                "Install it on the capture box: pip install opencv-python-headless"
            ) from exc

        device = self._cfg.grab_device
        cap = cv2.VideoCapture(device)
        if not cap.isOpened():
            raise ScannerConnectionError(
                f"Cannot open frame grabber device '{device}'. "
                f"Check USB/PCIe connection and device permissions (/dev/video*)."
            )
        cap.set(cv2.CAP_PROP_FPS, self._cfg.grab_fps)
        self._cap = cap
        self._connected = True
        log.info(
            "FrameGrab driver connected: device=%s fps=%d roi=%s",
            device, self._cfg.grab_fps, self._cfg.grab_roi,
        )
        log.warning(
            "FrameGrab: RGB display capture active. "
            "Raw dual-energy data unavailable. See acquisition/README.md §RGB-vs-raw."
        )

    def disconnect(self) -> None:
        if self._cap is not None:
            self._cap.release()
            self._cap = None
        self._connected = False

    # ------------------------------------------------------------------
    def read_scan(self, timeout_s: float = 60.0) -> ScanBundle:
        """Run the stability FSM until a complete scan frame is captured."""
        import cv2
        import numpy as np

        if not self.is_connected:
            raise ScannerConnectionError("Frame grabber not connected.")

        cfg = self._cfg
        window = _FrameWindow(frames=[], max_size=cfg.grab_stable_frames)
        state = _CaptureState.WAITING
        deadline = time.monotonic() + timeout_s
        last_state_change = time.monotonic()

        while time.monotonic() < deadline:
            with self._lock:
                ok, raw = self._cap.read()
            if not ok or raw is None:
                raise ScannerFrameError("Frame grabber read() returned no frame.")

            frame = self._apply_roi(raw, cfg.grab_roi)

            window.push(frame)
            diff = window.mean_diff()

            prev_state = state

            if state == _CaptureState.WAITING:
                if diff > cfg.grab_stable_thresh:
                    state = _CaptureState.SCANNING
            elif state == _CaptureState.SCANNING:
                if window.is_full() and diff <= cfg.grab_stable_thresh:
                    state = _CaptureState.STABLE
            elif state == _CaptureState.STABLE:
                if diff > cfg.grab_stable_thresh:
                    # Image changed again before we captured — restart
                    state = _CaptureState.SCANNING
                elif window.is_full():
                    state = _CaptureState.CAPTURED
            elif state == _CaptureState.CAPTURED:
                # Wait for belt to clear (image starts changing again)
                if diff > cfg.grab_stable_thresh:
                    state = _CaptureState.WAITING

            if prev_state != state:
                log.debug("FrameGrab FSM: %s → %s (diff=%.2f)", prev_state.name, state.name, diff)
                last_state_change = time.monotonic()

            if state == _CaptureState.CAPTURED:
                return self._emit_bundle(window.median_frame())

            # Throttle to ~capture_fps
            time.sleep(1.0 / max(cfg.grab_fps, 1))

        raise ScannerTimeoutError(
            f"FrameGrab: no stable scan frame appeared within {timeout_s:.0f}s. "
            f"Last FSM state: {state.name}. Check belt motion or increase timeout."
        )

    # ------------------------------------------------------------------
    @staticmethod
    def _apply_roi(frame, roi: tuple[int, int, int, int] | None):
        if roi is None:
            return frame
        x, y, w, h = roi
        return frame[y:y+h, x:x+w]

    def _emit_bundle(self, frame) -> ScanBundle:
        """Encode the captured numpy frame as PNG bytes and wrap in ScanBundle."""
        try:
            import cv2
            ok, buf = cv2.imencode(".png", frame)
            if not ok:
                raise ScannerFrameError("cv2.imencode failed for captured frame.")
            raw_bytes = buf.tobytes()
        except ImportError:
            # Fallback: raw RGB bytes
            raw_bytes = frame.tobytes()

        h, w = frame.shape[:2]
        raw_frame = RawFrame(
            raw_bytes=raw_bytes,
            frame_label=FrameLabel.RGB_DISPLAY.value,
            width_px=w,
            height_px=h,
            media_type="image/png",
            pixel_spacing_mm=None,   # unknown from rendered display
        )
        meta = CaptureMetadata(
            driver_type=DriverType.FRAMEGRAB,
            is_raw_dual_energy=False,   # ← structurally enforced; never overridable
            pixel_depth_bits=8,
            scanner_model="unknown (HDMI display)",
            fidelity_note=_FIDELITY_NOTE,
        )
        log.info("FrameGrab: captured scan frame %dx%d (RGB display)", w, h)
        return ScanBundle(frames=[raw_frame], metadata=[meta])


__all__ = ["FrameGrabDriver", "_FIDELITY_NOTE"]
