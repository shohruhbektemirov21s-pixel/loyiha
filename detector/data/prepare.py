"""Remap the public X-ray sets onto the unified weapons schema (YOLO format).

The three sources disagree on label names, granularity, and annotation format:

    SIXray    PASCAL-VOC XML, classes: Gun/Knife/Wrench/Pliers/Scissors,
              ~1.05M images but mostly negative — keep a sane positive:negative
              ratio so the model sees enough background without drowning.
    PIDray    COCO JSON, 12 classes; we keep gun/knife/scissors/wrench/pliers/
              hammer and DROP the rest (powerbank, lighter, ...). Its hard/
              hidden eval subsets are routed to the TEST split on purpose.
    OPIXray   VOC-style, 5 knife sub-types -> all collapse to 'knife'; carries
              occlusion level OL1/OL2/OL3 (route OL2/OL3 to test for the
              occlusion-stratified recall curve).

This module does NOT hardcode each parser (the on-disk layouts differ per
download); it provides the *normalization spine* every parser must funnel
through — ``remap_label`` and ``write_yolo_label`` — plus an integrity assert
that the class index order matches the contract-side taxonomy. Wire the
per-dataset readers (VOC/COCO -> list[(label, box_xyxy)]) on the data box and
push each box through these helpers.

Box convention out: YOLO normalized ``cls cx cy w h``, one per line.
"""

from __future__ import annotations

import logging
from pathlib import Path

from detector.taxonomy import DROPPED_NATIVE_LABELS, WEAPON_CLASSES, normalize_native

log = logging.getLogger("xray.detector.prepare")

# Authoritative index map — the single place name<->id is decided.
CLASS_TO_ID: dict[str, int] = {name: i for i, name in enumerate(WEAPON_CLASSES)}


def assert_consistent_with_dataset_yaml(yaml_names: dict[int, str]) -> None:
    """Guard: dataset.yaml ``names`` must equal taxonomy order, exactly."""
    expected = {i: n for i, n in enumerate(WEAPON_CLASSES)}
    if yaml_names != expected:
        raise ValueError(
            f"dataset.yaml names drifted from taxonomy.WEAPON_CLASSES.\n"
            f"  yaml:     {yaml_names}\n  taxonomy: {expected}"
        )


def remap_label(raw_label: str) -> int | None:
    """Raw source label -> unified class id, or None to DROP this box.

    None means 'do not write this annotation' — used for the intentionally
    excluded classes (powerbank/lighter/...). Critically, a dropped box is
    removed from the label file but the *image is still usable as background*
    only if it has no kept objects; prepare logic must not turn a dropped box
    into a negative for a class that is actually present elsewhere in frame.
    """
    low = raw_label.strip().lower()
    if low in DROPPED_NATIVE_LABELS:
        return None
    unified = normalize_native(low)
    if unified is None:
        log.warning("unknown source label %r — dropped (review prepare mapping)", raw_label)
        return None
    return CLASS_TO_ID[unified]


def to_yolo_line(cls_id: int, box_xyxy: tuple[float, float, float, float],
                 img_w: int, img_h: int) -> str:
    """(x1,y1,x2,y2) pixels -> 'cls cx cy w h' normalized, clamped to [0,1]."""
    x1, y1, x2, y2 = box_xyxy
    x1, x2 = sorted((max(0.0, x1), min(float(img_w), x2)))
    y1, y2 = sorted((max(0.0, y1), min(float(img_h), y2)))
    cx = (x1 + x2) / 2 / img_w
    cy = (y1 + y2) / 2 / img_h
    w = (x2 - x1) / img_w
    h = (y2 - y1) / img_h
    return f"{cls_id} {cx:.6f} {cy:.6f} {w:.6f} {h:.6f}"


def write_yolo_label(dst: Path, lines: list[str]) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text("\n".join(lines) + ("\n" if lines else ""))


# --- per-dataset readers: implement on the data box -------------------------
# Each returns: list of (raw_label, (x1,y1,x2,y2)) plus (img_w, img_h).
# Kept as stubs because the on-disk layout depends on the specific download.
def read_voc_annotation(xml_path: Path):  # SIXray, OPIXray
    raise NotImplementedError("Wire VOC XML parsing on the data box (xml.etree).")


def read_coco_annotations(json_path: Path):  # PIDray
    raise NotImplementedError("Wire COCO JSON parsing on the data box (pycocotools/json).")


if __name__ == "__main__":
    # Sanity: the index spine is internally consistent and matches dataset.yaml.
    import yaml  # type: ignore

    cfg = yaml.safe_load((Path(__file__).parent / "dataset.yaml").read_text())
    assert_consistent_with_dataset_yaml({int(k): v for k, v in cfg["names"].items()})
    print("OK: taxonomy <-> dataset.yaml class index map is consistent.")
    print("    CLASS_TO_ID =", CLASS_TO_ID)
