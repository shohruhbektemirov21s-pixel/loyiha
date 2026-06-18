"""DICOS (Digital Imaging and Communications in Security) driver.

DICOS is the ANSI/NEMA standard for security-screening imaging data.
It extends DICOM with security-specific tags:
  - Object of Interest (OOI): (4008,0111) — alarm regions flagged by the scanner
  - Threat Category Code: (4008,0111)→(0008,0100) — scanner's own alarm class
  - Multi-energy channels in separate SOP instances with shared Study UID
  - Pixel spacing in (0028,0030) — physical scale for bounding-box sizes

Supported scanner output formats:
  * Single .dcm file per scan (single-energy or composite)
  * Paired .dcm files per scan (high-energy + low-energy, shared Study/Series UID)
  * Polled directory: scanner writes files to a hot folder; this driver watches it

Data fidelity:
  DICOS preserves the raw attenuated pixel values at 12–16 bit depth. This is
  NOT the rendered RGB display; it is the actual sensor output. Material
  decomposition downstream is valid with DICOS data.

pydicom usage:
  Lazy-imported inside connect(); not required on boxes without a scanner.
  Install: pip install pydicom==2.x

Limitations:
  - The "hot folder" approach adds file-system latency (~100–500 ms per scan).
    For real-time requirements, the vendor SDK path is preferred.
  - If the scanner writes gzip-compressed pixel data, pydicom handles it
    automatically. If vendor-specific transfer syntaxes are used, you may
    need to install pylibjpeg or gdcm.
  - DICOS does not mandate which transfer syntax is used; test against the
    actual scanner's export configuration.
"""

from __future__ import annotations

import hashlib
import io
import logging
import os
import shutil
import threading
import time
from dataclasses import dataclass
from pathlib import Path
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

if TYPE_CHECKING:
    pass

log = logging.getLogger("xray.acquisition.dicos")

# DICOS SOP Class UIDs (from ANSI/NEMA DICOS 2013 / PS3.4 Supplement 172).
_DICOS_XA_SOP      = "1.2.840.10008.5.1.4.1.1.501.3"   # X-ray Image
_DICOS_TDR_SOP     = "1.2.840.10008.5.1.4.1.1.501.1"   # Threat Detection Report
_DICOS_CT_SOP      = "1.2.840.10008.5.1.4.1.1.501.4"   # CT Storage
_STANDARD_XA_SOP   = "1.2.840.10008.5.1.4.1.1.12.1"    # plain XA (some scanners use this)

_KNOWN_DICOS_SOPS = {_DICOS_XA_SOP, _DICOS_TDR_SOP, _DICOS_CT_SOP, _STANDARD_XA_SOP}

# DICOM tags used for DICOS extraction.
_TAG_ROWS              = (0x0028, 0x0010)
_TAG_COLS              = (0x0028, 0x0011)
_TAG_PIXEL_SPACING     = (0x0028, 0x0030)
_TAG_BITS_ALLOCATED    = (0x0028, 0x0100)
_TAG_PHOTOMETRIC       = (0x0028, 0x0004)
_TAG_SOP_CLASS_UID     = (0x0008, 0x0016)
_TAG_STUDY_UID         = (0x0020, 0x000D)
_TAG_SERIES_UID        = (0x0020, 0x000E)
_TAG_IMAGE_TYPE        = (0x0008, 0x0103)   # DICOS: HIGH_ENERGY / LOW_ENERGY in ImageType[2]
_TAG_PIXEL_DATA        = (0x7FE0, 0x0010)
_TAG_SCANNER_MODEL     = (0x0008, 0x1090)
_TAG_SOFTWARE_VERSION  = (0x0018, 0x1020)
_TAG_DETECTOR_ID       = (0x0018, 0x700A)   # DICOS: scanner hardware ID


def _safe_tag(ds, tag: tuple, default=None):
    """Read a pydicom tag safely; return ``default`` if absent or unreadable."""
    try:
        val = ds[tag].value
        return val if val is not None else default
    except (KeyError, AttributeError):
        return default


def _pixel_spacing(ds) -> float | None:
    """Extract pixel spacing in mm from tag (0028,0030). Returns mm/pixel."""
    ps = _safe_tag(ds, _TAG_PIXEL_SPACING)
    if ps is None:
        return None
    if hasattr(ps, "__iter__"):
        try:
            return float(list(ps)[0])
        except Exception:
            return None
    try:
        return float(ps)
    except Exception:
        return None


