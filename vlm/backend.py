"""VLM inference backends — the transport seam between prompts and completions.

Three backends, one Protocol. All are local-only; none phone home.

  vLLMBackend    — production. Calls the vLLM OpenAI-compatible REST API
                   running on the same machine (http://127.0.0.1:VLLM_PORT).
                   Telemetry disabled via env: VLLM_DISABLE_USAGE_STATS=1.

  OllamaBackend  — prototyping. Calls a local Ollama server
                   (http://127.0.0.1:11434). OLLAMA_NO_ANALYTICS=1.

  LlamaCppBackend — edge/offline. Calls llama-cpp-python's OpenAI-compatible
                   server or a subprocess. No network, no process escape.

All three expose the same async interface:
    generate(messages, *, temperature, max_tokens) -> str

``messages`` follows the OpenAI chat format: a list of
``{"role": "user"|"system"|"assistant", "content": str | list}``.
For vision inputs, ``content`` is a list of
``{"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,..."}}``
and ``{"type": "text", "text": "..."}``.

The ``httpx`` client is already a runtime dependency (fastapi TestClient).
No new packages are required for vLLM or Ollama backends.
"""

from __future__ import annotations

import base64
import json
import logging
import threading
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

log = logging.getLogger("xray.vlm.backend")

# ---------------------------------------------------------------------------
# Protocol (the seam every backend must satisfy)
# ---------------------------------------------------------------------------
Message = dict[str, Any]   # {"role": ..., "content": ...}


@runtime_checkable
class VLMBackend(Protocol):
    """One async text-generation call. Returns the assistant turn text."""

    async def generate(
        self,
        messages: list[Message],
        *,
        temperature: float = 0.1,
        max_tokens: int = 512,
    ) -> str: ...


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------
def encode_image_b64(image_bytes: bytes, media_type: str = "image/jpeg") -> str:
    """Encode raw image bytes as a data-URI for the vision content block."""
    b64 = base64.b64encode(image_bytes).decode("ascii")
    return f"data:{media_type};base64,{b64}"


_THINK_RE = None

# A pre-closed reasoning block. Prefilled as the *start* of the assistant turn so
# a thinking model (Qwen3-VL) continues straight into its final answer instead of
# burning the whole token budget inside <think>…</think> and returning empty
# ``content``. See OllamaBackend.generate for the full rationale.
_THINK_PREFILL = "<think>\n\n</think>\n\n"


def _strip_think(text: str) -> str:
    """Remove any ``<think>...</think>`` reasoning that a thinking model bled into
    the answer channel, so the LanguageGuard sees only the final operator text."""
    global _THINK_RE
    if _THINK_RE is None:
        import re
        _THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
    return _THINK_RE.sub("", text).strip()


def make_image_message(role: str, text: str, image_bytes: bytes | None = None) -> Message:
    """Build a chat message with an optional inline image (OpenAI multimodal shape)."""
    if image_bytes is None:
        return {"role": role, "content": text}
    return {
        "role": role,
        "content": [
            {"type": "image_url", "image_url": {"url": encode_image_b64(image_bytes)}},
            {"type": "text", "text": text},
        ],
    }


def to_ollama_messages(messages: list[Message]) -> list[Message]:
    """Translate OpenAI multimodal messages into Ollama's native /api/chat shape.

    OpenAI puts vision inputs in ``content`` as a list of typed blocks
    (``image_url`` data-URIs + ``text``). Ollama instead wants a flat
    ``content`` string plus a separate ``images`` list of *bare* base64 strings
    (no ``data:…;base64,`` prefix). Sending the OpenAI shape to Ollama returns
    400 Bad Request — which silently degraded every image verdict to the
    fallback template. Text-only messages pass through unchanged.
    """
    out: list[Message] = []
    for m in messages:
        content = m.get("content")
        if not isinstance(content, list):
            out.append(m)
            continue
        text_parts: list[str] = []
        images: list[str] = []
        for block in content:
            btype = block.get("type")
            if btype == "text":
                text_parts.append(block.get("text", ""))
            elif btype == "image_url":
                url = block.get("image_url", {}).get("url", "")
                # Strip the data-URI prefix → bare base64, as Ollama expects.
                images.append(url.split(",", 1)[1] if "," in url else url)
        msg: Message = {"role": m["role"], "content": "\n".join(text_parts)}
        if images:
            msg["images"] = images
        out.append(msg)
    return out


