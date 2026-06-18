"""Acquisition layer — hardware-to-software bridge for X-ray scanner ingestion.

Driver hierarchy (highest fidelity first):
  1. vendor_sdk  — proprietary SDK; raw dual-energy pixel arrays
  2. dicos       — ANSI/NEMA DICOS hot-folder; raw 12-16 bit pixels
  3. framegrab   — HDMI frame grabber; rendered RGB display only ⚠

See README.md for the full RGB-vs-raw data tradeoff documentation.
"""

from acquisition.protocol import (
    CaptureConfig,
    CaptureMetadata,
    DriverType,
    FrameLabel,
    ScanBundle,
    ScannerConnectionError,
    ScannerError,
    ScannerFrameError,
    ScannerTimeoutError,
    ScannerUnavailableError,
)

__all__ = [
    "CaptureConfig",
    "CaptureMetadata",
    "DriverType",
    "FrameLabel",
    "ScanBundle",
    "ScannerError",
    "ScannerConnectionError",
    "ScannerTimeoutError",
    "ScannerFrameError",
    "ScannerUnavailableError",
]
