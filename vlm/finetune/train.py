"""LoRA / QLoRA fine-tuning script for Qwen3-VL on Uzbek verdict examples.

GPU box only. Requirements (install separately on the training box —
NOT in the serving requirements.txt):
    torch>=2.3
    transformers>=4.51
    peft>=0.11
    trl>=0.8
    bitsandbytes>=0.43    # QLoRA 4-bit
    accelerate>=0.30
    qwen_vl_utils         # Qwen3-VL chat template helper

Usage:
    python -m vlm.finetune.train \\
        --data      /data/finetune/qwen3vl_uz.jsonl \\
        --model     Qwen/Qwen3-VL-4B-Instruct \\
        --output    /models/adapters/qwen3vl-uz-v1 \\
        --epochs    3 \\
        --qlora          # add for 4B; omit for 8B/32B with enough VRAM

Serve the adapter:
    vllm serve Qwen/Qwen3-VL-4B-Instruct \\
        --lora-modules uzbek-customs=/models/adapters/qwen3vl-uz-v1 \\
        --enable-lora \\
        --disable-log-requests

LoRA choices rationale:
  - r=16, alpha=32: standard starting point for task-specific adaptation.
  - Target modules: Q/K/V/O projections + MLP gate/up/down; covers the
    language head where Uzbek drift occurs without touching the vision tower.
  - Vision tower is frozen: we are not improving visual understanding, only
    the Uzbek slot-filling language output.
  - QLoRA (4-bit NF4) for the 4B model: fits in 12 GB VRAM with room for
    the KV cache. For 8B/32B, use full LoRA (BF16 base).
  - Max sequence length 2048: covers SYSTEM + user prompt + crop token budget.

Data volume expectations (push back on anything below these):
  - 100 examples: enough to reduce Cyrillic drift noticeably.
  - 300 examples: enough for consistent Uzbek Latin slot filling.
  - 1 000+ examples: full domain adaptation for all 8 threat categories.
  Training on < 50 examples will likely overfit to one operator's writing style.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import sys
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger("xray.vlm.finetune.train")


@dataclass
class TrainConfig:
    data_path: str
    model_name: str
    output_dir: str
    epochs: int = 3
    per_device_batch: int = 2
    grad_accumulation: int = 4
    lr: float = 2e-4
    lora_r: int = 16
    lora_alpha: int = 32
    lora_dropout: float = 0.05
    max_seq_len: int = 2048
    qlora: bool = False                 # 4-bit NF4 quantization
    bf16: bool = True
    warmup_ratio: float = 0.05
    save_steps: int = 50
    logging_steps: int = 10
    seed: int = 42
    # Air-gap flags: disable all telemetry and HF calls
    hf_offline: bool = True


# LoRA target modules for Qwen3-VL (language head only; vision tower frozen).
_LORA_TARGET_MODULES = [
    "q_proj", "k_proj", "v_proj", "o_proj",
    "gate_proj", "up_proj", "down_proj",
]


def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _count_examples(data_path: str) -> int:
    with open(data_path, "r", encoding="utf-8") as f:
        return sum(1 for line in f if line.strip())


def run_training(cfg: TrainConfig) -> None:
    """Execute the full LoRA fine-tuning run. All heavy imports are here."""
    # Air-gap: disable all outbound traffic before any HF import.
    if cfg.hf_offline:
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["TRANSFORMERS_OFFLINE"] = "1"
        os.environ["WANDB_DISABLED"] = "true"
        os.environ["WANDB_MODE"] = "disabled"
        os.environ["DISABLE_MLFLOW_INTEGRATION"] = "1"

    # Heavy imports — only reach here on the GPU training box.
    import torch
    from datasets import Dataset
    from peft import LoraConfig, TaskType, get_peft_model, prepare_model_for_kbit_training
    from transformers import AutoProcessor, AutoTokenizer, BitsAndBytesConfig, TrainingArguments
    from trl import SFTTrainer

    n_examples = _count_examples(cfg.data_path)
    log.info("Training on %d examples from %s", n_examples, cfg.data_path)
    if n_examples < 50:
        log.warning(
            "Only %d examples — will likely overfit. "
            "Collect at least 100 before fine-tuning.", n_examples
        )

    # -- Load model --------------------------------------------------------
    load_kwargs = {
        "trust_remote_code": True,
        "torch_dtype": torch.bfloat16 if cfg.bf16 else torch.float16,
        "device_map": "auto",
    }
    if cfg.qlora:
        bnb_config = BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_quant_type="nf4",
            bnb_4bit_compute_dtype=torch.bfloat16,
            bnb_4bit_use_double_quant=True,
        )
        load_kwargs["quantization_config"] = bnb_config
        log.info("QLoRA: 4-bit NF4 quantization enabled.")

    # Import Qwen3-VL-specific model class.
    try:
        from transformers import Qwen2_5_VLForConditionalGeneration as QwenVLModel
    except ImportError:
        from transformers import AutoModelForCausalLM as QwenVLModel  # type: ignore

    log.info("Loading model: %s", cfg.model_name)
    model = QwenVLModel.from_pretrained(cfg.model_name, **load_kwargs)
    processor = AutoProcessor.from_pretrained(cfg.model_name, trust_remote_code=True)

    if cfg.qlora:
        model = prepare_model_for_kbit_training(model)

    # Freeze the vision tower: we only adapt the language head.
    for name, param in model.named_parameters():
        if "visual" in name or "vision" in name:
            param.requires_grad = False
    log.info("Vision tower frozen; training language head only.")

    # -- LoRA config -------------------------------------------------------
    peft_cfg = LoraConfig(
        task_type=TaskType.CAUSAL_LM,
        r=cfg.lora_r,
        lora_alpha=cfg.lora_alpha,
        lora_dropout=cfg.lora_dropout,
        target_modules=_LORA_TARGET_MODULES,
        bias="none",
    )
    model = get_peft_model(model, peft_cfg)
    model.print_trainable_parameters()

    # -- Dataset -----------------------------------------------------------
    raw: list[dict] = []
    with open(cfg.data_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                raw.append(json.loads(line))

    def _format_example(row: dict) -> dict:
        """Apply the Qwen3-VL chat template to produce input_ids + labels."""
        msgs = row["messages"]
        text = processor.apply_chat_template(
            msgs, tokenize=False, add_generation_prompt=False
        )
        return {"text": text}

    dataset = Dataset.from_list(raw).map(_format_example, remove_columns=["messages", "metadata"])

    # -- Training arguments ------------------------------------------------
    training_args = TrainingArguments(
        output_dir=cfg.output_dir,
        num_train_epochs=cfg.epochs,
        per_device_train_batch_size=cfg.per_device_batch,
        gradient_accumulation_steps=cfg.grad_accumulation,
        learning_rate=cfg.lr,
        bf16=cfg.bf16,
        fp16=not cfg.bf16,
        logging_steps=cfg.logging_steps,
        save_steps=cfg.save_steps,
        warmup_ratio=cfg.warmup_ratio,
        seed=cfg.seed,
        dataloader_num_workers=0,   # air-gap: no parallel downloads
        report_to="none",            # no wandb / mlflow
        remove_unused_columns=False,
    )

    trainer = SFTTrainer(
        model=model,
        args=training_args,
        train_dataset=dataset,
        tokenizer=processor.tokenizer,
        max_seq_length=cfg.max_seq_len,
        dataset_text_field="text",
    )

    log.info("Training starts — %d epochs × %d examples.", cfg.epochs, n_examples)
    trainer.train()

    # -- Save adapter + print sha256 for provenance ----------------------
    adapter_path = Path(cfg.output_dir)
    model.save_pretrained(adapter_path)
    processor.save_pretrained(adapter_path)

    # Hash the adapter_model.safetensors for provenance logging.
    for candidate in ["adapter_model.safetensors", "adapter_model.bin"]:
        p = adapter_path / candidate
        if p.exists():
            sha = _sha256_file(str(p))
            log.info("Adapter saved: %s", adapter_path)
            log.info("Adapter sha256: %s  ← record in ModelProvenance for serving.", sha)
            (adapter_path / "adapter_sha256.txt").write_text(sha)
            break

    log.info("Fine-tuning complete. Serve with: "
             "vllm serve %s --lora-modules uz-customs=%s --enable-lora",
             cfg.model_name, adapter_path)


def main() -> None:
    parser = argparse.ArgumentParser(description="QLoRA fine-tune Qwen3-VL on Uzbek verdict examples.")
    parser.add_argument("--data",    required=True, help="Fine-tuning JSONL (from finetune/dataset.py)")
    parser.add_argument("--model",   default="Qwen/Qwen3-VL-4B-Instruct")
    parser.add_argument("--output",  required=True, help="Adapter output directory")
    parser.add_argument("--epochs",  type=int, default=3)
    parser.add_argument("--batch",   type=int, default=2)
    parser.add_argument("--lr",      type=float, default=2e-4)
    parser.add_argument("--lora-r",  type=int, default=16)
    parser.add_argument("--qlora",   action="store_true", help="Enable 4-bit NF4 QLoRA")
    parser.add_argument("--no-bf16", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    cfg = TrainConfig(
        data_path=args.data,
        model_name=args.model,
        output_dir=args.output,
        epochs=args.epochs,
        per_device_batch=args.batch,
        lr=args.lr,
        lora_r=args.lora_r,
        qlora=args.qlora,
        bf16=not args.no_bf16,
    )
    run_training(cfg)


if __name__ == "__main__":
    main()
