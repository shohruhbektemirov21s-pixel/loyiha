"""Continuous auto-labelling collector — camera/scanner stream -> growing dataset.

Loads the zero-shot teacher (YOLO-World + CLIP) ONCE, then keeps pulling frames
from a live source and auto-labelling each into a YOLO dataset that grows on its
own. No manual labels, no external API key.

Sources
-------
  camera   USB webcam via camera.driver (motion-triggered or interval one-shot)
  folder   a hot-folder the scanner/acquisition layer writes images into
           (mirrors the DICOS hot-folder; the production bridge drops frames here)

    # live webcam, 10 frames, 2s apart
    python -m detector.collect_stream --source camera --out /tmp/xray_stream \
        --interval 2 --max-frames 10

    # scanner hot-folder, run until Ctrl-C
    python -m detector.collect_stream --source folder --watch /var/lib/xray/incoming \
        --out /tmp/xray_stream

Every frame becomes one dataset sample (images/ + labels/, train/val split).
Frames with no detections are kept as background negatives (useful training
signal). A sha256 manifest skips duplicates. dataset.yaml is rewritten each
flush so `detector.train.train --data <out>/dataset.yaml` can run at any time.
"""

from __future__ import annotations

import argparse
import hashlib
import signal
import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

_STOP = False


def _install_signals() -> None:
    def _handler(signum, _frame):
        global _STOP
        _STOP = True
        print(f"\n[signal {signum}] finishing current frame and flushing…")
    signal.signal(signal.SIGINT, _handler)
    signal.signal(signal.SIGTERM, _handler)


class _MutableLoader:
    """FrameLoader whose array is swapped per frame (teacher loaded once)."""
    def __init__(self):
        self.array = None
    def load(self, ref):
        return self.array


def _decode_jpeg(jpeg: bytes):
    import cv2
    import numpy as np
    arr = cv2.imdecode(np.frombuffer(jpeg, dtype="uint8"), cv2.IMREAD_COLOR)
    return arr  # BGR


def _camera_frames(interval: float, motion: bool):
    """Yield BGR frames from the USB camera forever (until _STOP)."""
    from camera.composition import build_camera_driver
    driver = build_camera_driver()
    driver.open()
    try:
        while not _STOP:
            try:
                cf = driver.next_frame(timeout_s=interval * 3) if motion else driver.capture_now()
            except Exception as exc:  # noqa: BLE001 — keep the stream alive
                print(f"  camera read skipped: {exc}")
                time.sleep(interval)
                continue
            yield _decode_jpeg(cf.jpeg_bytes)
            if not motion:
                time.sleep(interval)
    finally:
        driver.close()


def _folder_frames(watch: Path, poll: float):
    """Yield BGR frames for each NEW image file dropped into a hot-folder."""
    import cv2
    seen: set[str] = set()
    exts = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff"}
    while not _STOP:
        new = sorted(p for p in watch.glob("*") if p.suffix.lower() in exts and p.name not in seen)
        if not new:
            time.sleep(poll)
            continue
        for p in new:
            if _STOP:
                break
            seen.add(p.name)
            img = cv2.imread(str(p))
            if img is not None:
                yield img


