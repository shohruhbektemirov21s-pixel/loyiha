"""L3/Leidos SDK adapter stub.

Supported hardware families
---------------------------
  L3 ProVision ATD          (millimetre-wave; no X-ray imaging — excluded)
  L3 ClearScan              (CT baggage)
  Leidos CertScan           (cargo CT)
  L3 CTX 9800 / CTX 5800   (hold baggage CT)
  L3 MV3D / MVT             (multi-view X-ray)

SDK library
-----------
L3/Leidos distributes the SDK under the "L3 Security & Detection Systems OEM
Programme".  Common package name: ``l3sds_sdk`` or ``l3_xray_api``.

Contact: L3 Security & Detection Systems, OEM Integration Engineering.
         sdk-support@l3sds.com

Integration notes (L3-specific)
---------------------------------
1. The CTX family uses DICOS as its native export format.  If the scanner is
   configured to write to a hot folder, use DICOSDriver instead — it is
   simpler and requires no proprietary SDK.
2. The MVT family provides a TCP push-socket.  The SDK wraps this socket in a
   higher-level ``ScanSubscriber`` class.
3. Data fidelity: ClearScan / CertScan deliver volumetric CT data.  The SDK
   provides a ``Volume`` object with per-slice 16-bit TIFF export.  Treat
   each axial slice as one RawFrame with view_label="axial_<N>".
4. Pixel format: 16-bit unsigned, row-major, HU-equivalent for CT.
5. Dual-energy MVT: delivers two registered views at different energies.
   ``ScanImage.energy_level`` is "HIGH" or "LOW".
6. Pixel spacing: ``ScanImage.pixel_spacing_mm`` (tuple: row_mm, col_mm).
   Use the mean of (row_mm, col_mm) as the scalar pixel_spacing_mm.

Stub implementation
-------------------
Replace TODO blocks with real SDK calls.  Do not change the surrounding
plumbing — it correctly maps SDK errors to ``ScannerError`` subtypes.
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

log = logging.getLogger("xray.acquisition.sdk.l3")


def _mean_spacing(spacing) -> float | None:
    """Average row/col spacing tuple to a scalar mm/pixel."""
    try:
        vals = list(spacing)
        return sum(vals) / len(vals) if vals else None
    except (TypeError, ZeroDivisionError):
        return None


class L3LeidosDriver(VendorSDKBase):
    """L3/Leidos SDK driver.

    Replace the TODO blocks with real SDK calls.
    """

    def connect(self) -> None:
        try:
            # TODO: replace with real SDK import, e.g.:
            #   import l3sds_sdk as _sdk
            #   self._sdk = _sdk
            #   self._subscriber = _sdk.ScanSubscriber(
            #       host=self._cfg.sdk_host,
            #       port=self._cfg.sdk_port,
            #   )
            #   self._subscriber.connect()
            raise ImportError("l3sds_sdk not installed")
        except ImportError as exc:
            raise ScannerConnectionError(
                "L3/Leidos SDK not installed. "
                "Obtain 'l3sds_sdk' from the L3 OEM Integration Programme and "
                "install it on this workstation. "
                f"Underlying error: {exc}"
            ) from exc
        except Exception as exc:
            raise ScannerConnectionError(
                f"L3/Leidos SDK failed to connect to "
                f"{self._cfg.sdk_host}:{self._cfg.sdk_port}: {exc}"
            ) from exc

        self._connected = True
        log.info(
            "L3/Leidos SDK connected: %s:%d",
            self._cfg.sdk_host, self._cfg.sdk_port,
        )

    def disconnect(self) -> None:
        try:
            # TODO: self._subscriber.disconnect()
            pass
        except Exception as exc:
            log.warning("L3 SDK disconnect error (ignored): %s", exc)
        finally:
            self._connected = False

    def read_scan(self, timeout_s: float = 60.0) -> ScanBundle:
        self._require_connected()

        # TODO: replace with real SDK call, e.g.:
        #   scan = self._subscriber.next_scan(timeout_s=timeout_s)
        #   if scan is None:
        #       raise ScannerTimeoutError(...)
        raise ScannerTimeoutError(
            "L3LeidosDriver.read_scan() is a stub. "
            "Implement using l3sds_sdk ScanSubscriber.next_scan()."
        )

        # ---- TEMPLATE: MVT dual-energy path ----
        # frames: list[RawFrame] = []
        # metas: list[CaptureMetadata] = []
        #
        # for sdk_img in scan.images:           # one entry per energy level / view
        #     raw_buf = sdk_img.pixel_data      # bytes, 16-bit unsigned row-major
        #     w, h = sdk_img.width, sdk_img.height
        #     try:
        #         import numpy as np
        #         from PIL import Image
        #         arr = np.frombuffer(raw_buf, dtype=np.uint16).reshape(h, w)
        #         out = io.BytesIO()
        #         Image.fromarray(arr, mode="I;16").save(out, format="TIFF")
        #         tiff_buf = out.getvalue()
        #     except ImportError:
        #         tiff_buf = raw_buf
        #
        #     energy = getattr(sdk_img, "energy_level", "").upper()
        #     label = (FrameLabel.HIGH_ENERGY if energy == "HIGH"
        #              else FrameLabel.LOW_ENERGY if energy == "LOW"
        #              else FrameLabel.COMPOSITE)
        #     spacing = _mean_spacing(getattr(sdk_img, "pixel_spacing_mm", None))
        #
        #     frames.append(RawFrame(
        #         raw_bytes=tiff_buf,
        #         frame_label=label.value,
        #         width_px=w,
        #         height_px=h,
        #         media_type="image/tiff",
        #         pixel_spacing_mm=spacing,
        #     ))
        #     metas.append(CaptureMetadata(
        #         driver_type=DriverType.VENDOR_SDK,
        #         is_raw_dual_energy=(energy in ("HIGH", "LOW")),
        #         pixel_depth_bits=16,
        #         scanner_model=getattr(sdk_img, "scanner_model", "L3/Leidos"),
        #         firmware_version=getattr(sdk_img, "firmware_version", None),
        #         pixel_spacing_mm=spacing,
        #     ))
        #
        # if not frames:
        #     raise ScannerFrameError("L3 SDK returned a scan with no image data.")
        # return ScanBundle(frames=frames, metadata=metas)


__all__ = ["L3LeidosDriver"]
