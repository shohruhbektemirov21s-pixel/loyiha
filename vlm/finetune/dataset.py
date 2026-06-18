"""Convert operator feedback + verdicts into Qwen3-VL fine-tuning examples.

Each example is one (scan, detection, operator-corrected verdict) triple
written as a Qwen3-VL chat message in OpenAI JSONL format, ready for
``trl.SFTTrainer`` / ``unsloth``.

Input sources (two complementary signals):
  A. ``OperatorVerdict`` confirmed by the operator (CONFIRMED judgement in
     the matching ``OperatorFeedback``) — the model was right, reinforce it.
  B. ``OperatorFeedback.missed`` annotations — the model was wrong; teach it
     the correct category and box region.
  C. ``DetectionJudgement.RECLASSIFIED`` — right box, wrong category; teach
     the correct label.

Each example format (Qwen3-VL chat template, OpenAI-compatible):
  {
    "messages": [
      {"role": "system", "content": "<SYSTEM_PROMPT>"},
      {"role": "user",   "content": [
          {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,..."}},
          {"type": "text", "text": "<slot_prompt>"}
      ]},
      {"role": "assistant", "content": "TAVSIF: ...\nSABAB: ..."}
    ],
    "metadata": {"scan_id": "...", "category": "...", "source": "..."}
  }

When no crop image is available, the image block is omitted (text-only).

Run:
    python -m vlm.finetune.dataset \\
        --feedback-jsonl  /data/labels/active_learning.jsonl \\
        --store-root      /data/store \\
        --out             /data/finetune/qwen3vl_uz.jsonl \\
        --min-examples    50
"""

from __future__ import annotations

import argparse
import base64
import json
import logging
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterator

log = logging.getLogger("xray.vlm.finetune.dataset")

# ---------------------------------------------------------------------------
# Example dataclass (in-memory; serialized to JSONL)
# ---------------------------------------------------------------------------
@dataclass
class FinetuneExample:
    messages: list[dict]
    metadata: dict


def example_to_jsonl(ex: FinetuneExample) -> str:
    return json.dumps({"messages": ex.messages, "metadata": ex.metadata}, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Build one example from a label entry + optional crop bytes
# ---------------------------------------------------------------------------
def _build_messages(
    category_value: str,
    frame_w: int,
    frame_h: int,
    box: tuple[int, int, int, int] | None,
    gold_tavsif: str,
    gold_sabab: str,
    crop_bytes: bytes | None,
) -> list[dict]:
    """Assemble the messages list for one fine-tuning example."""
    from vlm.prompts import SYSTEM_PROMPT, CATEGORY_UZ
    from contracts.v1 import ThreatCategory

    try:
        cat = ThreatCategory(category_value)
        cat_uz = CATEGORY_UZ[cat]
    except (ValueError, KeyError):
        cat_uz = category_value

    conf_pct = 90  # gold examples assume high confidence
    if box:
        x, y, w, h = box
        loc_text = f"({x},{y}), oʻlcham {w}×{h} px"
    else:
        loc_text = "noaniq joylashuv"

    if crop_bytes:
        b64 = base64.b64encode(crop_bytes).decode("ascii")
        image_url = f"data:image/jpeg;base64,{b64}"
        user_content: list[dict] = [
            {"type": "image_url", "image_url": {"url": image_url}},
            {"type": "text", "text": (
                f"Detektor quyidagini aniqladi:\n"
                f"  Toifa: {cat_uz}\n"
                f"  Ishonch: {conf_pct}%\n"
                f"  Joylashuv: {loc_text}\n"
                f"\n"
                f"Yuqoridagi rasm — aniqlanagan hududning kesib olingan tasvirI.\n"
                f"\n"
                f"Quyidagi maydonlarni FAQAT OZBEK TILIDA (lotin yozuvi) toʻldiring:\n"
                f"\n"
                f"TAVSIF: [rasmda koʻrinayotgan narsani 1–3 gap bilan tasvirlab bering]\n"
                f"SABAB: [nima uchun bu hudud diqqatni tortishini 1–2 gapda tushuntiring]"
            )},
        ]
    else:
        user_content = (
            f"Detektor quyidagini aniqladi:\n"
            f"  Toifa: {cat_uz}\n"
            f"  Ishonch: {conf_pct}%\n"
            f"  Joylashuv: {loc_text}\n"
            f"  Rasm mavjud emas. Kadr oʻlchami: {frame_w}×{frame_h} px.\n"
            f"\n"
            f"TAVSIF: [...]\nSABAB: [...]"
        )

    assistant_content = f"TAVSIF: {gold_tavsif}\nSABAB: {gold_sabab}"

    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
        {"role": "assistant", "content": assistant_content},
    ]


