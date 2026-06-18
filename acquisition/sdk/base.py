"""VendorSDKBase — common scaffolding for all vendor SDK drivers.

Vendor SDK drivers are structurally identical:
  - connect()      : load the SDK library, open a session/socket to the
                     scanner daemon, authenticate if required.
  - disconnect()   : release handles; safe to call if not connected.
  - read_scan()    : block until the scanner delivers a complete scan,
                     return a ScanBundle with proper CaptureMetadata.

Key discipline: SDK imports must be inside connect(), not at module level.
The SDK wheel is only present on the scanner box; the rest of the fleet
must be able to import this module without it installed.

Data fidelity contract
-----------------------
Set ``CaptureMetadata.is_raw_dual_energy = True`` **only** when:
  a) The scanner hardware is dual-energy capable (two detectors or rapid
     kVp switching), AND
  b) The SDK actually delivers the two channels as separate pixel arrays
     (not pre-blended into a display image).

If the SDK delivers only a rendered/composited image, set it to False and
populate ``fidelity_note`` with a vendor-specific explanation.
"""

from __future__ import annotations

import logging
import threading
from abc import abstractmethod

from acquisition.protocol import (
    CaptureConfig,
    CaptureMetadata,
    DriverType,
    ScanBundle,
    ScannerConnectionError,
)

log = logging.getLogger("xray.acquisition.sdk")


class VendorSDKBase:
    """Abstract base for all proprietary scanner SDK drivers."""

    def __init__(self, cfg: CaptureConfig) -> None:
        self._cfg = cfg
        self._lock = threading.Lock()
        self._connected = False

    @property
    def driver_type(self) -> DriverType:
        return DriverType.VENDOR_SDK

    @property
    def is_connected(self) -> bool:
        return self._connected

    @abstractmethod
    def connect(self) -> None: ...

    @abstractmethod
    def disconnect(self) -> None: ...

    @abstractmethod
    def read_scan(self, timeout_s: float = 60.0) -> ScanBundle: ...

    def _require_connected(self) -> None:
        if not self._connected:
            raise ScannerConnectionError(
                f"{self.__class__.__name__} is not connected. Call connect() first."
            )


__all__ = ["VendorSDKBase"]
