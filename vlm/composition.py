"""Composition root for the VLM serving seam.

This is the one place that assembles a concrete ``QwenVLGenerator`` from a
flat config and hands it to the FastAPI app (``app.main`` wires it in
the lifespan via ``dependency_overrides``).

Pattern mirrors ``detector/serving/composition.py`` exactly:
* Safe to import on any box — no heavy deps at module level.
* Heavy deps (httpx, etc.) are already in requirements; no new imports.
* The actual backend construction is deferred to ``build_vlm_generator``,
  which only runs when ``XRAY_VLM_ENABLED=1`` (the VLM serving box).
* Fail-closed: misconfiguration raises at startup, never degrades silently.

Deployment guide (GPU / VLM box):
  1. Start the VLM server (see vlm/backend.py for flags + env vars).
  2. Set env: XRAY_VLM_ENABLED=1, XRAY_VLM_BACKEND=vllm,
              XRAY_VLM_BASE_URL=http://127.0.0.1:8080,
              XRAY_VLM_MODEL=Qwen/Qwen3-VL-4B-Instruct
  3. Optionally: XRAY_VLM_STORE_ROOT + XRAY_STORE_KEY for crop loading.
  4. Start the API: uvicorn app.main:app
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

from contracts.v1 import ModelProvenance
from vlm.backend import VLMBackend, build_backend
from vlm.guard import LanguageGuard
from vlm.generator import QwenVLGenerator

log = logging.getLogger("xray.vlm.composition")

# Model size -> reasonable token budget for the two narrow slots.
_MAX_TOKENS_BY_MODEL_SIZE: dict[str, int] = {
    "4b": 300,
    "8b": 350,
    "32b": 400,
}
_DEFAULT_MAX_TOKENS = 300


@dataclass(frozen=True)
class VLMConfig:
    """Flat, serializable view of the VLM serving config (from Settings)."""

    backend_type: str = "vllm"                         # vllm | ollama | llamacpp | echo
    base_url: str = "http://127.0.0.1:8080"
    model: str = "Qwen/Qwen3-VL-4B-Instruct"
    timeout_s: float = 60.0
    temperature: float = 0.10
    max_tokens: int = _DEFAULT_MAX_TOKENS
    name: str = "qwen3-vl"
    version: str = "4b"
    store_root: str | None = None                      # path to SecureImageStore for crop loading
    store_key_env: str = "XRAY_STORE_KEY"              # env var holding the AES key
    verify: bool = True                                # whether to ping the backend on startup
    describe: bool = True                              # False => deterministic Uzbek, no model call (CPU)


def _infer_max_tokens(model: str, configured: int) -> int:
    if configured != _DEFAULT_MAX_TOKENS:
        return configured
    for size_tag, tokens in _MAX_TOKENS_BY_MODEL_SIZE.items():
        if size_tag in model.lower():
            return tokens
    return _DEFAULT_MAX_TOKENS


def _ping_backend(backend: VLMBackend, timeout_s: float) -> None:
    """Synchronously probe the backend with a trivial prompt. Fail-closed."""
    import asyncio

    async def _probe() -> None:
        await backend.generate(
            [{"role": "user", "content": "salom"}],
            temperature=0.0,
            max_tokens=5,
        )

    try:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            in_loop = False
        else:
            in_loop = True

        if in_loop:
            # Called from inside a running event loop (e.g. the FastAPI lifespan):
            # run_until_complete() would raise "loop is already running". Probe in a
            # dedicated thread with its own loop instead.
            import concurrent.futures

            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
                ex.submit(lambda: asyncio.run(_probe())).result(timeout=timeout_s)
        else:
            asyncio.run(_probe())
    except Exception as exc:
        raise RuntimeError(
            f"VLM backend probe failed: {exc}. "
            f"Is the VLM server running? Check XRAY_VLM_BASE_URL."
        ) from exc


def _build_store(cfg: VLMConfig):
    """Build a SecureImageStore for crop loading, or None if not configured."""
    if not cfg.store_root:
        log.info("VLM: store_root not set — crop images will not be loaded (text-only prompts).")
        return None

    from datalayer.storage import AesGcmEncryptor, DevPassthroughEncryptor, SecureImageStore

    key_b64 = os.environ.get(cfg.store_key_env)
    if key_b64:
        encryptor = AesGcmEncryptor.from_env(cfg.store_key_env)
        log.info("VLM: SecureImageStore wired with AES-GCM encryption at %s.", cfg.store_root)
    else:
        log.warning(
            "VLM: %s not set — SecureImageStore using DevPassthroughEncryptor. "
            "Dev/test only; never do this on a box holding real scans.",
            cfg.store_key_env,
        )
        encryptor = DevPassthroughEncryptor()

    return SecureImageStore(cfg.store_root, encryptor)


def build_vlm_generator(cfg: VLMConfig) -> QwenVLGenerator:
    """Assemble the production ``QwenVLGenerator``.

    Fail-closed: any problem here raises so the serving box aborts startup
    rather than booting with a silent 501.
    """
    max_tokens = _infer_max_tokens(cfg.model, cfg.max_tokens)

    backend: VLMBackend = build_backend(
        cfg.backend_type,
        base_url=cfg.base_url,
        model=cfg.model,
        timeout_s=cfg.timeout_s,
    )

    # Skip the probe when descriptions are off — the backend is never called.
    if cfg.verify and cfg.describe and cfg.backend_type != "echo":
        log.info("VLM: probing backend at %s ...", cfg.base_url)
        _ping_backend(backend, cfg.timeout_s)
        log.info("VLM: backend probe OK.")

    guard = LanguageGuard()
    store = _build_store(cfg)

    provenance = ModelProvenance(
        name=cfg.name,
        version=cfg.version,
        weights_sha256=None,   # served model; no local weights file to hash
        runtime=cfg.backend_type,
    )

    generator = QwenVLGenerator(
        backend=backend,
        guard=guard,
        provenance=provenance,
        store=store,
        max_tokens=max_tokens,
        temperature=cfg.temperature,
        describe=cfg.describe,
    )

    log.info(
        "VLM wired: backend=%s model=%s url=%s max_tokens=%d temp=%.2f crops=%s describe=%s",
        cfg.backend_type,
        cfg.model,
        cfg.base_url,
        max_tokens,
        cfg.temperature,
        "yes" if store else "no",
        cfg.describe,
    )
    return generator


__all__ = ["VLMConfig", "build_vlm_generator"]