# ---------------------------------------------------------------------------
# Converter: LabelEntry JSONL → FinetuneExample stream
# ---------------------------------------------------------------------------
def iter_examples_from_label_jsonl(
    label_jsonl_path: Path,
    store=None,           # SecureImageStore | None
) -> Iterator[FinetuneExample]:
    """Yield fine-tuning examples from a datalayer LabelEntry JSONL file.

    Only entries with source in {OPERATOR_CONFIRMED, OPERATOR_RECLASSIFIED,
    OPERATOR_MISSED} and status REVIEWED are converted — other sources do not
    carry the information needed to build a complete assistant turn.
    """
    POSITIVE_SOURCES = {"operator_confirmed", "operator_reclassified", "operator_missed"}

    with label_jsonl_path.open("r", encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError as exc:
                log.warning("Line %d: JSON decode error: %s", lineno, exc)
                continue

            if obj.get("source") not in POSITIVE_SOURCES:
                continue
            if obj.get("status") not in ("reviewed", "consumed"):
                continue

            category = obj.get("category", "unknown")
            scan_sha256 = obj.get("scan_sha256", "")
            frame_id = obj.get("frame_id", "")
            box_x = obj.get("box_x")
            box_y = obj.get("box_y")
            box_w = obj.get("box_w")
            box_h = obj.get("box_h")
            box = (box_x, box_y, box_w, box_h) if all(
                v is not None for v in (box_x, box_y, box_w, box_h)
            ) else None
            operator_note = obj.get("operator_note") or ""

            # Build gold slots from the operator note when available;
            # otherwise use a generic confirmation.
            gold_tavsif = operator_note[:200] if operator_note else f"{category} toifasiga oid predmet aniqlandi."
            gold_sabab = "Operator tomonidan tasdiqlangan shubhali predmet."

            # Try to load the crop from the store (optional).
            crop_bytes: bytes | None = None
            if store is not None and scan_sha256:
                try:
                    from contracts.v1 import StorageRef
                    from datalayer.storage import SecureImageStore
                    # Reconstruct a minimal StorageRef for the frame blob.
                    # The store layout: <root>/<sha256[:2]>/<sha256>.blob
                    blob_path = Path(store._root) / scan_sha256[:2] / f"{scan_sha256}.blob"
                    if blob_path.exists():
                        ref = StorageRef(
                            uri=blob_path.as_uri(),
                            media_type="image/tiff",
                            sha256=scan_sha256,
                            size_bytes=blob_path.stat().st_size,
                        )
                        crop_bytes = store.get(ref)
                except Exception as exc:
                    log.debug("Could not load crop for sha=%s: %s", scan_sha256[:8], exc)

            messages = _build_messages(
                category_value=category,
                frame_w=640,   # unknown at this stage; use a sane default
                frame_h=480,
                box=box,
                gold_tavsif=gold_tavsif,
                gold_sabab=gold_sabab,
                crop_bytes=crop_bytes,
            )

            yield FinetuneExample(
                messages=messages,
                metadata={
                    "scan_sha256": scan_sha256,
                    "frame_id": frame_id,
                    "category": category,
                    "source": obj.get("source"),
                    "label_id": obj.get("label_id"),
                },
            )


def write_jsonl(examples: list[FinetuneExample], out_path: Path) -> int:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as fh:
        for ex in examples:
            fh.write(example_to_jsonl(ex) + "\n")
    return len(examples)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build Qwen3-VL fine-tuning JSONL from label store entries."
    )
    parser.add_argument("--feedback-jsonl", required=True, help="Path to active_learning.jsonl")
    parser.add_argument("--store-root", default=None, help="SecureImageStore root (optional; for crops)")
    parser.add_argument("--out", required=True, help="Output JSONL path")
    parser.add_argument("--min-examples", type=int, default=50, help="Abort if fewer than N examples")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    store = None
    if args.store_root:
        from datalayer.storage import DevPassthroughEncryptor, SecureImageStore
        store = SecureImageStore(args.store_root, DevPassthroughEncryptor())

    examples = list(iter_examples_from_label_jsonl(Path(args.feedback_jsonl), store))
    if len(examples) < args.min_examples:
        log.error(
            "Only %d examples found (min %d). Collect more labeled data before fine-tuning.",
            len(examples),
            args.min_examples,
        )
        sys.exit(1)

    n = write_jsonl(examples, Path(args.out))
    log.info("Wrote %d fine-tuning examples to %s", n, args.out)


if __name__ == "__main__":
    main()
