"""Auto-labelling data collector — build a local YOLO dataset with NO manual
labels and NO external API key.

The zero-shot teacher (YOLO-World + CLIP, see serving/openvocab.py) reads every
image in a source folder, and its detections are written out as YOLO labels.
A fast student (yolo11n) is then fine-tuned on those pseudo-labels. This is the
local, self-collecting active-learning loop: point it at the scanner/camera
output folder and the dataset grows on its own; an operator only needs to
correct the labels later (the datalayer feedback path already models that).

    python -m detector.autolabel --src /tmp/incoming --out /tmp/xray_auto \
        --val-frac 0.2 --min-score 0.25

Emits the YOLO layout dataset.yaml expects:
    <out>/images/{train,val}/*  <out>/labels/{train,val}/*.txt  <out>/dataset.yaml

Student classes are a subset of taxonomy.WEAPON_CLASSES (gun/knife/scissors) so
the fine-tuned weights plug straight into the production WeaponsDetector adapter.
NOTE: the teacher is a NATURAL-image model — on real X-ray, review/correct the
pseudo-labels before trusting them (that is what the operator-feedback loop is for).
"""

from __future__ import annotations

import argparse
import random
import shutil
from pathlib import Path

from contracts.v1 import ThreatCategory

# Teacher category -> student class name (must exist in WEAPON_CLASSES order).
_CAT_TO_CLASS: dict[ThreatCategory, str] = {
    ThreatCategory.FIREARM: "gun",
    ThreatCategory.BLADED_WEAPON: "knife",
}
# Student class list (index == YOLO id). Kept small + taxonomy-compatible.
STUDENT_CLASSES = ("gun", "knife", "scissors")
_NAME_TO_ID = {n: i for i, n in enumerate(STUDENT_CLASSES)}

_IMG_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff"}


def _student_class(native_label: str, category: ThreatCategory) -> str | None:
    """Resolve a teacher detection to a student class name (or None to skip)."""
    if "scissor" in native_label.lower():
        return "scissors"
    return _CAT_TO_CLASS.get(category)


def collect(src: Path, out: Path, val_frac: float, min_score: float,
            imgsz: int, conf: float) -> dict:
    import numpy as np
    from PIL import Image

    from detector.serving.openvocab import (
        ClipReclassifier, OpenVocabDetector, YoloWorldPredictor,
    )
    from detector.serving.predictor import ConstantLoader
    from contracts.v1 import (
        AcquisitionResult, ImageFrame, ImageModality, ModelProvenance,
        ScanSubject, StorageRef,
    )
    import asyncio
    from datetime import datetime, timezone
    from uuid import uuid4

    images = sorted(p for p in src.rglob("*") if p.suffix.lower() in _IMG_EXTS)
    if not images:
        raise SystemExit(f"No images under {src}")
    print(f"teacher: loading YOLO-World + CLIP … ({len(images)} source images)")

    predictor = YoloWorldPredictor(prompts=None, conf=conf, imgsz=imgsz)
    reclf = ClipReclassifier()
    prov = ModelProvenance(name="autolabel-teacher", version="yw+clip",
                           weights_sha256=None, runtime="ultralytics")

    rng = random.Random(13)
    for split in ("train", "val"):
        (out / "images" / split).mkdir(parents=True, exist_ok=True)
        (out / "labels" / split).mkdir(parents=True, exist_ok=True)

    stats = {"images": 0, "labelled": 0, "boxes": 0,
             "per_class": {c: 0 for c in STUDENT_CLASSES}}

    for idx, ip in enumerate(images):
        try:
            arr = np.array(Image.open(ip).convert("RGB"))
        except Exception as exc:  # noqa: BLE001
            print(f"  skip {ip.name}: {exc}")
            continue
        h, w = arr.shape[:2]
        det = OpenVocabDetector(
            predictor=predictor, loader=ConstantLoader(arr[:, :, ::-1].copy()),
            provenance=prov, reclassifier=reclf,
        )
        frame = ImageFrame(frame_id="f0", width_px=w, height_px=h,
                           image=StorageRef(uri="file:///t", media_type="image/png",
                                            sha256="0" * 64, size_bytes=arr.nbytes),
                           view_label="auto")
        acq = AcquisitionResult(scan_id=uuid4(), scanner_id="auto", lane_id="lane-1",
                                operator_id="auto", subject=ScanSubject.BAGGAGE,
                                modality=ImageModality.SINGLE_ENERGY,
                                captured_at=datetime.now(timezone.utc),
                                emitted_at=datetime.now(timezone.utc), frames=[frame])
        result = asyncio.run(det.detect(acq))

        lines: list[str] = []
        for d in result.detections:
            if d.score < min_score:
                continue
            cls = _student_class(d.native_label, d.category)
            if cls is None:
                continue
            b = d.box
            cx, cy = (b.x + b.width / 2) / w, (b.y + b.height / 2) / h
            bw, bh = b.width / w, b.height / h
            lines.append(f"{_NAME_TO_ID[cls]} {cx:.6f} {cy:.6f} {bw:.6f} {bh:.6f}")
            stats["per_class"][cls] += 1
            stats["boxes"] += 1

        split = "val" if rng.random() < val_frac else "train"
        stem = f"auto_{idx:05d}"
        shutil.copy(ip, out / "images" / split / f"{stem}{ip.suffix.lower()}")
        (out / "labels" / split / f"{stem}.txt").write_text("\n".join(lines) + ("\n" if lines else ""))
        stats["images"] += 1
        if lines:
            stats["labelled"] += 1
        print(f"  [{idx+1}/{len(images)}] {ip.name}: {len(lines)} box(es) -> {split}")

    names = "\n".join(f"  {i}: {n}" for i, n in enumerate(STUDENT_CLASSES))
    (out / "dataset.yaml").write_text(
        f"path: {out}\ntrain: images/train\nval: images/val\ntest: images/val\n\nnames:\n{names}\n"
    )
    return stats


def main() -> None:
    ap = argparse.ArgumentParser(description="Zero-shot auto-labelling data collector")
    ap.add_argument("--src", required=True, help="folder of images to auto-label")
    ap.add_argument("--out", default="/tmp/xray_auto")
    ap.add_argument("--val-frac", type=float, default=0.2)
    ap.add_argument("--min-score", type=float, default=0.25)
    ap.add_argument("--imgsz", type=int, default=640)
    ap.add_argument("--conf", type=float, default=0.05)
    args = ap.parse_args()

    stats = collect(Path(args.src), Path(args.out), args.val_frac,
                    args.min_score, args.imgsz, args.conf)
    print("=" * 56)
    print(f"OK: auto-labelled dataset at {args.out}")
    print(f"    images={stats['images']} labelled={stats['labelled']} boxes={stats['boxes']}")
    print(f"    per-class: {stats['per_class']}")
    print(f"    dataset.yaml -> {Path(args.out)/'dataset.yaml'}")


if __name__ == "__main__":
    main()