def run(args) -> None:
    import asyncio
    import random

    from contracts.v1 import (
        AcquisitionResult, ImageFrame, ImageModality, ModelProvenance,
        ScanSubject, StorageRef,
    )
    from detector.autolabel import STUDENT_CLASSES, _NAME_TO_ID, _student_class
    from detector.serving.openvocab import (
        ClipReclassifier, OpenVocabDetector, YoloWorldPredictor,
    )

    out = Path(args.out)
    for split in ("train", "val"):
        (out / "images" / split).mkdir(parents=True, exist_ok=True)
        (out / "labels" / split).mkdir(parents=True, exist_ok=True)

    print("teacher: loading YOLO-World + CLIP (once)…")
    predictor = YoloWorldPredictor(prompts=None, conf=args.conf, imgsz=args.imgsz)
    reclf = ClipReclassifier()
    loader = _MutableLoader()
    detector = OpenVocabDetector(
        predictor=predictor, loader=loader,
        provenance=ModelProvenance(name="stream-teacher", version="yw+clip",
                                   weights_sha256=None, runtime="ultralytics"),
        reclassifier=reclf, threshold=args.min_score,
    )

    src = (_camera_frames(args.interval, args.motion) if args.source == "camera"
           else _folder_frames(Path(args.watch), args.interval))

    rng = random.Random(13)
    seen_hashes: set[str] = set()
    stats = {"frames": 0, "labelled": 0, "boxes": 0,
             "per_class": {c: 0 for c in STUDENT_CLASSES}}
    import cv2

    print(f"collecting from {args.source} -> {out}  (Ctrl-C to stop)")
    for bgr in src:
        if _STOP or (args.max_frames and stats["frames"] >= args.max_frames):
            break
        if bgr is None or bgr.size == 0:
            continue
        h, w = bgr.shape[:2]
        digest = hashlib.sha256(bgr.tobytes()).hexdigest()
        if digest in seen_hashes:
            continue
        seen_hashes.add(digest)

        loader.array = bgr
        frame = ImageFrame(frame_id="f0", width_px=w, height_px=h,
                           image=StorageRef(uri="live://frame", media_type="image/png",
                                            sha256="0" * 64, size_bytes=bgr.nbytes),
                           view_label="stream")
        acq = AcquisitionResult(scan_id=uuid4(), scanner_id=args.source, lane_id="lane-1",
                                operator_id="auto", subject=ScanSubject.BAGGAGE,
                                modality=ImageModality.SINGLE_ENERGY,
                                captured_at=datetime.now(timezone.utc),
                                emitted_at=datetime.now(timezone.utc), frames=[frame])
        result = asyncio.run(detector.detect(acq))

        lines: list[str] = []
        for d in result.detections:
            cls = _student_class(d.native_label, d.category)
            if cls is None:
                continue
            b = d.box
            cx, cy = (b.x + b.width / 2) / w, (b.y + b.height / 2) / h
            lines.append(f"{_NAME_TO_ID[cls]} {cx:.6f} {cy:.6f} {b.width/w:.6f} {b.height/h:.6f}")
            stats["per_class"][cls] += 1
            stats["boxes"] += 1

        split = "val" if rng.random() < args.val_frac else "train"
        stem = f"stream_{stats['frames']:06d}"
        cv2.imwrite(str(out / "images" / split / f"{stem}.jpg"), bgr)
        (out / "labels" / split / f"{stem}.txt").write_text("\n".join(lines) + ("\n" if lines else ""))
        stats["frames"] += 1
        if lines:
            stats["labelled"] += 1
        tag = ", ".join(f"{d.category.value}:{d.score:.2f}" for d in result.detections) or "background"
        print(f"  frame {stats['frames']}: {len(lines)} box -> {split}  [{tag}]")

        if stats["frames"] % args.flush_every == 0:
            _write_yaml(out)

    _write_yaml(out)
    print("=" * 56)
    print(f"stopped. frames={stats['frames']} labelled={stats['labelled']} "
          f"boxes={stats['boxes']} per-class={stats['per_class']}")
    print(f"dataset.yaml -> {out/'dataset.yaml'}  (retrain: python -m detector.train.train --data {out}/dataset.yaml)")


def _write_yaml(out: Path) -> None:
    from detector.autolabel import STUDENT_CLASSES
    names = "\n".join(f"  {i}: {n}" for i, n in enumerate(STUDENT_CLASSES))
    (out / "dataset.yaml").write_text(
        f"path: {out}\ntrain: images/train\nval: images/val\ntest: images/val\n\nnames:\n{names}\n"
    )


def main() -> None:
    ap = argparse.ArgumentParser(description="Continuous camera/scanner auto-label collector")
    ap.add_argument("--source", choices=["camera", "folder"], default="camera")
    ap.add_argument("--watch", default="/var/lib/xray/incoming", help="folder source hot-dir")
    ap.add_argument("--out", default="/tmp/xray_stream")
    ap.add_argument("--interval", type=float, default=2.0, help="seconds between captures / poll")
    ap.add_argument("--motion", action="store_true", help="camera: motion-trigger instead of interval")
    ap.add_argument("--max-frames", type=int, default=0, help="0 = run until stopped")
    ap.add_argument("--val-frac", type=float, default=0.2)
    ap.add_argument("--min-score", type=float, default=0.25)
    ap.add_argument("--imgsz", type=int, default=640)
    ap.add_argument("--conf", type=float, default=0.05)
    ap.add_argument("--flush-every", type=int, default=10)
    args = ap.parse_args()
    _install_signals()
    run(args)


if __name__ == "__main__":
    main()
