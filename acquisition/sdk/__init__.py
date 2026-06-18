"""Vendor SDK driver stubs.

Each sub-module implements ``ScannerDriver`` for one vendor's proprietary
SDK.  The SDK library itself is never bundled here — it must be installed
separately on the scanner box under a vendor NDA.

Available stubs
---------------
  smiths.py — Smiths Detection (HI-SCAN, ECIX, IONSCAN families)
  l3.py     — L3/Leidos (ProVision, ClearScan, CTX families)

How to add a new vendor
-----------------------
1. Subclass ``VendorSDKBase`` from this package.
2. Implement ``connect``, ``disconnect``, ``read_scan``.
3. Keep the SDK import inside ``connect()`` with a clear ImportError message.
4. Always set ``CaptureMetadata.is_raw_dual_energy`` honestly — only True
   when the SDK actually delivers separate high/low energy pixel arrays.
5. Register the driver in ``composition.py`` ``_VENDOR_DRIVERS`` dict.
"""
