"""Training entrypoint for the unified weapons detector (runs on the GPU box).

Defaults encode the project's priorities, not Ultralytics' demo settings:

* **YOLO11m** baseline. RT-DETR-R50 is a one-flag swap (``--model rtdetr-l.pt``)
  once the eval harness is the arbiter; pick the winner on held-out *recall at
  the deploy operating point*, never on mAP alone.
* **imgsz 1024**, not 640. Weapons in cluttered baggage are small and thin
  (a knife edge is a few pixels wide); downsampling to 640 erases exactly the
  hard positives we must not miss. Costs throughput — measured against the
  lane's latency budget on the target hardware.
* **Recall-leaning loss/val.** Higher ``box`` gain and we always validate at a
  low ``conf`` so val recall reflects what the calibrated adapter will actually
  surface. The real operating threshold is chosen *after* training by
  eval/recall_eval.py on the test split, then set per-class in the adapter.
* **ONNX export** at the end — the air-gapped serving runtime is onnxruntime
  (see ModelProvenance.runtime), and we hash the exported weights so provenance
  in the contract is the ground truth of what ran.

This module imports ultralytics lazily inside ``main`` so the file is importable
(and unit-checkable) on a box without the ML stack.
"""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path

from detector.taxonomy import NET_CONF


def sha256_file(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> None:
    ap = argparse.ArgumentParser(description="Train the unified weapons detector.")
    ap.add_argument("--model", default="yolo11m.pt", help="yolo11m.pt | yolo11l.pt | rtdetr-l.pt")
    ap.add_argument("--data", default=str(Path(__file__).parents[1] / "data" / "dataset.yaml"))
    ap.add_argument("--imgsz", type=int, default=1024)
    ap.add_argument("--epochs", type=int, default=120)
    ap.add_argument("--batch", type=int, default=16)
    ap.add_argument("--device", default="0")
    ap.add_argument("--project", default="runs/weapons")
    ap.add_argument("--name", default="yolo11m_imgsz1024")
    ap.add_argument("--export-onnx", action="store_true")
    args = ap.parse_args()

    from ultralytics import YOLO  # lazy

    model = YOLO(args.model)
    model.train(
        data=args.data,
        imgsz=args.imgsz,
        epochs=args.epochs,
        batch=args.batch,
        device=args.device,
        project=args.project,
        name=args.name,
        # recall-leaning + occlusion-robust augmentation
        box=8.0,             # emphasize localization of small/thin weapons
        cls=0.5,
        hsv_v=0.5,           # X-ray intensity varies across scanners/penetration
        mosaic=1.0,          # synthetic clutter/occlusion — the failure regime
        mixup=0.1,
        scale=0.5,
        fliplr=0.5,
        flipud=0.0,          # X-ray has a meaningful vertical (objects don't flip top/bottom freely)
        patience=25,
        # validate at a low conf so reported val recall is honest for our use
        # (the deployed per-class thresholds are set later by the eval harness).
        conf=NET_CONF,
    )

    best = Path(args.project) / args.name / "weights" / "best.pt"
    print(f"best weights: {best}")
    if best.exists():
        print(f"weights_sha256: {sha256_file(best)}  # -> ModelProvenance.weights_sha256")

    if args.export_onnx and best.exists():
        onnx_path = YOLO(str(best)).export(format="onnx", imgsz=args.imgsz, opset=17, dynamic=False)
        print(f"onnx: {onnx_path}")
        print(f"onnx_sha256: {sha256_file(onnx_path)}")


if __name__ == "__main__":
    main()