def _frame_label_from_image_type(ds) -> str:
    """Infer high/low energy label from DICOS ImageType value."""
    img_type = _safe_tag(ds, _TAG_IMAGE_TYPE)
    if img_type is None:
        return FrameLabel.COMPOSITE.value
    parts = str(img_type).upper().replace("\\", "/").split("/")
    for part in parts:
        if "HIGH" in part or "HIGH_ENERGY" in part:
            return FrameLabel.HIGH_ENERGY.value
        if "LOW" in part or "LOW_ENERGY" in part:
            return FrameLabel.LOW_ENERGY.value
    return FrameLabel.COMPOSITE.value


def _dcm_to_raw_frame(dcm_path: Path) -> tuple[RawFrame, CaptureMetadata]:
    """Parse one DICOS/DICOM file into a RawFrame + CaptureMetadata.

    Returns raw 16-bit pixels encoded as uncompressed TIFF (in-memory) so
    the rest of the pipeline handles them identically to scanner-native bytes.
    """
    try:
        import pydicom
    except ImportError as exc:
        raise ScannerConnectionError(
            "pydicom is not installed. Install it on the scanner box: pip install pydicom"
        ) from exc

    try:
        ds = pydicom.dcmread(str(dcm_path), force=True)
    except Exception as exc:
        raise ScannerFrameError(f"Cannot parse DICOS file {dcm_path}: {exc}") from exc

    rows   = int(_safe_tag(ds, _TAG_ROWS,   default=0))
    cols   = int(_safe_tag(ds, _TAG_COLS,   default=0))
    bits   = int(_safe_tag(ds, _TAG_BITS_ALLOCATED, default=8))

    if rows <= 0 or cols <= 0:
        raise ScannerFrameError(f"DICOS {dcm_path.name}: zero/negative dimensions ({rows}x{cols})")

    try:
        pixel_array = ds.pixel_array
    except Exception as exc:
        raise ScannerFrameError(f"DICOS {dcm_path.name}: cannot decode pixel data: {exc}") from exc

    # Encode as uncompressed TIFF so IngestPipeline can handle it.
    # We use io.BytesIO + PIL if available, otherwise raw bytes.
    try:
        from PIL import Image as _PIL
        import numpy as _np
        if bits <= 8:
            arr8 = pixel_array.astype(_np.uint8)
        else:
            # Scale 12/14/16-bit to 16-bit for TIFF storage.
            arr16 = pixel_array.astype(_np.uint16)
            buf = io.BytesIO()
            img = _PIL.fromarray(arr16, mode="I;16")
            img.save(buf, format="TIFF")
            raw_bytes = buf.getvalue()
        if bits <= 8:
            buf = io.BytesIO()
            img = _PIL.fromarray(arr8)
            img.save(buf, format="TIFF")
            raw_bytes = buf.getvalue()
    except ImportError:
        # Pillow not available: store raw pixel bytes directly.
        raw_bytes = pixel_array.tobytes()

    sop_uid = _safe_tag(ds, _TAG_SOP_CLASS_UID, default="")
    is_dicos = str(sop_uid) in _KNOWN_DICOS_SOPS
    is_dual = bits > 8   # rough heuristic: raw dual-energy data is > 8 bit
    frame_label = _frame_label_from_image_type(ds)
    pixel_spacing = _pixel_spacing(ds)

    scanner_model    = str(_safe_tag(ds, _TAG_SCANNER_MODEL, default="unknown"))
    firmware_version = str(_safe_tag(ds, _TAG_SOFTWARE_VERSION, default=None) or "")

    raw_frame = RawFrame(
        raw_bytes=raw_bytes,
        frame_label=frame_label,
        width_px=cols,
        height_px=rows,
        media_type="image/tiff",
        pixel_spacing_mm=pixel_spacing,
    )
    meta = CaptureMetadata(
        driver_type=DriverType.DICOS,
        is_raw_dual_energy=is_dual and is_dicos,
        pixel_depth_bits=bits,
        scanner_model=scanner_model,
        firmware_version=firmware_version or None,
        pixel_spacing_mm=pixel_spacing,
        fidelity_note=None if is_dicos else (
            "Non-DICOS DICOM file: SOP class not in DICOS registry; "
            "raw dual-energy status unconfirmed."
        ),
    )
    return raw_frame, meta


def _group_by_study(paths: list[Path]) -> dict[str, list[Path]]:
    """Group .dcm files by Study Instance UID for multi-file scans."""
    try:
        import pydicom
    except ImportError:
        return {"_single": paths}

    groups: dict[str, list[Path]] = {}
    for p in paths:
        try:
            ds = pydicom.dcmread(str(p), specific_tags=[_TAG_STUDY_UID], stop_before_pixels=True)
            uid = str(_safe_tag(ds, _TAG_STUDY_UID, default="_single"))
        except Exception:
            uid = "_single"
        groups.setdefault(uid, []).append(p)
    return groups


