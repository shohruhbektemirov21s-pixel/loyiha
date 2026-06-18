# Data Diode / Sneakernet Runbook

**System**: X-ray Customs Assistant  
**Purpose**: Move feedback data OUT for retraining and model updates IN — without creating any network path between the air-gapped server and external systems.

---

## Security model

```
  ┌─────────────────────────┐        ┌──────────────────────────┐
  │   AIR-GAPPED SERVER     │        │   OFFLINE TRAINING BOX   │
  │                         │  USB   │                          │
  │  data-export.sh ───────►│══════► │  feedback import + retrain│
  │                         │        │                          │
  │  update-model.sh ◄──────│◄══════ │  model bundle + signature │
  │                         │  USB   │                          │
  └─────────────────────────┘        └──────────────────────────┘
         │                                      │
         │ NO network path                      │ NO network path
         │ between these systems                │ to internet
```

**Invariants**:
- The training box has **zero network connectivity** to the air-gapped server.
- USB is the **only** transport medium.
- Data flows are **one-way per USB trip**: export USBs carry data out only; import USBs carry model weights in only. Never mix directions on the same USB.
- Both sides verify cryptographic signatures before acting on transferred data.

---

## Outbound: feedback data export (labels → training box)

**Frequency**: Weekly, or when `ElevatedFalsePositiveRate` / `ElevatedFalseNegativeRate` alerts fire.

**Required personnel**: One operator + one supervisor (two-person rule).

### Procedure

1. **Supervisor approves** the export in the physical logbook:
   ```
   Date:
   Reason: [routine weekly | alert response]
   Records to export: ~N (from Grafana feedback_total counter)
   Supervisor sign-off:
   ```

2. **Operator mounts a clean USB** (verified write-once or freshly wiped):
   ```bash
   # Verify USB is writable and has enough space
   df -h /media/export-usb
   ```

3. **Run export script**:
   ```bash
   EXPORT_DEST=/media/export-usb \
     bash deploy/scripts/data-export.sh
   ```
   The script exports only ground-truth labels (bounding boxes, category corrections, outcomes, missed regions). **No raw scan images leave the server** unless `EXPORT_CROPS=true` is explicitly set and supervisor-approved.

4. **Verify output** on the USB before unmounting:
   ```bash
   ls -lh /media/export-usb/
   cat /media/export-usb/manifest-*.json
   ```

5. **Unmount and physically hand off** to the training team under escort:
   ```bash
   umount /media/export-usb
   ```

6. **Log the transfer** in the physical logbook:
   ```
   USB serial: [label on USB]
   File:       xray-feedback-TIMESTAMP.tar.gz.enc
   SHA-256:    [from manifest]
   Handed to:  [training team member name]
   Escorted by: [supervisor name]
   ```

### On the training box

```bash
# Verify signature before decrypting
minisign -V -p /etc/xray/model-signing.pub \
    -m xray-feedback-TIMESTAMP.tar.gz.enc

# Decrypt
openssl enc -d -aes-256-cbc -pbkdf2 -iter 200000 \
    -pass "pass:$(cat /etc/training/backup.key)" \
    -in xray-feedback-TIMESTAMP.tar.gz.enc \
    | tar -xzf -

# Import into training pipeline
python training/import_feedback.py feedback_labels.jsonl
```

---

## Inbound: model weight update (training box → server)

**Frequency**: After each successful retraining cycle.

**Required personnel**: MLOps lead (signs the bundle) + ops engineer (installs it).

### Procedure

1. **On the training box** — prepare and sign the bundle:
   ```bash
   # Create bundle with manifest
   python deploy/model-registry/registry.py push detector /path/to/detector-v2.5.0.onnx \
       --version 2.5.0 --name "YOLOv8-xray-customs"

   tar -czf detector-v2.5.0.tar.gz \
       detector-v2.5.0.onnx \
       manifest.json

   # Sign with MLOps lead's private key
   minisign -S -s /secure/model-signing.key \
       -m detector-v2.5.0.tar.gz
   ```

2. **Write to a clean USB**:
   ```bash
   cp detector-v2.5.0.tar.gz     /media/import-usb/
   cp detector-v2.5.0.tar.gz.sig /media/import-usb/
   sync && umount /media/import-usb
   ```

3. **Physical transport** with escort to the air-gapped server room.

4. **On the air-gapped server** — run `update-model.sh` (see `model-update.md`):
   ```bash
   MODEL_TYPE=detector \
   BUNDLE=/media/usb/detector-v2.5.0.tar.gz \
     bash deploy/scripts/update-model.sh
   ```

5. **After installation** — immediately remove USB:
   ```bash
   umount /media/usb
   ```
   Log the transfer in the physical logbook.

---

## USB hygiene rules

| Rule | Rationale |
|------|-----------|
| Export USBs are write-once; never re-used for imports | Prevents malicious data injection |
| Import USBs verified with SHA-256 + signature before mounting | Ensures supply-chain integrity |
| USBs stored in a locked cabinet when not in use | Physical access control |
| USBs logged by serial number in the physical ledger | Accountability |
| USB auto-mount disabled in `/etc/udev/rules.d/` (done by hardening.sh) | Prevents accidental mount by non-operators |
| Maximum of 5 USB transfers per week without additional supervisor sign-off | Anomaly detection |

---

## Disabling USB automount (applied by hardening.sh)

```
# /etc/udev/rules.d/99-disable-usb-automount.rules
ACTION=="add", SUBSYSTEM=="block", ENV{ID_BUS}=="usb", ENV{UDISKS_AUTO}="0"
```

Operators mount manually: `mount -o ro /dev/sdX /media/usb`
