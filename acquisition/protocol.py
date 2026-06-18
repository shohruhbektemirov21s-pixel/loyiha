"""Vendor-agnostic ScannerDriver Protocol and shared data types.

Every acquisition path (DICOS, vendor SDK, HDMI frame grabber) satisfies this
Protocol. The AcquisitionPipeline and ConnectionWatchdog only know about this
interface — vendor specifics live entirely inside the driver implementations.

Driver hierarchy (choose the highest available):

  1. VENDOR_SDK   — Proprietary API. Raw dual-energy data. Highest fidelity.
                    Material discrimination from attenuation coefficients.
  2. DICOS        — ANSI/NEMA DICOS standard. Dual-energy when scanner exports
                    separate channels. Good fidelity; needs no vendor library.
  3. FRAMEGRAB    — HDMI frame grabber + OpenCV. RGB display capture only.
                    ⚠ See RGB-vs-raw limitation documented in README.md and
                    enforced structurally in framegrab.py's CaptureMetadata.

Never mix driver paths for the same lane — the modality reported in the
AcquisitionResult must match the actual pixel data fidelity.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from contracts.v1 import ImageModality, ScanSubject


# ---------------------------------------------------------------------------
# Exceptions (hardware-to-software bridge error taxonomy)
# ---------------------------------------------------------------------------
class ScannerError(RuntimeError):
    """Base for all acquisition-layer exceptions."""


class ScannerConnectionError(ScannerError):
    """Could not establish or maintain a connection to the scanner hardware."""


class ScannerTimeoutError(ScannerError):
    """No scan arrived within the configured timeout window."""


class ScannerFrameError(ScannerError):
    """A scan arrived but produced malformed or incomplete frame data."""


class ScannerUnavailableError(ScannerError):
    """The scanner is offline and all reconnect attempts have been exhausted."""


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------
class DriverType(str, enum.Enum):
    VENDOR_SDK  = "vendor_sdk"  # proprietary; raw dual-energy data
    DICOS       = "dicos"       # ANSI/NEMA DICOS file or stream
    FRAMEGRAB   = "framegrab"   # HDMI frame grabber (RGB rendered output only)


class FrameLabel(str, enum.Enum):
    """Standard view/channel labels shared across all drivers."""
    HIGH_ENERGY  = "high_energy"   # dual-energy high-kVp channel
    LOW_ENERGY   = "low_energy"    # dual-energy low-kVp channel
    COMPOSITE    = "composite"     # scanner-blended or single-energy
    TOP_VIEW     = "top_view"      # multi-view top
    SIDE_VIEW    = "side_view"     # multi-view side
    RGB_DISPLAY  = "rgb_display"   # ⚠ frame grabber rendered RGB only


# ---------------------------------------------------------------------------
# CaptureMetadata — honest fidelity declaration per frame
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class CaptureMetadata:
    """Records the exact acquisition fidelity for one frame.

    The ``is_raw_dual_energy`` flag is the structural enforcement of the
    RGB-vs-raw distinction: downstream code that needs true dual-energy data
    must check this flag rather than trust the ``modality`` field alone.

    A FRAMEGRAB frame is always ``is_raw_dual_energy=False``, even when the
    scanner is dual-energy — because what the frame grabber captures is the
    scanner's *rendered display*, not the raw sensor output.
    """
    driver_type: DriverType
    is_raw_dual_energy: bool       # True only for SDK/DICOS dual-energy paths
    pixel_depth_bits: int = 8      # 8 for RGB, 12/14/16 for raw scanner data
    scanner_model: str = "unknown"
    firmware_version: str | None = None
    # Populated by DICOS reader from tag (0028,0030).
    pixel_spacing_mm: float | None = None
    # Set to a human-readable warning for FRAMEGRAB frames.
    fidelity_note: str | None = None


# ---------------------------------------------------------------------------
# CaptureConfig — flat, env-readable config for one scanner lane
# ---------------------------------------------------------------------------
@dataclass
class CaptureConfig:
    """All configuration for one acquisition lane. Injected at startup.

    Env-variable mapping (via composition.py):
        XRAY_ACQ_DRIVER         dicos | vendor_sdk | framegrab
        XRAY_ACQ_SCANNER_ID     stable hardware identifier
        XRAY_ACQ_LANE_ID        lane label (optional)
        XRAY_ACQ_MODALITY       dual_energy | single_energy | multi_view
        XRAY_ACQ_SUBJECT        vehicle | cargo | baggage | parcel | other

        -- DICOS --
        XRAY_ACQ_DICOS_WATCH_DIR    directory to monitor for .dcm files
        XRAY_ACQ_DICOS_MOVE_DIR     processed files moved here (default: watch_dir/done)
        XRAY_ACQ_DICOS_POLL_S       polling interval in seconds (default: 0.5)

        -- FRAMEGRAB --
        XRAY_ACQ_GRAB_DEVICE        device index or path (e.g. 0 or /dev/video0)
        XRAY_ACQ_GRAB_FPS           target capture FPS (default: 30)
        XRAY_ACQ_GRAB_ROI           "x,y,w,h" — crop the scanner area from the screen
        XRAY_ACQ_GRAB_STABLE_FRAMES number of identical frames to call a scan stable
        XRAY_ACQ_GRAB_STABLE_THRESH pixel diff threshold for "same frame" (0–255)
        XRAY_ACQ_GRAB_SCAN_TIMEOUT  max seconds to wait for a stable scan

        -- SDK --
        XRAY_ACQ_SDK_HOST   host of the SDK broker (default: localhost)
        XRAY_ACQ_SDK_PORT   port of the SDK broker (default: 5000)

        -- Watchdog --
        XRAY_ACQ_RECONNECT_ATTEMPTS max reconnect attempts (default: 10)
        XRAY_ACQ_RECONNECT_DELAY_S  base backoff between reconnects (default: 5.0)
        XRAY_ACQ_FRAME_TIMEOUT_S    how long to wait for a frame (default: 60.0)
    """

    driver_type: DriverType = DriverType.DICOS
    scanner_id: str = "scanner-01"
    lane_id: str | None = None
    modality: ImageModality = ImageModality.DUAL_ENERGY
    subject: ScanSubject = ScanSubject.BAGGAGE
    operator_id: str | None = None

    # DICOS options
    dicos_watch_dir: str = "/var/lib/xray/incoming"
    dicos_move_dir: str | None = None
    dicos_poll_s: float = 0.5

    # Framegrab options
    grab_device: int | str = 0
    grab_fps: int = 30
    grab_roi: tuple[int, int, int, int] | None = None     # (x, y, w, h)
    grab_stable_frames: int = 8                           # ~0.25 s at 30 fps
    grab_stable_thresh: int = 6                           # mean abs diff threshold
    grab_scan_timeout_s: float = 30.0

    # SDK options
    sdk_host: str = "localhost"
    sdk_port: int = 5000

    # Watchdog options
    reconnect_attempts: int = 10
    reconnect_delay_s: float = 5.0
    frame_timeout_s: float = 60.0

    # Downstream — where to POST AcquisitionResult
    api_base_url: str = "http://127.0.0.1:8000"
    api_timeout_s: float = 30.0


# ---------------------------------------------------------------------------
# ScannerDriver Protocol
# ---------------------------------------------------------------------------
@runtime_checkable
class ScannerDriver(Protocol):
    """The single interface every scanner backend must satisfy.

    ``read_scan`` is **blocking**: it returns when a complete scan is ready
    (or raises). The AcquisitionPipeline calls it from a thread executor so
    the async event loop is never blocked.

    Implementations must be thread-safe: the watchdog and pipeline may call
    ``is_connected`` from a different thread than ``read_scan``.
    """

    @property
    def driver_type(self) -> DriverType: ...

    @property
    def is_connected(self) -> bool: ...

    def connect(self) -> None:
        """Establish the connection to the scanner.
        Raises ``ScannerConnectionError`` on failure.
        """
        ...

    def disconnect(self) -> None:
        """Release resources. Safe to call even if not connected."""
        ...

    def read_scan(self, timeout_s: float = 60.0) -> "ScanBundle":
        """Block until a complete scan is available, then return it.

        Raises:
            ScannerTimeoutError    — no scan in ``timeout_s`` seconds.
            ScannerFrameError      — scan arrived but data is malformed.
            ScannerConnectionError — link dropped mid-read.
        """
        ...


# ---------------------------------------------------------------------------
# ScanBundle — what a driver returns
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ScanBundle:
    """The driver's output: raw frames + per-frame capture metadata.

    ``RawFrame`` (from datalayer.ingestion) carries the bytes;
    ``CaptureMetadata`` carries the fidelity declaration.
    They zip 1-to-1: ``frames[i]`` was acquired with ``metadata[i]``.
    """
    from datalayer.ingestion import RawFrame   # local import to avoid circular

    frames: list   # list[RawFrame]
    metadata: list  # list[CaptureMetadata]


__all__ = [
    "ScannerError", "ScannerConnectionError", "ScannerTimeoutError",
    "ScannerFrameError", "ScannerUnavailableError",
    "DriverType", "FrameLabel", "CaptureMetadata", "CaptureConfig",
    "ScannerDriver", "ScanBundle",
]
