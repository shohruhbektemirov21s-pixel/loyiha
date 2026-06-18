# Detector — the "eye" (Hop 2)

The primary object-detection model: localizes + classifies contraband and emits
a contract-valid `DetectionResult` (the VLM's *only* input). This package owns
everything from raw scan pixels to a calibrated, fail-closed detection message.

**v1 scope: weapons** (`firearm`, `bladed_weapon`) on public data. Meat and
narcotics are deliberately out of scope here — they need dual-energy material
signals + proprietary data and are tracked separately. Do not read v1 numbers as
a claim about those classes.

## Why these choices

| Decision | Choice | Why |
|----------|--------|-----|
| Datasets | PIDray + SIXray + OPIXray | gun/knife coverage; PIDray *hidden* + OPIXray *OL2/OL3* give an occlusion-stratified test set; 3 scanners ≈ a first cross-scanner signal |
| Model | YOLO11m @ imgsz **1024** | small/thin weapons survive; ONNX-exportable to match the `onnxruntime` serving runtime |
| Challenger | RT-DETR-L | NMS-free, strong under clutter; swap via `--model`, judged by the eval harness |
| Headline metric | **per-class miss rate (1−recall) at the deploy threshold** | a missed weapon is the worst failure; mAP hides it |
| Confidence | per-class **Platt calibration** | makes the contract `score` a real probability so downstream thresholds mean something |

## Honest limitations (read before quoting any number)

- Public-set recall is an **upper bound**. Real acceptance is measured on
  held-out data from the **actual target scanner**, which we do not have yet.
- `scissors` is dual-use; recall-first, we flag it as `bladed_weapon` and let
  the operator decide. Expect it to cost precision.
- Platt calibration is logistic; for strongly non-logistic miscalibration,
  switch to isotonic regression (needs a larger calibration split).

## Layout

```
taxonomy.py        native label -> ThreatCategory (single source of truth; index == YOLO id)
serving/
  predictor.py     Predictor/FrameLoader Protocols + UltralyticsPredictor + test fakes (heavy deps lazy)
  image_store.py   StorageRef -> pixels, sha256-verified (fail-closed)
  adapter.py       WeaponsDetector: the app.deps.Detector seam. Clamp, calibrate, threshold, fail-closed.
eval/
  recall_eval.py   IoU match; recall/miss-rate; recall@FP-per-image; occlusion-stratified; AP (secondary)
  calibration.py   per-class Platt calibrator + ECE + reliability table
  profile_latency.py  latency profiler (serving + raw-ORT modes); profile before optimizing
  predict_dataset.py  GPU box: run a model over a split -> EvalBundle JSON (gate input)
  accuracy_delta.py   acceptance gate: optimized vs FP baseline, per-class recall delta + PASS/FAIL
data/
  dataset.yaml     unified classes (lock-stepped to taxonomy)
  prepare.py       remap PIDray/SIXray/OPIXray -> unified YOLO labels (readers wired on data box)
train/
  train.py         YOLO11m/RT-DETR training + ONNX export + weights sha256 for provenance
tests/
  test_adapter_contract.py   contract conformance, no ML deps
```

## What runs where

**This box (contract + numpy only):**
```bash
python -m detector.tests.test_adapter_contract   # 7 contract conformance tests
python -m detector.eval.recall_eval              # recall report on synthetic data (shape demo)
python -m detector.eval.calibration              # ECE before/after on synthetic data
python -m detector.eval.accuracy_delta           # acceptance-gate demo (synthetic; pure numpy)
python -m detector.data.prepare                  # assert class-index spine consistency
```

**GPU / data box:**
```bash
python -m detector.data.prepare                  # (after wiring VOC/COCO readers) build YOLO dataset
python -m detector.train.train --export-onnx     # train YOLO11m@1024, export ONNX, print weights sha256
python -m detector.eval.profile_latency --weights weights/best.onnx --image samples/scan.png
python -m detector.eval.predict_dataset --weights weights/best.onnx --data detector/data/dataset.yaml --split test --out reports/fp32_test.json
```

The acceptance gate (`accuracy_delta`) consumes two such bundles; full
optimized-vs-baseline runbook in [`serving/INFERENCE.md`](serving/INFERENCE.md).

## Wiring into the serving layer

The adapter *is* the `Detector` seam, and it is now wired by the composition
root `serving/composition.py` (`build_detector`), which `app/main.py`'s lifespan
invokes. It assembles `UltralyticsPredictor` + `ObjectStoreLoader` + the Platt
calibrator, hashes the loaded weights for provenance, and overrides
`provide_detector`. It's driven entirely by env (`XRAY_DETECTOR_*`) — there's no
code to edit per deployment.

Off by default ⇒ the seam stays honest: `provide_detector` returns the stub →
**501**, never a faked detection. On the GPU box set `XRAY_DETECTOR_ENABLED=1`
and the weights path; startup is fail-closed. The full env list and the
train↔serve config contract (imgsz/conf/iou/calibration) live in
[`serving/INFERENCE.md`](serving/INFERENCE.md).

Importing `serving/composition.py` is safe on this box — the ML stack import is
deferred to `build_detector`, which only runs when serving is enabled.

## Evaluation methodology (the discipline)

1. **Split**: train / val (tuning only) / **test = different scanner + hidden +
   OL2/OL3** / calib (calibration only). Test is never touched until acceptance.
2. **Match** predictions to GT greedily by score at IoU 0.5 (`recall_eval.py`).
3. **Report per class**: recall and **miss rate at the deploy threshold**,
   precision, FP/image, recall@FP-budget, AP (secondary).
4. **Stratify recall by occlusion** — the curve that exposes silent failure.
5. **Calibrate** on the calib split; report ECE before/after; freeze per-class
   Platt params into the adapter.
6. **Set per-class operating thresholds** from the recall@FP-budget curve, write
   them into the adapter, re-run on test, and report the final miss rates. Those
   are the numbers we stand behind — nothing from val or train.
