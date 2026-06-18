"""Zero-shot open-vocabulary screening smoke + optional Qwen verdict.

    python -m detector._smoke_openvocab --image /tmp/testimg/revolver.jpg [--verdict]

Runs the prompt-driven OpenVocabDetector (no training) over a real image and
prints the contract DetectionResult; with --verdict, chains the Qwen VLM to
produce the Uzbek operator verdict — the full screening pipeline.
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
from uuid import uuid4

import numpy as np
from PIL import Image

from contracts.v1 import (
    AcquisitionResult, ImageFrame, ImageModality, ModelProvenance,
    ScanSubject, StorageRef,
)
from detector.serving.openvocab import ClipReclassifier, OpenVocabDetector, YoloWorldPredictor
from detector.serving.predictor import ConstantLoader


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--image", required=True)
    ap.add_argument("--imgsz", type=int, default=640)
    ap.add_argument("--conf", type=float, default=0.05)
    ap.add_argument("--verdict", action="store_true", help="chain Qwen VLM verdict")
    ap.add_argument("--clip", action="store_true", help="add CLIP crop re-classification stage")
    args = ap.parse_args()

    img = np.array(Image.open(args.image).convert("RGB"))
    h, w = img.shape[:2]
    print(f"image {args.image}  {w}x{h}")

    detector = OpenVocabDetector(
        predictor=YoloWorldPredictor(prompts=None, conf=args.conf, imgsz=args.imgsz),
        loader=ConstantLoader(img[:, :, ::-1].copy()),
        provenance=ModelProvenance(
            name="yolo-world+clip" if args.clip else "yolo-world-openvocab",
            version="v2-s", weights_sha256=None, runtime="ultralytics",
        ),
        reclassifier=ClipReclassifier() if args.clip else None,
    )
    frame = ImageFrame(
        frame_id="frame-0", width_px=w, height_px=h,
        image=StorageRef(uri="file:///t.jpg", media_type="image/jpeg", sha256="0" * 64, size_bytes=img.nbytes),
        view_label="demo",
    )
    acq = AcquisitionResult(
        scan_id=uuid4(), scanner_id="demo", lane_id="lane-1", operator_id="op",
        subject=ScanSubject.BAGGAGE, modality=ImageModality.SINGLE_ENERGY,
        captured_at=datetime.now(timezone.utc), emitted_at=datetime.now(timezone.utc), frames=[frame],
    )

    result = asyncio.run(detector.detect(acq))
    print("=" * 60)
    print(f"status     : {result.status.value}   detections: {len(result.detections)}")
    for d in result.detections:
        b = d.box
        print(f"  - {d.category.value:16s} prompt='{d.native_label}'  score={d.score:.3f}  "
              f"box=({b.x},{b.y},{b.width}x{b.height})")
    print("=" * 60)

    if args.verdict and result.has_findings:
        from contracts.v1.verdict import Locale, VerdictRequest
        from vlm.composition import VLMConfig, build_vlm_generator
        gen = build_vlm_generator(VLMConfig(
            backend_type="ollama", base_url="http://127.0.0.1:11434",
            model="qwen2.5:3b", timeout_s=180, verify=False))
        vr = VerdictRequest(scan_id=result.scan_id, detection=result,
                            locale=Locale.UZ_LATN, emitted_at=datetime.now(timezone.utc))
        v = asyncio.run(gen.generate(vr))
        print(f"QWEN verdict: risk={v.overall_risk.value}")
        print("  " + v.summary_uz.replace("\n", " "))


if __name__ == "__main__":
    main()
