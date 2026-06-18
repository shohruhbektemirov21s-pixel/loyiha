# Acquisition Layer

Hardware-to-software bridge: gets scan images off physical X-ray scanners
and into the detection pipeline reliably and in real time.

---

## Driver hierarchy — always choose the highest available

| Priority | Driver | Data fidelity | Requires |
|----------|--------|--------------|---------|
| 1 | **`vendor_sdk`** | Raw dual-energy pixel arrays. Full material discrimination. | Vendor SDK wheel (NDA). |
| 2 | **`dicos`** | ANSI/NEMA DICOS standard. Raw 12–16-bit pixels. Dual-energy when scanner exports separate channels. | `pydicom`. Hot folder access. |
| 3 | **`framegrab`** | ⚠ Rendered RGB display only. **See §RGB-vs-raw below.** | `opencv-python-headless`. HDMI frame grabber device. |

Set `XRAY_ACQ_DRIVER` to select the path. The system will not silently fall
back — you must explicitly choose a lower-fidelity path.

---

## ⚠ RGB-vs-raw data tradeoff (FRAMEGRAB path)

**This is the most important limitation to understand before deploying the
framegrab path.**

The `framegrab` driver captures the **operator monitor output** from the
scanner via an HDMI frame grabber. This is the rendered, colourised RGB
image the scanner produces for human viewing — it is **not** the raw sensor
data.

### What is lost

| Property | DICOS / SDK path | Framegrab path |
|----------|-----------------|---------------|
| Raw attenuation coefficients | ✅ Preserved | ❌ Lost — display tone-mapped |
| High-energy channel | ✅ Separate array | ❌ Merged into RGB display |
| Low-energy channel | ✅ Separate array | ❌ Merged into RGB display |
| Pixel bit depth | 12–16 bit | 8 bit per channel |
| Material decomposition | ✅ Possible | ❌ Not possible |
| Hounsfield-equivalent values | ✅ CT path | ❌ Not available |
| Pixel physical scale (mm) | ✅ From DICOS tag (0028,0030) | ❌ Unknown |
| Vendor-neutral data | ✅ DICOS | ❌ Display depends on scanner model/settings |

### Impact on detection accuracy

1. **Material discrimination is limited** to interpreting the colour palette
   the scanner software already assigned. Orange pixels conventionally mean
   organic material; blue/green mean dense metal — but this mapping is
   scanner-model-specific and operator-configurable. A model trained on
   Smiths Detection output will not generalise to L3 output without
   retraining.

2. **On-screen overlays are burned in.** Alarm boxes, operator annotations,
   scan counters, and timestamps drawn by the scanner UI are part of the
   captured image. The detector must tolerate these artefacts.

3. **Quantisation loss.** A 16-bit raw image at 65 536 grey levels becomes
   an 8-bit display image at 256 levels. High-Z and low-Z materials that
   differ by a few Hounsfield units may be indistinguishable after tone
   mapping.

4. **Temporal jitter.** The frame grabber introduces 1–3 frame capture
   latency. At 30 fps this is 33–100 ms. Fast-moving belt items may appear
   slightly blurred.

### When the framegrab path is acceptable

- Rapid proof-of-concept or integration testing when the vendor SDK is
  unavailable.
- Retrofit installations where the scanner vendor refuses SDK access and
  DICOS export is not supported.
- Secondary verification overlay on scanners where the operator console is
  a separate machine (thin-client architecture).

### Structural enforcement

`CaptureMetadata.is_raw_dual_energy` is **always `False`** for framegrab
frames. This flag is set in code, not configuration. Any downstream code
that requires raw dual-energy data must check this flag and either reject
the frame or document that it is operating on display data.

---

## Configuration reference

All variables are prefixed `XRAY_ACQ_`.

```
XRAY_ACQ_DRIVER            dicos | vendor_sdk | framegrab
XRAY_ACQ_SCANNER_ID        Stable hardware ID (e.g. "smiths-lane-1")
XRAY_ACQ_LANE_ID           Lane label for the console UI
XRAY_ACQ_MODALITY          dual_energy | single_energy | multi_view
XRAY_ACQ_SUBJECT           baggage | cargo | vehicle | parcel | other
XRAY_ACQ_OPERATOR_ID       Operator on shift (audit only)
XRAY_ACQ_API_BASE_URL      http://127.0.0.1:8000  (where to POST /v1/detect)
XRAY_ACQ_API_RETRIES       3  (HTTP retry count on transient failures)

# DICOS
XRAY_ACQ_DICOS_WATCH_DIR   /var/lib/xray/incoming
XRAY_ACQ_DICOS_MOVE_DIR    /var/lib/xray/incoming/done  (processed files)
XRAY_ACQ_DICOS_POLL_S      0.5  (directory poll interval)

# Framegrab
XRAY_ACQ_GRAB_DEVICE       0  (device index) or /dev/video0
XRAY_ACQ_GRAB_FPS          30
XRAY_ACQ_GRAB_ROI          x,y,w,h  (crop scanner display area)
XRAY_ACQ_GRAB_STABLE_FRAMES  8   (frames to confirm stability)
XRAY_ACQ_GRAB_STABLE_THRESH  6   (mean abs pixel diff threshold)
XRAY_ACQ_GRAB_SCAN_TIMEOUT   30.0

# Vendor SDK
XRAY_ACQ_SDK_VENDOR        smiths | l3
XRAY_ACQ_SDK_HOST          localhost
XRAY_ACQ_SDK_PORT          5000

# Watchdog
XRAY_ACQ_RECONNECT_ATTEMPTS  10
XRAY_ACQ_RECONNECT_DELAY_S   5.0   (base exponential backoff)
XRAY_ACQ_FRAME_TIMEOUT_S     60.0

# SecureImageStore
XRAY_STORE_KEY             64-hex-char AES-256 key
XRAY_STORE_DIR             /var/lib/xray/store
```

