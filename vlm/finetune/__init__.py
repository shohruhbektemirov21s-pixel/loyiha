"""LoRA/QLoRA domain fine-tuning for Qwen3-VL on Uzbek verdict examples.

GPU-box only. The modules here have heavy dependencies (transformers, peft,
trl, bitsandbytes) that are NOT listed in requirements.txt and must be
installed separately on the training box. They are never imported at API
startup — all imports are deferred inside functions.

Workflow:
  1. Collect labeled examples via the active-learning loop (datalayer).
  2. Run ``dataset.py`` to convert OperatorFeedback + OperatorVerdict pairs
     into a Qwen3-VL fine-tuning JSONL file.
  3. Run ``train.py`` to fine-tune with LoRA/QLoRA on that JSONL.
  4. The resulting adapter is served by vLLM (``--lora-modules``).

See ``train.py`` for the full argument list and GPU box requirements.
"""
