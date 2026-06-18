"""USB camera driver — OpenCV-based capture with motion-triggered snapshots.

Design
------
The driver opens a V4L2 / DirectShow device via OpenCV (``cv2.VideoCapture``).
It continuously reads frames in a background thread and emits a ``CameraFrame``
whenever the scene is *stable* after a period of motion.

State machine (same model as acquisition/framegrab.py):

    IDLE      → waiting for motion (belt empty / no object)
    ACTIVE    → motion detected (object present)
    STABLE    → N consecutive frames with no motion after active period
    CAPTURED  → snapshot taken, waiting for scene to clear

A snapshot is taken on ACTIVE → STABLE transition.  The "best" frame is the
last stable frame (sharpest, least motion blur).

Thread safety
-------------
``capture_one()`` and ``snapshot()`` are safe to call from any thread.
The internal reader thread runs independently and deposits frames into a
deque protected by a threading.Lock.

OpenCV is lazy-imported; the module is safe to load on servers without a GPU
or display.  ``import camera.driver`` will NOT import cv2 until a
``USBCameraDriver`` is constructed.
"""

from __future__ import annotations

import io
import logging
import threading
import time
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Iterator

import numpy as np

log = logging.getLogger("xray.camera.driver")


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class CameraError(RuntimeError):
    """Base for all camera driver errors."""


class CameraOpenError(CameraError):
    """Could not open the camera device."""


class CameraReadError(CameraError):
    """Frame read failed (device disconnected or driver error)."""


class CameraTimeoutError(CameraError):
    """No motion-triggered snapshot arrived within the timeout."""


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

@dataclass
class CameraConfig:
    """All tunable parameters for one USB camera.

    Matched to XRAY_CAM_* env variables (see composition.py).
    """

    device: int | str = 0            # device index or /dev/video path
    width: int = 1280                # requested capture width
    height: int = 720                # requested capture height
    fps: int = 30                    # requested capture FPS
    roi: tuple[int, int, int, int] | None = None  # (x, y, w, h) crop

    encode_quality: int = 90         # JPEG quality for output bytes

    # Motion detection
    motion_thresh: int = 20          # mean abs-diff to declare "motion"
    stable_frames: int = 6           # consecutive quiet frames → STABLE
    scan_timeout_s: float = 30.0     # max wait for a motion-triggered snap


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CameraFrame:
    """One captured frame emitted by the driver."""

    jpeg_bytes: bytes                # JPEG-encoded image
    width: int
    height: int
    captured_at: float               # time.time() at capture
    device: str                      # device string (for logging/audit)
    motion_score: float              # mean abs-diff that triggered capture (0 if manual)


# ---------------------------------------------------------------------------
# Internal state machine
# ---------------------------------------------------------------------------

class _State(Enum):
    IDLE     = auto()
    ACTIVE   = auto()
    STABLE   = auto()
    CAPTURED = auto()


# ---------------------------------------------------------------------------
# USBCameraDriver
# ---------------------------------------------------------------------------