---

## Architecture

```
Physical scanner
      │
      │  (USB/PCIe/TCP/hot-folder)
      ▼
 ScannerDriver (dicos | framegrab | vendor_sdk)
      │  read_scan() → ScanBundle[RawFrame + CaptureMetadata]
      ▼
 ConnectionWatchdog
      │  reconnect on drop, timeout, malformed frame
      ▼
 IngestPipeline (datalayer)
      │  validate → AES-256-GCM encrypt → SHA-256 address → SecureImageStore
      │  → AcquisitionResult (scan_id, StorageRefs, modality, frames)
      ▼
 AcquisitionPipeline
      │  POST /v1/detect   (httpx, async, retry)
      ▼
 FastAPI serving layer
```

---

## Vendor SDK integration guide

### Smiths Detection (HI-SCAN, ECIX)

1. Obtain `smiths_xray_sdk` wheel from Smiths Detection OEM Integration.
2. Install on the scanner workstation: `pip install smiths_xray_sdk`.
3. Edit `acquisition/sdk/smiths.py` — replace the `# TODO` blocks with
   real `Session` and `wait_for_scan()` calls (template is in the file).
4. Set `XRAY_ACQ_DRIVER=vendor_sdk XRAY_ACQ_SDK_VENDOR=smiths`.
5. Dual-energy: set `XRAY_ACQ_MODALITY=dual_energy`; the SDK delivers
   `scan.high_energy` and `scan.low_energy` as separate objects.

### L3/Leidos (CTX, ClearScan, MVT)

1. For CTX family: configure the scanner to write DICOS files to a hot
   folder and use `XRAY_ACQ_DRIVER=dicos` — no SDK needed.
2. For ClearScan/MVT: obtain `l3sds_sdk` from L3 OEM Integration Programme.
3. Edit `acquisition/sdk/l3.py` — replace the `# TODO` blocks.
4. Set `XRAY_ACQ_DRIVER=vendor_sdk XRAY_ACQ_SDK_VENDOR=l3`.

### Other vendors

1. Subclass `VendorSDKBase` in a new file under `acquisition/sdk/`.
2. Implement `connect()`, `disconnect()`, `read_scan()`.
3. Set `CaptureMetadata.is_raw_dual_energy` honestly.
4. Register in `acquisition/composition.py` `_build_driver()`.

---

## Deployment checklist

- [ ] `XRAY_ACQ_DRIVER` set to the highest available path.
- [ ] `XRAY_STORE_KEY` set to a 64-hex-char secret (generated offline, stored in vault).
- [ ] `XRAY_STORE_DIR` on an encrypted volume.
- [ ] No egress firewall rules opened — `XRAY_ACQ_API_BASE_URL` points to localhost or LAN only.
- [ ] `XRAY_ACQ_DICOS_WATCH_DIR` owned by the service user, not world-writable.
- [ ] Frame grabber ROI (`XRAY_ACQ_GRAB_ROI`) validated against actual display resolution.
- [ ] If using framegrab: detection model trained/validated on rendered RGB data from this scanner model.
- [ ] systemd `Restart=on-failure` or equivalent — pipeline exits on `ScannerUnavailableError`.
- [ ] Log rotation configured — pipeline logs fidelity metadata for every scan.
- [ ] `CaptureMetadata.is_raw_dual_energy` verified in first-run logs before going live.

---

## Files

```
acquisition/
  README.md          ← this file
  protocol.py        ScannerDriver Protocol, CaptureConfig, CaptureMetadata, exceptions
  dicos.py           DICOS hot-folder driver (pydicom)
  framegrab.py       HDMI frame grabber driver (OpenCV) — RGB fallback
  watchdog.py        ConnectionWatchdog — reconnect loop, frame timeout
  pipeline.py        AcquisitionPipeline — async loop, dedup, POST /v1/detect
  composition.py     build_acquisition_pipeline() — env-driven composition root
  sdk/
    __init__.py      Guide for adding new vendor drivers
    base.py          VendorSDKBase abstract class
    smiths.py        Smiths Detection stub + implementation template
    l3.py            L3/Leidos stub + implementation template
```
