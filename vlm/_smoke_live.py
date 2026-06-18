"""Live VLM smoke test — runs the real Qwen3-VL-4B backend end-to-end.

Builds a VerdictRequest with one firearm detection, runs it through the
production QwenVLGenerator against whatever backend XRAY_VLM_* points at
(Ollama by default), and prints the Uzbek operator verdict.

    XRAY_VLM_BACKEND=ollama XRAY_VLM_MODEL=qwen3-vl:4b \
        python -m vlm._smoke_live

This is the proof that the VLM seam is wired to a real model, not a stub.
"""

from __future__ import annotations

import asyncio
import os
import sys

from tests.fixtures.builders import make_detection_result, make_verdict_request
from vlm.composition import VLMConfig, build_vlm_generator


async def _run() -> int:
    cfg = VLMConfig(
        backend_type=os.environ.get("XRAY_VLM_BACKEND", "ollama"),
        base_url=os.environ.get("XRAY_VLM_BASE_URL", "http://127.0.0.1:11434"),
        model=os.environ.get("XRAY_VLM_MODEL", "qwen3-vl:4b"),
        timeout_s=float(os.environ.get("XRAY_VLM_TIMEOUT_S", "600")),
        verify=False,  # we probe by actually generating below
    )
    print(f"VLM backend={cfg.backend_type} model={cfg.model} url={cfg.base_url}")
    generator = build_vlm_generator(cfg)

    request = make_verdict_request(make_detection_result())
    print(f"scan_id={request.scan_id}  detections={len(request.detection.detections)}")
    print("Generating verdict (first call loads the model — may take a while on CPU)…\n")

    verdict = await generator.generate(request)

    print("=" * 60)
    print(f"overall_risk : {verdict.overall_risk.value}")
    print(f"summary_uz   : {verdict.summary_uz}")
    for dv in verdict.per_detection:
        print(f"  - [{dv.category.value}] conf={dv.confidence:.2f}")
        print(f"    {dv.rationale_uz}")
    print(f"model        : {verdict.model.name} v{verdict.model.version} ({verdict.model.runtime})")
    print(f"decision_support_only={verdict.decision_support_only}")
    print("=" * 60)
    print("\nOK: Qwen3-VL verdict generated, guard passed, contract valid.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(_run()))
