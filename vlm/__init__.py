"""VLM layer — the language/reasoning copilot (Hop 3).

This package owns everything from ``DetectionResult`` (the detector's output)
to ``OperatorVerdict`` (the plain-Uzbek, decision-support text shown to the
operator). It does NOT own detection — the object-detection model is in
``detector/``. This layer only *explains* what the detector found.

Layout:
    prompts.py      Uzbek template system: CLEAR summary, slot-fill prompts,
                    risk-band computation, slot assembly. The fixed Uzbek text
                    lives here, never in model weights.
    guard.py        Language guard: Cyrillic drift, Russian stopwords, English
                    drift, forbidden clearance phrases. Hard gate before any
                    slot enters the wire message.
    backend.py      VLMBackend Protocol + vLLM / Ollama / llama.cpp adapters.
                    All local. No egress.
    generator.py    QwenVLGenerator: the VerdictGenerator seam implementation.
                    Wires prompts + backend + guard + contract validation.
    composition.py  build_vlm_generator(): composition root, mirrors
                    detector/serving/composition.py. Lazy heavy imports.
    finetune/
        dataset.py  LabelEntry JSONL -> Qwen3-VL fine-tuning JSONL.
        train.py    LoRA/QLoRA training script (GPU box; heavy deps not in
                    serving requirements.txt).

Wiring into the API (app/main.py does this on the VLM box):
    XRAY_VLM_ENABLED=1
    XRAY_VLM_BACKEND=vllm
    XRAY_VLM_BASE_URL=http://127.0.0.1:8080
    XRAY_VLM_MODEL=Qwen/Qwen3-VL-4B-Instruct

Off by default → the seam returns 501, never a fabricated verdict.
"""
