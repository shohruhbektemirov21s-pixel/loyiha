# Detector inference config — serving must match training/export

The contract `score` and every box are only trustworthy if the model is served
**exactly** as it was exported and calibrated. This file is the canonical record
of that config and the deploy runbook. Composition root: `composition.py`
(`build_detector`), wired in `app/main.py`'s lifespan.

## Canonical config (must equal train/export)

| Knob | Value | Source of truth | Why it's not free at runtime |
|------|-------|-----------------|------------------------------|
| `imgsz` | **1024** | `composition.EXPORT_IMGSZ`, `UltralyticsPredictor.imgsz` | ONNX exported static (`dynamic=False`); small/thin weapons need it. Drift shifts geometry + recall. |
| `conf` (net) | **0.05** | `taxonomy.NET_CONF` | Wide net; anything dropped here is an unrecoverable FN. Deploy thresholding happens later, in the adapter. |
| `iou` (NMS) | **0.6** | `taxonomy.NET_IOU` | NMS IoU for YOLO. RT-DETR (NMS-free) ignores it. |
| operating thresholds | per-class, post-calibration | `adapter.DEFAULT_THRESHOLDS` | Recall-first; set from the recall@FP curve on test, not guessed. |
| calibration | per-class Platt `{label:[a,b]}` | `XRAY_DETECTOR_CALIBRATION` JSON | Makes `score` a real probability so downstream thresholds mean something. Absent ⇒ identity (adapter marks `calibrated=false`). |

`conf`/`iou` come from `taxonomy.py` — the *single* source shared by training
(val recall), serving, and `eval/profile_latency.py`, so the three can't drift.
Leave `XRAY_DETECTOR_CONF/IOU` unset unless you mean to override locally.

## Environment (GPU serving box)

```bash
export XRAY_DETECTOR_ENABLED=1
export XRAY_DETECTOR_WEIGHTS=/var/lib/xray/models/best.onnx   # .onnx | .engine | .pt
export XRAY_DETECTOR_DEVICE=cuda:0
export XRAY_DETECTOR_CALIBRATION=/var/lib/xray/models/platt.json   # optional, recommended
export XRAY_DETECTOR_VERSION=0.1.0
# imgsz/conf/iou intentionally left at the canonical defaults above.
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

`runtime` (provenance) is inferred from the weights extension:
`.onnx → onnxruntime`, `.engine`/`.plan → tensorrt`, `.pt`/`.pth →
pytorch-ultralytics`. Override with `XRAY_DETECTOR_RUNTIME` if needed.

`weights_sha256` is **not** configured — `build_detector` hashes the bytes it
loads, so the audit log records what actually ran. Confirm it against the hash
`train.py --export-onnx` printed at export time.

## Behaviour by box

- **Contract/API box (no ML stack):** `XRAY_DETECTOR_ENABLED` off (default). The
  seam returns the honest **501** stub — never a faked detection. `composition.py`
  imports fine here; the heavy import only fires inside `build_detector`.
- **GPU serving box:** enabled. Startup is **fail-closed** — missing/unloadable
  weights, a bad calibration file, or an absent ML stack abort boot. The process
  never degrades into a silent 501 on a box that is supposed to detect.

On boot the lifespan logs the full effective config (name, version, sha256…,
runtime, imgsz, device, conf, iou, calibrated yes/no). That log line is the
operator's proof the deployment matches the exported artifact.

## Before flipping `ENABLED` on (the acceptance gate)

Per the optimization discipline: **validate the served artifact against the
full-precision baseline on the same held-out test set and report the accuracy
delta.** A faster/quantized ONNX/TensorRT engine ships only when its per-class
**miss rate** is within the signed-off delta of the FP baseline — never trade
recall for speed without explicit sign-off.

The gate is three commands, all on the GPU box except the last (pure numpy, runs
anywhere). `imgsz/conf/iou` default from `taxonomy.py` everywhere, so the profile
and the eval both reflect the real serving net.

```bash
# 1. Profile each model (latency budget + per-stage breakdown).
python -m detector.eval.profile_latency --weights weights/fp32.onnx \
    --image samples/real_scan.png --iters 300 --json reports/fp32_latency.json
python -m detector.eval.profile_latency --weights weights/int8.engine \
    --image samples/real_scan.png --iters 300 --json reports/int8_latency.json

# 2. Run BOTH models over the SAME held-out test split → one EvalBundle each.
python -m detector.eval.predict_dataset --weights weights/fp32.onnx \
    --data detector/data/dataset.yaml --split test --device cuda:0 \
    --latency-json reports/fp32_latency.json --out reports/fp32_test.json
python -m detector.eval.predict_dataset --weights weights/int8.engine \
    --data detector/data/dataset.yaml --split test --device cuda:0 \
    --latency-json reports/int8_latency.json --out reports/int8_test.json

# 3. The gate. Refuses to compare if the two ground-truth sets differ; FAILs
#    (exit 1) if any PRIMARY weapon class loses recall beyond the signed
#    tolerance — regardless of speedup. Exit 1 hard-blocks a deploy script.
python -m detector.eval.accuracy_delta \
    --baseline reports/fp32_test.json --candidate reports/int8_test.json \
    --recall-tolerance 0.0          # raise ONLY with detection-lead sign-off
```

The gate measures recall at the **per-class deploy thresholds** it imports from
`adapter.DEFAULT_THRESHOLDS` — the same operating point we serve. Default
`--recall-tolerance 0.0` means *any* primary-class recall regression fails;
a non-zero value is an explicit sign-off and is stamped into the report. Only
after a PASS do you set `XRAY_DETECTOR_ENABLED=1` with the candidate weights.