# ---------------------------------------------------------------------------
# vLLM backend (OpenAI-compatible API)
# ---------------------------------------------------------------------------
@dataclass
class VLLMBackend:
    """Calls a local vLLM server's /v1/chat/completions endpoint.

    vLLM startup flags (on the GPU box, not here):
        vllm serve Qwen/Qwen3-VL-4B-Instruct \\
            --port 8080 \\
            --disable-log-requests \\
            --trust-remote-code

    Env on the vLLM process (set in systemd unit / launch script):
        VLLM_DISABLE_USAGE_STATS=1
        VLLM_NO_DEPRECATION_WARNING=1
        HF_HUB_OFFLINE=1           # air-gap: block any HuggingFace requests
        TRANSFORMERS_OFFLINE=1
    """

    base_url: str = "http://127.0.0.1:8080"
    model: str = "Qwen/Qwen3-VL-4B-Instruct"
    timeout_s: float = 60.0

    async def generate(
        self,
        messages: list[Message],
        *,
        temperature: float = 0.1,
        max_tokens: int = 512,
    ) -> str:
        import httpx  # already in requirements

        url = f"{self.base_url.rstrip('/')}/v1/chat/completions"
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }
        async with httpx.AsyncClient(timeout=self.timeout_s) as client:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
            body = resp.json()

        text: str = body["choices"][0]["message"]["content"]
        log.debug(
            "vLLM generate: tokens=%s finish=%s",
            body.get("usage", {}).get("completion_tokens"),
            body["choices"][0].get("finish_reason"),
        )
        return text.strip()


# ---------------------------------------------------------------------------
# Ollama backend
# ---------------------------------------------------------------------------
@dataclass
class OllamaBackend:
    """Calls a local Ollama server's /api/chat endpoint.

    Start Ollama with analytics disabled (set in launch environment):
        OLLAMA_NO_ANALYTICS=1 ollama serve

    Pull the model before first use:
        ollama pull qwen3-vl:4b
    """

    base_url: str = "http://127.0.0.1:11434"
    model: str = "qwen3-vl:4b"
    timeout_s: float = 120.0
    # Qwen3 / Qwen3-VL ship with chain-of-thought ON and will, within any
    # practical ``num_predict`` budget, exhaust the budget *inside* the <think>
    # block and return an empty ``content`` (observed: even 2000 tokens, fully
    # GPU-offloaded, still no answer). Passing ``think:false`` alone does NOT stop
    # it on Qwen3-VL in current Ollama builds. The reliable fix is to *prefill* an
    # already-closed reasoning block (``_THINK_PREFILL``) as the start of the
    # assistant turn: the model then continues straight into its final Uzbek
    # answer (~7s instead of empty). ``_strip_think`` removes any residual tag.

    async def generate(
        self,
        messages: list[Message],
        *,
        temperature: float = 0.1,
        max_tokens: int = 512,
    ) -> str:
        import httpx

        url = f"{self.base_url.rstrip('/')}/api/chat"
        ollama_messages = to_ollama_messages(messages)
        # Prefill the closed reasoning block so the thinking model skips straight
        # to the answer (see class comment). Harmless for non-thinking models.
        ollama_messages.append({"role": "assistant", "content": _THINK_PREFILL})
        payload = {
            "model": self.model,
            "messages": ollama_messages,
            "stream": False,
            "think": False,
            "options": {
                "temperature": temperature,
                "num_predict": max_tokens,
            },
        }
        async with httpx.AsyncClient(timeout=self.timeout_s) as client:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
            body = resp.json()

        text: str = body["message"]["content"]
        log.debug("Ollama generate: done_reason=%s", body.get("done_reason"))
        return _strip_think(text)


# ---------------------------------------------------------------------------
# llama.cpp backend (OpenAI-compatible server mode)
# ---------------------------------------------------------------------------
@dataclass
class LlamaCppBackend:
    """Calls llama-cpp-python's OpenAI-compatible server.

    Start the server (GPU box, no telemetry — llama.cpp has none):
        python -m llama_cpp.server \\
            --model /models/qwen3-vl-4b.Q4_K_M.gguf \\
            --host 127.0.0.1 --port 8081 \\
            --n_gpu_layers -1 --chat_format chatml

    Quantized GGUF models for Qwen3-VL are available from:
        Qwen/Qwen3-VL-4B-GGUF on HuggingFace (mirror before air-gap).
    """

    base_url: str = "http://127.0.0.1:8081"
    model: str = "qwen3-vl-4b"   # model name as registered with the server
    timeout_s: float = 180.0

    async def generate(
        self,
        messages: list[Message],
        *,
        temperature: float = 0.1,
        max_tokens: int = 512,
    ) -> str:
        import httpx

        url = f"{self.base_url.rstrip('/')}/v1/chat/completions"
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }
        async with httpx.AsyncClient(timeout=self.timeout_s) as client:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
            body = resp.json()

        text: str = body["choices"][0]["message"]["content"]
        return text.strip()


