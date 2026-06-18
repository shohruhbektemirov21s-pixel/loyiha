# Model Update Runbook

**System**: X-ray Customs Assistant  
**Applies to**: Detector weights (ONNX), VLM weights (GGUF)  
**Frequency**: As needed (triggered by drift alerts or scheduled retraining cycle)

---

## Overview

Model updates follow a **four-environment pipeline**:

```
[Training box]              [Air-gapped server]
     │                              │
     ├─ Offline retraining          │
     ├─ Evaluation against          │
     │  held-out test set           │
     ├─ Sign bundle (minisign)      │
     ├─ Write to USB ───────────────┤
     │                              ├─ Verify signature
     │                              ├─ Verify SHA-256
     │                              ├─ Smoke test on staging
     │                              ├─ Backup existing weights
     │                              ├─ Promote to production
     │                              └─ Restart affected service
```

**The training box has NO network path to the air-gapped server.**  
USB is the only transport. USB must be physically escorted.

---

## Prerequisites

- [ ] Model has been evaluated on the held-out test set and meets minimum thresholds:
  - Detector: precision ≥ 0.90, recall ≥ 0.95 on threat categories
  - VLM: rationale quality reviewed by domain expert
- [ ] Bundle is signed with the `model-signing.key` (held by MLOps lead)
- [ ] `model-signing.pub` is installed at `/etc/xray/model-signing.pub`
- [ ] System is not processing a high-volume shift (schedule update during low-traffic window)
- [ ] Maintenance notification sent to shift supervisors

---

## Step 1 — Receive and mount USB

```bash
# Confirm USB is write-protected (physically, if hardware switch exists)
# Mount read-only
mount -o ro /dev/sdX /media/usb

# Verify the bundle file is present
ls /media/usb/
# Expected: detector-v2.4.2.tar.gz  detector-v2.4.2.tar.gz.sig
# OR:       qwen3-vl-7b-v1.1.gguf   qwen3-vl-7b-v1.1.gguf.sig
```

## Step 2 — Run update script

```bash
# For detector update:
MODEL_TYPE=detector \
BUNDLE=/media/usb/detector-v2.4.2.tar.gz \
bash deploy/scripts/update-model.sh

# For VLM update:
MODEL_TYPE=vlm \
BUNDLE=/media/usb/qwen3-vl-7b-v1.1.gguf \
bash deploy/scripts/update-model.sh
```

The script performs automatically:
1. Signature verification (minisign or gpg)
2. SHA-256 checksum verification against `manifest.json` in bundle
3. Pre-update backup
4. Smoke test (detector: runs inference on a test image; VLM: loads model)
5. Atomic promotion (old weights renamed `.previous`)
6. Registry manifest update
7. Service restart with healthcheck polling

---

## Step 3 — Post-update verification

```bash
bash deploy/scripts/health-check.sh
```

Specifically verify:
- [ ] `Model integrity: detector OK` (or vlm)
- [ ] Container restarted and reached healthy state
- [ ] Run a test scan end-to-end from the operator console
- [ ] Check Grafana: detection score distribution should match expectations

---

## Rollback

If the update causes degradation:

```bash
# Restore previous weights
MODEL_DIR=/var/lib/xray/models
mv ${MODEL_DIR}/detector.onnx ${MODEL_DIR}/detector.onnx.broken
mv ${MODEL_DIR}/detector.onnx.previous ${MODEL_DIR}/detector.onnx

# Restart
docker compose restart detector
bash deploy/scripts/health-check.sh
```

---

## Alert response

When `HighFalsePositiveRate` or `ElevatedFalseNegativeRate` fires:

| Alert | Immediate action | Model action |
|-------|-----------------|--------------|
| FP > 20% (warning) | Increase manual inspection rate; notify supervisor | Export feedback data; initiate retraining |
| FP > 40% (critical) | Switch to 100% manual inspection; disable auto-verdict display | Emergency retraining |
| FN > 3% (critical) | **Do not clear any scans automatically**; 100% manual | Emergency retraining; escalate to ops lead |
| Model weight checksum failed | Halt system; do not trust results | Roll back to previous weights immediately |
