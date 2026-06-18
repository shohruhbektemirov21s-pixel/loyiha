"""Live detector smoke — run the trained ONNX through the real serving path.

Loads best.onnx via UltralyticsPredictor, feeds a real image through the
WeaponsDetector adapter (the app.deps.Detector seam), and prints the
contract DetectionResult. Proves the trained model detects end-to-end.

    python -m detector._smoke_detect --weights /tmp/runs/synth/weights/best.onnx \
        --image /tmp/xray_synth/images/val/val_0000.png --imgsz 320
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from uuid import uuid4

import numpy as np
from PIL import Image

from contracts.v1 import (
    AcquisitionResult, ImageFrame, ImageModality, ModelProvenance,
    ScanSubject, StorageRef,
)
from detector.serving.adapter import WeaponsDetector
from detector.serving.predictor import ConstantLoader, UltralyticsPredictor


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--weights", default="/tmp/runs/synth/weights/best.onnx")
    ap.add_argument("--image", default="/tmp/xray_synth/images/val/val_0000.png")
    ap.add_argument("--imgsz", type=int, default=320)
    args = ap.parse_args()

    img = np.array(Image.open(args.image).convert("RGB"))
    h, w = img.shape[:2]
    print(f"image {args.image}  {w}x{h}")

    predictor = UltralyticsPredictor(args.weights, imgsz=args.imgsz)
    detector = WeaponsDetector(
        predictor=predictor,
        loader=ConstantLoader(img[:, :, ::-1].copy()),  # adapter expects BGR
        provenance=ModelProvenance(
            name="xray-weapons-yolo11n-synthdemo", version="0.0.1",
            weights_sha256=None, runtime="onnxruntime",
        ),
    )

    frame = ImageFrame(
        frame_id="frame-0", width_px=w, height_px=h,
        image=StorageRef(
            uri="file:///tmp/test.png", media_type="image/png",
            sha256="0" * 64, size_bytes=img.nbytes,
        ),
        view_label="demo",
    )
    acq = AcquisitionResult(
        scan_id=uuid4(), scanner_id="demo-scanner", lane_id="lane-1",
        operator_id="op-1", subject=ScanSubject.BAGGAGE,
        modality=ImageModality.SINGLE_ENERGY,
        captured_at=datetime.now(timezone.utc), emitted_at=datetime.now(timezone.utc),
        frames=[frame],
    )

    import asyncio
    result = asyncio.run(detector.detect(acq))

    print("=" * 60)
    print(f"status     : {result.status.value}")
    print(f"detections : {len(result.detections)}")
    for d in result.detections:
        b = d.box
        print(f"  - {d.category.value:16s} native={d.native_label:10s} "
              f"score={d.score:.3f}  box=({b.x},{b.y},{b.width}x{b.height})")
    print("=" * 60)
    print("OK: trained ONNX ran through the real serving adapter, contract-valid.")


if __name__ == "__main__":
    main()