class USBCameraDriver:
    """Continuously captures from a USB camera and emits motion-triggered frames.

    Usage (blocking — call from a thread or async executor)::

        driver = USBCameraDriver(CameraConfig(device=0))
        driver.open()
        try:
            while True:
                frame = driver.next_frame(timeout_s=60.0)
                process(frame)
        finally:
            driver.close()

    Or one-shot::

        driver = USBCameraDriver(CameraConfig())
        with driver:
            frame = driver.next_frame()
    """

    def __init__(self, cfg: CameraConfig | None = None) -> None:
        self._cfg    = cfg or CameraConfig()
        self._cap    = None   # cv2.VideoCapture
        self._lock   = threading.Lock()
        self._stop   = threading.Event()
        self._thread: threading.Thread | None = None

        # Latest pending snapshot (set by reader thread, consumed by next_frame)
        self._pending: CameraFrame | None = None
        self._pending_event = threading.Event()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def open(self) -> None:
        """Open the camera device and start the capture thread. Fail-closed."""
        import cv2  # lazy import

        cfg = self._cfg
        device = cfg.device if isinstance(cfg.device, str) else int(cfg.device)
        log.info("Opening camera device %s (%dx%d @ %dfps)", device, cfg.width, cfg.height, cfg.fps)

        cap = cv2.VideoCapture(device)
        if not cap.isOpened():
            raise CameraOpenError(
                f"cv2.VideoCapture({device!r}) failed. "
                f"Check: ls /dev/video*, v4l2-ctl --list-devices"
            )

        cap.set(cv2.CAP_PROP_FRAME_WIDTH,  cfg.width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, cfg.height)
        cap.set(cv2.CAP_PROP_FPS,          cfg.fps)

        actual_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        actual_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        actual_fps = cap.get(cv2.CAP_PROP_FPS)
        log.info("Camera opened: actual %dx%d @ %.1ffps", actual_w, actual_h, actual_fps)

        self._cap  = cap
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._reader_loop, name="camera-reader", daemon=True
        )
        self._thread.start()

    def close(self) -> None:
        """Stop the capture thread and release the device."""
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3.0)
        if self._cap:
            self._cap.release()
            self._cap = None
        log.info("Camera closed.")

    def __enter__(self) -> "USBCameraDriver":
        self.open()
        return self

    def __exit__(self, *_) -> None:
        self.close()

    @property
    def is_open(self) -> bool:
        return self._cap is not None and self._cap.isOpened()

    # ------------------------------------------------------------------
    # Frame API
    # ------------------------------------------------------------------

    def next_frame(self, timeout_s: float | None = None) -> CameraFrame:
        """Block until a motion-triggered snapshot is ready, then return it.

        Raises:
            CameraTimeoutError   — no frame within timeout_s seconds.
            CameraReadError      — capture thread died.
        """
        t = timeout_s if timeout_s is not None else self._cfg.scan_timeout_s
        self._pending_event.clear()
        if not self._pending_event.wait(timeout=t):
            raise CameraTimeoutError(
                f"No motion-triggered frame in {t:.1f}s on device {self._cfg.device!r}"
            )
        with self._lock:
            frame = self._pending
            self._pending = None
        if frame is None:
            raise CameraReadError("Pending frame disappeared — reader may have crashed")
        return frame

    def capture_now(self) -> CameraFrame:
        """Capture a single frame immediately (no motion trigger).

        Useful for one-shot captures (document scanning, operator snapshot).
        """
        if not self.is_open:
            raise CameraError("Camera is not open. Call open() first.")
        # Many UVC webcams return empty/invalid frames on the first few reads
        # while the sensor warms up and auto-exposure settles. Retry briefly
        # before declaring the device dead.
        ok, bgr = False, None
        for _ in range(15):
            ok, bgr = self._cap.read()
            if ok and bgr is not None and bgr.size > 0:
                break
            time.sleep(0.1)
        if not ok or bgr is None or bgr.size == 0:
            raise CameraReadError(f"Failed to read from device {self._cfg.device!r}")
        return self._encode_frame(bgr, motion_score=0.0)

    # ------------------------------------------------------------------
    # Reader thread
    # ------------------------------------------------------------------

    def _reader_loop(self) -> None:
        import cv2
        cfg = self._cfg

        state = _State.IDLE
        prev_gray: np.ndarray | None = None
        stable_count = 0
        best_frame: np.ndarray | None = None

        log.debug("Camera reader started (device=%s)", cfg.device)

        while not self._stop.is_set():
            ok, bgr = self._cap.read()
            if not ok:
                log.warning("Camera read error on device %s — retrying…", cfg.device)
                time.sleep(0.1)
                continue

            # Apply ROI crop if configured
            if cfg.roi:
                x, y, w, h = cfg.roi
                bgr = bgr[y:y+h, x:x+w]

            gray  = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
            score = _motion_score(prev_gray, gray)
            prev_gray = gray

            # ---------- State machine ----------
            if state == _State.IDLE:
                if score > cfg.motion_thresh:
                    state = _State.ACTIVE
                    stable_count = 0
                    log.debug("Camera: IDLE → ACTIVE (score=%.1f)", score)

            elif state == _State.ACTIVE:
                best_frame = bgr.copy()
                if score <= cfg.motion_thresh:
                    stable_count += 1
                    if stable_count >= cfg.stable_frames:
                        state = _State.STABLE
                        log.debug("Camera: ACTIVE → STABLE")
                else:
                    stable_count = 0

            elif state == _State.STABLE:
                frame = self._encode_frame(best_frame, motion_score=score)
                with self._lock:
                    self._pending = frame
                self._pending_event.set()
                state = _State.CAPTURED
                log.info(
                    "Camera: snapshot captured (%dx%d %.1fKB)",
                    frame.width, frame.height, len(frame.jpeg_bytes) / 1024,
                )

            elif state == _State.CAPTURED:
                # Wait for the scene to clear before accepting the next object
                if score > cfg.motion_thresh * 2:
                    state = _State.IDLE
                    stable_count = 0
                    log.debug("Camera: CAPTURED → IDLE (scene cleared)")

        log.debug("Camera reader stopped.")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _encode_frame(self, bgr: np.ndarray, *, motion_score: float) -> CameraFrame:
        import cv2
        cfg = self._cfg
        ok, buf = cv2.imencode(
            ".jpg", bgr,
            [cv2.IMWRITE_JPEG_QUALITY, cfg.encode_quality],
        )
        if not ok:
            raise CameraError("JPEG encoding failed")
        h, w = bgr.shape[:2]
        return CameraFrame(
            jpeg_bytes=buf.tobytes(),
            width=w,
            height=h,
            captured_at=time.time(),
            device=str(cfg.device),
            motion_score=motion_score,
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _motion_score(prev: np.ndarray | None, curr: np.ndarray) -> float:
    """Mean absolute pixel difference between two grayscale frames."""
    if prev is None or prev.shape != curr.shape:
        return 0.0
    return float(np.mean(np.abs(curr.astype(np.int16) - prev.astype(np.int16))))


def list_cameras(max_index: int = 8) -> list[int]:
    """Probe device indices 0..max_index and return the ones that open.

    Utility for discovery — call once at startup or from CLI:
        python -c "from camera.driver import list_cameras; print(list_cameras())"
    """
    import cv2
    found = []
    for i in range(max_index):
        cap = cv2.VideoCapture(i)
        if cap.isOpened():
            found.append(i)
            cap.release()
    return found


__all__ = [
    "CameraError", "CameraOpenError", "CameraReadError", "CameraTimeoutError",
    "CameraConfig", "CameraFrame", "USBCameraDriver",
    "list_cameras",
]