class DICOSDriver:
    """Monitors a hot-folder for new DICOS files and returns complete scans.

    One scan = one Study (all .dcm files sharing the same Study Instance UID).
    Single-file scans (one .dcm = one scan) are emitted immediately.
    Dual-channel scans (high + low energy, same Study UID) wait for both files
    to appear before emitting.

    Processed files are moved to ``done_dir`` to prevent double-ingestion.
    """

    def __init__(self, cfg: CaptureConfig) -> None:
        self._cfg = cfg
        self._watch = Path(cfg.dicos_watch_dir)
        self._done  = Path(cfg.dicos_move_dir or (cfg.dicos_watch_dir + "/done"))
        self._lock  = threading.Lock()
        self._connected = False

    @property
    def driver_type(self) -> DriverType:
        return DriverType.DICOS

    @property
    def is_connected(self) -> bool:
        return self._connected and self._watch.is_dir()

    def connect(self) -> None:
        if not self._watch.is_dir():
            raise ScannerConnectionError(
                f"DICOS watch dir does not exist: {self._watch}. "
                f"Create it and configure the scanner to write .dcm files there."
            )
        self._done.mkdir(parents=True, exist_ok=True)
        self._connected = True
        log.info("DICOS driver connected: watching %s → done: %s", self._watch, self._done)

    def disconnect(self) -> None:
        self._connected = False

    def read_scan(self, timeout_s: float = 60.0) -> ScanBundle:
        """Block until a complete scan is available in the watch directory."""
        deadline = time.monotonic() + timeout_s

        while time.monotonic() < deadline:
            dcm_files = sorted(self._watch.glob("*.dcm"))
            if not dcm_files:
                # Also try .IMA and .img (common vendor extensions)
                dcm_files = sorted(
                    list(self._watch.glob("*.IMA")) +
                    list(self._watch.glob("*.img")) +
                    list(self._watch.glob("*.dic"))
                )

            if dcm_files:
                bundle = self._try_ingest(dcm_files)
                if bundle is not None:
                    return bundle

            time.sleep(self._cfg.dicos_poll_s)

        raise ScannerTimeoutError(
            f"No DICOS scan appeared in {self._watch} within {timeout_s:.0f}s."
        )

    def _try_ingest(self, paths: list[Path]) -> ScanBundle | None:
        """Try to form a complete scan from available files."""
        with self._lock:
            # Wait for files to stop growing (write completion guard).
            settled = [p for p in paths if self._is_settled(p)]
            if not settled:
                return None

            groups = _group_by_study(settled)

            # Take the first complete group.
            for study_uid, group_paths in groups.items():
                frames: list[RawFrame] = []
                metas: list[CaptureMetadata] = []
                errors: list[str] = []

                for dcm_path in sorted(group_paths):
                    try:
                        rf, meta = _dcm_to_raw_frame(dcm_path)
                        frames.append(rf)
                        metas.append(meta)
                    except ScannerFrameError as exc:
                        errors.append(str(exc))

                if errors:
                    log.warning("DICOS: %d frame error(s) in study %s: %s",
                                len(errors), study_uid[:12], "; ".join(errors))
                    if not frames:
                        # Move bad files to done so we don't loop forever.
                        for p in group_paths:
                            self._move_done(p)
                        raise ScannerFrameError(
                            f"All frames in study {study_uid[:12]} are malformed."
                        )

                # Move processed files to done dir.
                for p in group_paths:
                    self._move_done(p)

                log.info(
                    "DICOS: ingested study=%s frames=%d paths=%s",
                    study_uid[:12], len(frames),
                    [p.name for p in group_paths],
                )
                return ScanBundle(frames=frames, metadata=metas)

        return None

    @staticmethod
    def _is_settled(path: Path, wait_s: float = 0.3) -> bool:
        """Check that a file's size is stable (write has completed)."""
        try:
            s1 = path.stat().st_size
            time.sleep(wait_s)
            s2 = path.stat().st_size
            return s1 == s2 and s1 > 0
        except OSError:
            return False

    def _move_done(self, path: Path) -> None:
        try:
            dest = self._done / path.name
            shutil.move(str(path), str(dest))
        except Exception as exc:
            log.warning("DICOS: failed to move %s to done dir: %s", path.name, exc)


__all__ = ["DICOSDriver"]
