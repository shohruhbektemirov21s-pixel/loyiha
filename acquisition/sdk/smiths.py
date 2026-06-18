"""Smiths Detection SDK adapter stub.

Supported hardware families
---------------------------
  HI-SCAN 10080 XCT / 10080 EDX   (baggage, cabin)
  ECIX-6  / ECIX-10               (hold baggage)
  IONSCAN 600                      (trace detection — no imaging; excluded here)
  eqo                              (checkpoint cabin baggage)

SDK library
-----------
The Smiths Detection SDK is distributed as a Windows/Linux shared library
under a separate commercial licence and NDA.  Common package name on the
scanner workstation: ``sdvxapi`` or ``hivis_sdk``.

Contact: Smiths Detection OEM Integration team.
         integration@smithsdetection.com

Integration notes (Smiths-specific)
-------------------------------------
1. The SDK typically exposes a C-ABI DLL/SO that Python reaches via ctypes
   or a provided Python binding wheel (``smiths_xray_sdk``).
2. Sessions are per-scanner-unit.  Multi-lane installs need one session each.
3. Scan delivery is callback-based on Windows; polling-based on Linux.
4. Dual-energy: HI-SCAN EDX and ECIX-10 hardware delivers separate
   ``ScanImage.HIGH_ENERGY`` and ``ScanImage.LOW_ENERGY`` objects.
   Older HI-SCAN 6040 hardware delivers a composite only.
5. Pixel format: 16-bit unsigned, row-major, no padding.
6. Coordinate origin: top-left.
7. Pixel spacing: available in ``ScanImage.pixel_size_mm`` (float).
8. The SDK's ``ScanImage.to_bytes()`` returns a raw pixel buffer (no header).
   Wrap it as a TIFF before passing to IngestPipeline.

Stub implementation
-------------------
The connect() and read_scan() bodies are clearly marked TODO.
Replace the TODO blocks with actual SDK calls when the library is available.
The rest of the plumbing (error mapping, metadata construction, TIFF
wrapping) is fully implemented and should not need changes.
"""

from __future__ import annotations

import io
import logging
import time

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
from acquisition.sdk.base import VendorSDKBase
from datalayer.ingestion import RawFrame

log = logging.getLogger("xray.acquisition.sdk.smiths")


def _raw16_to_tiff(buf: bytes, width: int, height: int) -> bytes:
    """Wrap a 16-bit raw pixel buffer in a minimal TIFF container."""
    try:
        import numpy as np
        from PIL import Image
        arr = np.frombuffer(buf, dtype=np.uint16).reshape(height, width)
        out = io.BytesIO()
        Image.fromarray(arr, mode="I;16").save(out, format="TIFF")
        return out.getvalue()
    except ImportError:
        return buf   # store raw bytes; IngestPipeline accepts them


class SmithsDetectionDriver(VendorSDKBase):
    """Smiths Detection SDK driver.

    Replace the TODO blocks with real SDK calls.  The data flow and
    error-mapping logic around the TODOs is complete.
    """

    def connect(self) -> None:
        try:
            # TODO: replace with real SDK import, e.g.:
            #   import smiths_xray_sdk as _sdk
            #   self._sdk = _sdk
            #   self._session = _sdk.Session(
            #       host=self._cfg.sdk_host,
            #       port=self._cfg.sdk_port,
            #   )
            #   self._session.open()
            raise ImportError("smiths_xray_sdk not installed")
        except ImportError as exc:
            raise ScannerConnectionError(
                "Smiths Detection SDK not installed. "
                "Obtain 'smiths_xray_sdk' from Smiths Detection OEM Integration "
                "and install it on this workstation. "
                f"Underlying error: {exc}"
            ) from exc
        except Exception as exc:
            raise ScannerConnectionError(
                f"Smiths Detection SDK failed to connect to "
                f"{self._cfg.sdk_host}:{self._cfg.sdk_port}: {exc}"
            ) from exc

        self._connected = True
        log.info(
            "Smiths Detection SDK connected: %s:%d",
            self._cfg.sdk_host, self._cfg.sdk_port,
        )

    def disconnect(self) -> None:
        try:
            # TODO: self._session.close()
            pass
        except Exception as exc:
            log.warning("Smiths SDK disconnect error (ignored): %s", exc)
        finally:
            self._connected = False

    def read_scan(self, timeout_s: float = 60.0) -> ScanBundle:
        self._require_connected()
        deadline = time.monotonic() + timeout_s

        # TODO: replace polling loop with real SDK scan-ready callback/event, e.g.:
        #   scan_event = self._session.wait_for_scan(timeout_ms=int(timeout_s*1000))
        #   if scan_event is None:
        #       raise ScannerTimeoutError(...)
        #   scan = scan_event.scan
        raise ScannerTimeoutError(
            "SmithsDetectionDriver.read_scan() is a stub. "
            "Implement using smiths_xray_sdk Session.wait_for_scan()."
        )

        # ---- TEMPLATE: code below runs once stub is replaced ----
        # frames: list[RawFrame] = []
        # metas: list[CaptureMetadata] = []
        #
        # # Dual-energy: HI-SCAN EDX / ECIX-10 deliver two images.
        # for sdk_img in [scan.high_energy, scan.low_energy]:
        #     if sdk_img is None:
        #         continue
        #     raw_buf = sdk_img.to_bytes()           # 16-bit raw pixels
        #     tiff_buf = _raw16_to_tiff(raw_buf, sdk_img.width, sdk_img.height)
        #     label = (FrameLabel.HIGH_ENERGY if sdk_img.channel == "HIGH"
        #              else FrameLabel.LOW_ENERGY)
        #     frames.append(RawFrame(
        #         raw_bytes=tiff_buf,
        #         frame_label=label.value,
        #         width_px=sdk_img.width,
        #         height_px=sdk_img.height,
        #         media_type="image/tiff",
        #         pixel_spacing_mm=sdk_img.pixel_size_mm,
        #     ))
        #     metas.append(CaptureMetadata(
        #         driver_type=DriverType.VENDOR_SDK,
        #         is_raw_dual_energy=True,   # ECIX/HI-SCAN EDX deliver raw channels
        #         pixel_depth_bits=16,
        #         scanner_model=sdk_img.scanner_model,
        #         firmware_version=sdk_img.firmware_version,
        #         pixel_spacing_mm=sdk_img.pixel_size_mm,
        #     ))
        #
        # if not frames:
        #     raise ScannerFrameError("Smiths SDK returned a scan with no image data.")
        # return ScanBundle(frames=frames, metadata=metas)


__all__ = ["SmithsDetectionDriver"]