# ---------------------------------------------------------------------------
# Transformers backend — direct in-process inference (no server needed)
# ---------------------------------------------------------------------------
@dataclass
class TransformersBackend:
    """Loads Qwen3-VL-4B-Instruct directly in-process via HuggingFace transformers.

    No separate vLLM/Ollama/llama.cpp server is required.  The model is loaded
    once at construction time and held for the process lifetime.

    Requirements (add to requirements-vlm.txt before use):
        torch>=2.3.0
        transformers>=4.52.0
        qwen-vl-utils>=0.0.8
        accelerate>=0.35.0

    Model download (do this before air-gapping):
        huggingface-cli download Qwen/Qwen3-VL-4B-Instruct \\
            --local-dir /models/qwen3-vl-4b

    Env:
        XRAY_VLM_MODEL_PATH   local path to model directory (default: /models/qwen3-vl-4b)
        XRAY_VLM_DEVICE_MAP   "cuda" | "cpu" | "auto"  (default: "auto")
        XRAY_VLM_TORCH_DTYPE  "bfloat16" | "float16" | "float32" (default: "bfloat16")

    GPU memory footprint (approximate):
        4B bfloat16  → ~8 GB VRAM
        4B int4 GPTQ → ~4 GB VRAM  (use a GPTQ model variant)

    Air-gap: set HF_HUB_OFFLINE=1 and TRANSFORMERS_OFFLINE=1 to prevent any
    network calls during model loading.
    """

    model_path: str = "/models/qwen3-vl-4b"
    device_map: str = "auto"
    torch_dtype: str = "bfloat16"
    max_new_tokens: int = 512
    temperature: float = 0.1

    # Internal state — populated by _load()
    _model: object = field(default=None, init=False, repr=False)
    _processor: object = field(default=None, init=False, repr=False)
    _loaded: bool = field(default=False, init=False, repr=False)
    _lock: object = field(default_factory=threading.Lock, init=False, repr=False)

    def _load(self) -> None:
        """Load model and processor once (thread-safe)."""
        import threading as _threading
        if self._loaded:
            return
        with self._lock:
            if self._loaded:
                return
            log.info("Loading Qwen3-VL from %s (device_map=%s dtype=%s) …",
                     self.model_path, self.device_map, self.torch_dtype)

            import os
            os.environ.setdefault("HF_HUB_OFFLINE", "1")
            os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

            import torch
            from transformers import AutoProcessor

            # Use the UNIVERSAL image-text-to-text auto class, not a hard-coded
            # Qwen2.5-VL class. The serving model is Qwen3-VL — loading it through
            # ``Qwen2_5_VLForConditionalGeneration`` is the wrong architecture and
            # either errors or silently mis-maps weights. ``AutoModelForImageText
            # ToText`` resolves the correct class from the model config (Qwen3-VL,
            # Qwen2.5-VL, or any future VLM) via the HF auto map. Older transformers
            # without this class fall back to the generic causal-LM auto class.
            try:
                from transformers import AutoModelForImageTextToText as _VLMAuto
            except ImportError:  # transformers < 4.49
                from transformers import AutoModelForCausalLM as _VLMAuto  # type: ignore

            dtype_map = {
                "bfloat16": torch.bfloat16,
                "float16":  torch.float16,
                "float32":  torch.float32,
            }
            dtype = dtype_map.get(self.torch_dtype, torch.bfloat16)

            self._processor = AutoProcessor.from_pretrained(
                self.model_path,
                trust_remote_code=True,
            )
            self._model = _VLMAuto.from_pretrained(
                self.model_path,
                torch_dtype=dtype,
                device_map=self.device_map,
                trust_remote_code=True,
            )
            self._loaded = True
            log.info("Qwen3-VL loaded successfully from %s.", self.model_path)

    async def generate(
        self,
        messages: list[Message],
        *,
        temperature: float = 0.1,
        max_tokens: int = 512,
    ) -> str:
        """Run inference in the default executor to avoid blocking the event loop."""
        import asyncio
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None,
            self._generate_sync,
            messages,
            temperature,
            max_tokens,
        )

    def _generate_sync(
        self,
        messages: list[Message],
        temperature: float,
        max_tokens: int,
    ) -> str:
        """Blocking inference. Called from a thread pool via run_in_executor."""
        self._load()

        import torch
        from qwen_vl_utils import process_vision_info  # type: ignore[import]

        # Build text-only fallback if qwen_vl_utils is unavailable
        try:
            image_inputs, video_inputs = process_vision_info(messages)
        except Exception:
            image_inputs, video_inputs = None, None

        text = self._processor.apply_chat_template(   # type: ignore[union-attr]
            messages,
            tokenize=False,
            add_generation_prompt=True,
        )

        inputs = self._processor(  # type: ignore[call-arg]
            text=[text],
            images=image_inputs,
            videos=video_inputs,
            padding=True,
            return_tensors="pt",
        )
        inputs = inputs.to(self._model.device)  # type: ignore[union-attr]

        with torch.inference_mode():
            output_ids = self._model.generate(  # type: ignore[union-attr]
                **inputs,
                max_new_tokens=max_tokens,
                temperature=temperature,
                do_sample=temperature > 0.0,
            )

        # Strip the prompt tokens from the output
        input_len   = inputs.input_ids.shape[1]
        new_ids     = output_ids[:, input_len:]
        result      = self._processor.batch_decode(  # type: ignore[union-attr]
            new_ids,
            skip_special_tokens=True,
            clean_up_tokenization_spaces=False,
        )
        text_out = result[0].strip() if result else ""
        log.debug("TransformersBackend generated %d chars", len(text_out))
        return text_out


