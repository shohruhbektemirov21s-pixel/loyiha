"""Run one model over a held-out split → an ``EvalBundle`` for the gate.

This is the GPU-box step that feeds ``accuracy_delta.py``. Run it once per model
(FP32 baseline, then the optimized candidate) over the **same** ``test`` split;
the two bundles it writes are then compared by the gate, which refuses to
proceed unless their ground truth is identical (so this step is the thing that
guarantees an apples-to-apples delta).

It mirrors the real serving path exactly:
  * the same ``UltralyticsPredictor`` the adapter uses (same imgsz/conf/iou,
    sourced from ``taxonomy``), so measured recall reflects what we deploy;
  * native labels mapped to the shared ``ThreatCategory`` via ``to_category`` —
    the same normalization the adapter applies — so predictions and ground
    truth share the contract vocabulary the gate thresholds on.

Ground truth is read from the YOLO labels ``prepare.py`` produces
(``cls cx cy w h`` normalized); class ids map back through
``taxonomy.WEAPON_CLASSES`` → category. Optionally attach a latency block
(``--latency-json`` from ``profile_latency``) so the gate can pair recall delta
with speedup in one verdict.

GPU / data box:

    python -m detector.eval.predict_dataset \\
        --weights weights/best.onnx --data detector/data/dataset.yaml \\
        --split test --device cuda:0 \\
        --latency-json reports/fp32_latency.json \\
        --out reports/fp32_test.json

Validate wiring anywhere (no ML stack, no images loaded):

    python -m detector.eval.predict_dataset --weights weights/best.onnx \\
        --data detector/data/dataset.yaml --self-check
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

from detector.taxonomy import NET_CONF, NET_IOU, WEAPON_CLASSES, to_category
from detector.eval.accuracy_delta import EvalBundle, save_bundle
from detector.eval.recall_eval import GroundTruth, Prediction

_RUNTIME_BY_EXT = {
    ".onnx": "onnxruntime", ".engine": "tensorrt", ".plan": "tensorrt",
    ".pt": "pytorch-ultralytics", ".pth": "pytorch-ultralytics",
}
_IMG_EXTS = (".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp")


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError as e:
        return f"<unreadable: {e}>"


def _load_data_yaml(path: str) -> dict[str, Any]:
    import yaml  # lazy: not needed on the API box

    cfg = yaml.safe_load(Path(path).read_text())
    # Guard the class-index spine, exactly like prepare.py does.
    from detector.data.prepare import assert_consistent_with_dataset_yaml

    assert_consistent_with_dataset_yaml({int(k): v for k, v in cfg["names"].items()})
    return cfg


def _split_dirs(cfg: dict, split: str) -> tuple[Path, Path]:
    root = Path(cfg["path"])
    images = root / cfg.get(split, f"images/{split}")
    labels = Path(str(images).replace("/images/", "/labels/"))
    return images, labels


def _read_gt(label_file: Path, img_w: int, img_h: int, image_id: str,
             occlusion: str | None) -> list[GroundTruth]:
    """YOLO ``cls cx cy w h`` (normalized) → category-space GroundTruth (xywh px)."""
    out: list[GroundTruth] = []
    if not label_file.is_file():
        return out  # negative image (background) — legal, just no GT
    for line in label_file.read_text().splitlines():
        parts = line.split()
        if len(parts) != 5:
            continue
        cls_id, cx, cy, w, h = int(parts[0]), *map(float, parts[1:])
        category = to_category(WEAPON_CLASSES[cls_id]).value
        bw, bh = w * img_w, h * img_h
        x, y = cx * img_w - bw / 2, cy * img_h - bh / 2
        out.append(GroundTruth(image_id, category, (x, y, bw, bh), occlusion))
    return out


def run(args: argparse.Namespace) -> EvalBundle:
    import cv2  # lazy

    from detector.serving.predictor import UltralyticsPredictor

    cfg = _load_data_yaml(args.data)
    images_dir, labels_dir = _split_dirs(cfg, args.split)
    if not images_dir.is_dir():
        raise FileNotFoundError(f"split images dir not found: {images_dir}")

    occ_map: dict[str, str] = {}
    if args.occlusion_map:
        occ_map = json.loads(Path(args.occlusion_map).read_text())

    predictor = UltralyticsPredictor(
        args.weights, conf=args.conf, iou=args.iou, imgsz=args.imgsz,
        device=args.device,
    )

    gts: list[GroundTruth] = []
    preds: list[Prediction] = []
    image_paths = sorted(p for p in images_dir.iterdir() if p.suffix.lower() in _IMG_EXTS)
    if args.limit:
        image_paths = image_paths[: args.limit]

    for img_path in image_paths:
        image_id = img_path.stem
        img = cv2.imread(str(img_path), cv2.IMREAD_COLOR)
        if img is None:
            raise FileNotFoundError(f"could not decode {img_path}")
        h, w = img.shape[:2]
        occ = occ_map.get(image_id)
        gts.extend(_read_gt(labels_dir / f"{image_id}.txt", w, h, image_id, occ))
        for raw in predictor.predict_frame(img):
            bw, bh = raw.x2 - raw.x1, raw.y2 - raw.y1
            preds.append(Prediction(
                image_id, to_category(raw.native_label).value,
                (raw.x1, raw.y1, bw, bh), raw.score,
            ))

    latency = None
    if args.latency_json:
        prof = json.loads(Path(args.latency_json).read_text())
        latency = prof.get("end_to_end")  # mean_ms/p95_ms/throughput_fps live here

    runtime = args.runtime or _RUNTIME_BY_EXT.get(Path(args.weights).suffix.lower(), "ultralytics")
    provenance = {
        "name": args.name, "version": args.version,
        "weights_sha256": _sha256(args.weights), "runtime": runtime,
        "imgsz": args.imgsz, "conf": args.conf, "iou": args.iou,
        "split": args.split, "device": args.device, "n_images": len(image_paths),
    }
    return EvalBundle(ground_truth=gts, predictions=preds,
                      provenance=provenance, latency=latency)


def _config(args: argparse.Namespace) -> dict:
    return {
        "weights": args.weights, "data": args.data, "split": args.split,
        "imgsz": args.imgsz, "conf": args.conf, "iou": args.iou,
        "device": args.device, "name": args.name, "version": args.version,
        "out": args.out, "limit": args.limit,
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Run a model over a split → EvalBundle JSON.")
    p.add_argument("--weights", required=True, help="path to .onnx/.engine/.pt")
    p.add_argument("--data", required=True, help="dataset.yaml")
    p.add_argument("--split", default="test", help="split to evaluate (held-out!)")
    p.add_argument("--imgsz", type=int, default=1024, help="MUST match export (static ONNX)")
    p.add_argument("--conf", type=float, default=NET_CONF, help="recall-first low net")
    p.add_argument("--iou", type=float, default=NET_IOU)
    p.add_argument("--device", default=None, help="e.g. cuda:0; None = auto")
    p.add_argument("--name", default="xray-weapons-yolo11m")
    p.add_argument("--version", default="0.1.0")
    p.add_argument("--runtime", default=None, help="override; else inferred from ext")
    p.add_argument("--occlusion-map", default=None,
                   help="JSON {image_stem: 'OL1'|'OL2'|...} for occlusion-stratified recall")
    p.add_argument("--latency-json", default=None, help="profile_latency report to embed")
    p.add_argument("--limit", type=int, default=0, help="cap #images (smoke only)")
    p.add_argument("--out", default=None, help="write EvalBundle JSON here")
    p.add_argument("--self-check", action="store_true",
                   help="validate config + dataset.yaml spine without the ML stack")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    if args.self_check:
        cfg = _load_data_yaml(args.data)  # also asserts class-index spine
        images_dir, labels_dir = _split_dirs(cfg, args.split)
        print("[self-check] dataset.yaml class spine consistent with taxonomy.")
        print(f"[self-check] split '{args.split}' resolves to:")
        print(f"             images: {images_dir}  (exists={images_dir.is_dir()})")
        print(f"             labels: {labels_dir}  (exists={labels_dir.is_dir()})")
        print(f"[self-check] weights sha256: {_sha256(args.weights)}")
        print(f"[self-check] config: {json.dumps(_config(args))}")
        print("[self-check] note: actual prediction needs the GPU/ML stack.")
        return 0

    bundle = run(args)
    print(f"evaluated {bundle.provenance['n_images']} images: "
          f"{len(bundle.ground_truth)} GT objects, {len(bundle.predictions)} predictions.")
    if args.out:
        save_bundle(args.out, bundle)
        print(f"[bundle] written to {args.out}")
    else:
        print("(no --out given; bundle not persisted)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
