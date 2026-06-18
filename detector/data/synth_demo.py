"""Generate a tiny SYNTHETIC X-ray-style dataset for a CPU smoke-train.

This is NOT a real weapons dataset — it draws simple gun/knife/scissors
silhouettes on noisy grey backgrounds so the full training pipeline
(train -> best.pt -> ONNX export -> wire -> /v1/detect) can be run end-to-end
on a box with no GPU and no SIXray/PIDray/OPIXray access. The resulting model
learns these toy shapes, not real contraband. For production, wire the real
dataset readers in data/prepare.py and train per detector/README.md.

    python -m detector.data.synth_demo --root /tmp/xray_synth --n-train 120 --n-val 30

Produces YOLO layout matching dataset.yaml:
    <root>/images/{train,val}/*.png
    <root>/labels/{train,val}/*.txt
    <root>/dataset.yaml
"""

from __future__ import annotations

import argparse
import random
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from detector.taxonomy import WEAPON_CLASSES

# We only draw a few of the 6 classes; the rest stay declared but unused.
_DRAWN = {"gun": 0, "knife": 1, "scissors": 2}


def _bg(w: int, h: int, rng: random.Random) -> Image.Image:
    """Noisy grey background ~ an X-ray panel."""
    base = rng.randint(170, 210)
    arr = np.clip(
        np.random.default_rng(rng.randint(0, 2**31)).normal(base, 18, (h, w)),
        0, 255,
    ).astype("uint8")
    return Image.fromarray(arr, mode="L").convert("RGB")


def _draw_gun(d: ImageDraw.ImageDraw, x: int, y: int, s: int, c) -> tuple[int, int, int, int]:
    # L-shaped silhouette: barrel + grip
    d.rectangle([x, y, x + s, y + s // 4], fill=c)                      # barrel
    d.rectangle([x + s // 6, y + s // 4, x + s // 2, y + s], fill=c)    # grip
    return x, y, x + s, y + s


def _draw_knife(d: ImageDraw.ImageDraw, x: int, y: int, s: int, c) -> tuple[int, int, int, int]:
    # thin blade triangle + small handle
    d.polygon([(x, y + s // 8), (x + s, y), (x + s, y + s // 4)], fill=c)
    d.rectangle([x, y + s // 16, x + s // 5, y + s // 5], fill=c)
    return x, y, x + s, y + s // 4


def _draw_scissors(d: ImageDraw.ImageDraw, x: int, y: int, s: int, c) -> tuple[int, int, int, int]:
    d.line([(x, y), (x + s, y + s)], fill=c, width=max(2, s // 14))
    d.line([(x, y + s), (x + s, y)], fill=c, width=max(2, s // 14))
    d.ellipse([x - s // 8, y - s // 8, x + s // 8, y + s // 8], outline=c, width=2)
    return x, y, x + s, y + s


_DRAWERS = {"gun": _draw_gun, "knife": _draw_knife, "scissors": _draw_scissors}


def _one_image(path_img: Path, path_lbl: Path, w: int, h: int, rng: random.Random) -> None:
    img = _bg(w, h, rng)
    d = ImageDraw.Draw(img)
    lines: list[str] = []
    for _ in range(rng.randint(1, 2)):
        name = rng.choice(list(_DRAWN))
        s = rng.randint(min(w, h) // 6, min(w, h) // 3)
        x = rng.randint(5, max(6, w - s - 5))
        y = rng.randint(5, max(6, h - s - 5))
        shade = rng.randint(40, 90)  # weapons read darker (denser metal)
        x1, y1, x2, y2 = _DRAWERS[name](d, x, y, s, (shade, shade, shade))
        x1, x2 = max(0, x1), min(w, x2)
        y1, y2 = max(0, y1), min(h, y2)
        cls_id = WEAPON_CLASSES.index(name)
        cx, cy = (x1 + x2) / 2 / w, (y1 + y2) / 2 / h
        bw, bh = (x2 - x1) / w, (y2 - y1) / h
        lines.append(f"{cls_id} {cx:.6f} {cy:.6f} {bw:.6f} {bh:.6f}")
    img.save(path_img)
    path_lbl.write_text("\n".join(lines) + "\n")


def build(root: Path, n_train: int, n_val: int, w: int, h: int, seed: int) -> Path:
    rng = random.Random(seed)
    for split, n in (("train", n_train), ("val", n_val)):
        (root / "images" / split).mkdir(parents=True, exist_ok=True)
        (root / "labels" / split).mkdir(parents=True, exist_ok=True)
        for i in range(n):
            stem = f"{split}_{i:04d}"
            _one_image(
                root / "images" / split / f"{stem}.png",
                root / "labels" / split / f"{stem}.txt",
                w, h, rng,
            )
    names = "\n".join(f"  {i}: {n}" for i, n in enumerate(WEAPON_CLASSES))
    yaml = (
        f"path: {root}\ntrain: images/train\nval: images/val\ntest: images/val\n\nnames:\n{names}\n"
    )
    yaml_path = root / "dataset.yaml"
    yaml_path.write_text(yaml)
    return yaml_path


def main() -> None:
    ap = argparse.ArgumentParser(description="Synthetic X-ray demo dataset")
    ap.add_argument("--root", default="/tmp/xray_synth")
    ap.add_argument("--n-train", type=int, default=120)
    ap.add_argument("--n-val", type=int, default=30)
    ap.add_argument("--size", type=int, default=320)
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()
    yaml_path = build(Path(args.root), args.n_train, args.n_val, args.size, args.size, args.seed)
    print(f"OK: synthetic dataset at {args.root}")
    print(f"    dataset.yaml -> {yaml_path}")


if __name__ == "__main__":
    main()