# ---------------------------------------------------------------------------
# Stub — for testing without a running VLM
# ---------------------------------------------------------------------------
class _EchoBackend:
    """Test stub: echoes the last user message back. Not for production."""

    async def generate(
        self,
        messages: list[Message],
        *,
        temperature: float = 0.1,
        max_tokens: int = 512,
    ) -> str:
        last_user = next(
            (m for m in reversed(messages) if m["role"] == "user"),
            {"content": ""},
        )
        content = last_user["content"]
        if isinstance(content, list):
            content = " ".join(
                p.get("text", "") for p in content if isinstance(p, dict)
            )
        return f"TAVSIF: Test tavsif.\nSABAB: Test sabab. ({content[:40]})"


def build_backend(
    backend_type: str,
    *,
    base_url: str | None = None,
    model: str | None = None,
    timeout_s: float = 60.0,
) -> VLMBackend:
    """Factory: build the right backend from a string name.

    ``backend_type`` values: "vllm" | "ollama" | "llamacpp" | "echo" (test).
    """
    t = backend_type.lower().strip()
    if t == "vllm":
        kwargs: dict = {"timeout_s": timeout_s}
        if base_url:
            kwargs["base_url"] = base_url
        if model:
            kwargs["model"] = model
        return VLLMBackend(**kwargs)
    elif t == "ollama":
        kwargs = {"timeout_s": timeout_s}
        if base_url:
            kwargs["base_url"] = base_url
        if model:
            kwargs["model"] = model
        return OllamaBackend(**kwargs)
    elif t in ("llamacpp", "llama_cpp", "llama-cpp"):
        kwargs = {"timeout_s": timeout_s}
        if base_url:
            kwargs["base_url"] = base_url
        if model:
            kwargs["model"] = model
        return LlamaCppBackend(**kwargs)
    elif t in ("transformers", "local", "direct"):
        # Direct in-process inference — model_path from `base_url` arg or env
        import os
        model_path = (
            base_url
            or os.environ.get("XRAY_VLM_MODEL_PATH", "/models/qwen3-vl-4b")
        )
        return TransformersBackend(
            model_path=model_path,
            device_map=os.environ.get("XRAY_VLM_DEVICE_MAP", "auto"),
            torch_dtype=os.environ.get("XRAY_VLM_TORCH_DTYPE", "bfloat16"),
        )
    elif t == "echo":
        return _EchoBackend()
    else:
        raise ValueError(
            f"Unknown VLM backend {backend_type!r}. "
            f"Valid: vllm | ollama | llamacpp | transformers | echo"
        )


__all__ = [
    "VLMBackend",
    "Message",
    "VLLMBackend",
    "OllamaBackend",
    "LlamaCppBackend",
    "TransformersBackend",
    "build_backend",
    "make_image_message",
    "encode_image_b64",
]
