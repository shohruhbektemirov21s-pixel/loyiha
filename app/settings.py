"""Runtime configuration. Air-gap defaults: nothing reaches out by default.

All values are env-overridable (12-factor) but every default is safe for an
isolated server — no external hosts, CORS closed, docs on (internal network).
"""

from __future__ import annotations

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="XRAY_", env_file=".env", extra="ignore")

    app_name: str = "xray-assistant"
    environment: str = "dev"  # dev | staging | prod

    # Serving
    host: str = "127.0.0.1"
    port: int = 8000

    # OpenAPI docs — on for the internal network; flip off via env in prod if required.
    enable_docs: bool = True

    # CORS: empty => closed. The NiceGUI console is same-origin (also on FastAPI),
    # so this stays empty unless a separate Vue front-end is deployed later.
    cors_allow_origins: list[str] = []

    # -- Detector serving (Hop 2) --------------------------------------------
    # OFF by default: on a box without the ML stack (contract/API box) the seam
    # stays the honest 501 stub. The GPU serving box sets XRAY_DETECTOR_ENABLED=1
    # and the weights path; startup then loads the model fail-closed (a missing
    # or unloadable artifact aborts boot — it never silently degrades to 501).
    detector_enabled: bool = False
    detector_weights: str = "weights/best.onnx"   # .onnx/.engine/.pt — runtime inferred from ext
    detector_device: str | None = None            # None => ultralytics auto; set 'cuda:0' on GPU box
    # imgsz is NOT a free runtime knob: the ONNX is exported static at this size
    # (weapons are small/thin). It MUST equal the train/export imgsz or geometry
    # and recall both shift. Overriding away from 1024 logs a loud warning.
    detector_imgsz: int = 1024
    # Emission net (conf/iou). None => use taxonomy.NET_CONF / NET_IOU, the single
    # source of truth shared by train/serve/profile. Only set to override locally.
    detector_conf: float | None = None
    detector_iou: float | None = None
    # Provenance, logged at every hop. weights_sha256 is computed from the loaded
    # bytes at startup (ground truth of what ran) — not configured here.
    detector_name: str = "xray-weapons-yolo11m"
    detector_version: str = "0.1.0"
    detector_runtime: str | None = None           # None => inferred from weights extension
    # Per-class Platt params as JSON {native_label: [a, b]}. None => IdentityCalibrator
    # (honest: we don't claim calibration we didn't fit).
    detector_calibration: str | None = None
    detector_verify_sha256: bool = True           # frame integrity check in the loader

    # -- VLM serving (Hop 3) -------------------------------------------------
    # OFF by default: the seam stays the honest 501 stub. The GPU/VLM box sets
    # XRAY_VLM_ENABLED=1 and the backend URL; startup is then fail-closed.
    vlm_enabled: bool = False
    # Backend selection: "vllm" (production) | "ollama" (prototype) |
    # "llamacpp" (edge/offline) | "transformers" (direct, no server) |
    # "echo" (test stub — never in prod).
    vlm_backend: str = "vllm"
    # Base URL of the locally running VLM server. Never a remote host.
    # For "transformers" backend this field is ignored; use vlm_model_path.
    vlm_base_url: str = "http://127.0.0.1:8080"
    # Model identifier as registered with the backend server (vllm/ollama/llamacpp).
    vlm_model: str = "Qwen/Qwen3-VL-4B-Instruct"
    # -- TransformersBackend (XRAY_VLM_BACKEND=transformers) --
    # Local directory with Qwen3-VL-4B-Instruct weights (downloaded via
    # deploy/download_model.sh before air-gapping the server).
    vlm_model_path: str = "/models/qwen3-vl-4b"
    # torch device_map: "auto" | "cuda" | "cpu"
    vlm_device_map: str = "auto"
    # torch dtype: "bfloat16" | "float16" | "float32"
    vlm_torch_dtype: str = "bfloat16"
    vlm_timeout_s: float = 120.0
    # Slot-fill temperature. 0.10 keeps the model deterministic enough for
    # structured Uzbek slots while allowing minor variation. Lower if drift
    # increases; never go above 0.3 for structured operator text.
    vlm_temperature: float = 0.10
    vlm_max_tokens: int = 300
    # Provenance labels logged at every verdict hop.
    vlm_name: str = "qwen3-vl"
    vlm_version: str = "4b"
    # Optional: path to a SecureImageStore root for loading detection crops.
    # If unset, the VLM falls back to text-only prompts (no crop images).
    vlm_store_root: str | None = None
    # Whether to probe the VLM backend at startup (recommended; set False for
    # "transformers" backend since model loading happens lazily on first call).
    vlm_verify: bool = True
    # Whether the VLM actually generates per-detection Uzbek descriptions.
    # On a CPU box a small model can't produce clean Uzbek in time (it exhausts
    # its token budget on chain-of-thought), so set XRAY_VLM_DESCRIBE=false: the
    # verdict then uses deterministic, fact-derived Uzbek (vlm/prompts.py
    # deterministic_slots) — fast and correct. Set true on a GPU box to enable
    # real model descriptions.
    vlm_describe: bool = True

    # -- Active-learning data layer (Hop 4 FeedbackSink) --------------------
    # Set XRAY_DATALAYER_ENABLED=1 to wire the real ActiveLearningLoop.
    # When disabled the FeedbackSink stub returns HTTP 501.
    datalayer_enabled: bool = False
    # Root directory for all mutable data (queue, versions, retrain jobs).
    # Sub-paths are created automatically on first use.
    data_dir: str = "/var/lib/xray"
    # Override individual sub-paths if needed (defaults: data_dir/{name}).
    queue_dir: str | None = None       # label queue JSONL files
    version_dir: str | None = None     # model version manifests
    job_dir: str | None = None         # retrain job-spec files
    dataset_target: str = "active-learning/pending"

    # -- PostgreSQL (persistence + audit) ------------------------------------
    # postgres+asyncpg DSN.  Unset = DB not wired; seams fall back to stubs.
    db_url: str | None = None
    db_pool_size: int = 10
    # Require TLS on the DB connection (recommended even on LAN).
    db_ssl: bool = False
    # Echo SQL to the log (only for dev; never in prod).
    db_echo: bool = False

    # -- JWT authentication --------------------------------------------------
    # HS256 secret, minimum 32 characters. Required when DB is wired.
    # Generate: python -c "import secrets; print(secrets.token_hex(32))"
    jwt_secret: str = ""
    # Dev bypass: skip JWT verification entirely (NEVER in prod).
    auth_bypass: bool = False
    jwt_expires_seconds: int = 28800   # 8 hours (one operator shift)

    # -- Audit HMAC chain ----------------------------------------------------
    # 64 hex chars (32 bytes). Required when DB is wired.
    # Generate: python -c "import secrets; print(secrets.token_hex(32))"
    audit_hmac_key: str = ""

    @model_validator(mode="after")
    def _enforce_security_invariants(self) -> "Settings":
        """Fail-fast at boot instead of on the first request.

        The auth layer (auth/backend.py) and audit sink already refuse to run
        without their secrets, but they only discover that lazily — the first
        login or first audit write returns a 500. When the DB is wired (a real
        deployment) or environment=prod we surface the problem at startup, and
        we refuse to boot a production box with authentication disabled.
        """
        is_prod = self.environment.lower() in ("prod", "production")
        secrets_required = is_prod or bool(self.db_url)

        if is_prod and self.auth_bypass:
            raise ValueError(
                "XRAY_AUTH_BYPASS is enabled in a production environment. "
                "It disables ALL authentication — refusing to start."
            )

        if secrets_required:
            if len(self.jwt_secret) < 32:
                raise ValueError(
                    "XRAY_JWT_SECRET must be at least 32 characters when the DB "
                    "is wired or environment=prod. Generate: "
                    'python -c "import secrets; print(secrets.token_hex(32))"'
                )
            try:
                hmac_bytes = bytes.fromhex(self.audit_hmac_key)
            except ValueError as exc:
                raise ValueError(
                    f"XRAY_AUDIT_HMAC_KEY must be valid hex (64 chars / 32 bytes): {exc}"
                ) from exc
            if len(hmac_bytes) < 16:
                raise ValueError(
                    "XRAY_AUDIT_HMAC_KEY must be at least 32 hex chars (16 bytes)."
                )
        return self

    @property
    def docs_url(self) -> str | None:
        return "/docs" if self.enable_docs else None

    @property
    def openapi_url(self) -> str | None:
        return "/openapi.json" if self.enable_docs else None


_settings: Settings | None = None


def get_settings() -> Settings:
    """Cached settings accessor (cheap singleton; avoids re-reading env per request)."""
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
