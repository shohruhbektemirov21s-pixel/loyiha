"""Runtime configuration. Air-gap defaults: nothing reaches out by default.

All values are env-overridable (12-factor) but every default is safe for an
isolated server — no external hosts, CORS closed, docs on (internal network).
"""

from __future__ import annotations

from typing import Any

from pydantic import model_validator
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict


class _PrefixFilteredSource(PydanticBaseSettingsSource):
    """Wraps an env/dotenv source and keeps only XRAY_-prefixed keys.

    pydantic-settings strips ``env_prefix`` from matching keys but leaves
    *non*-prefixed keys in the parsed mapping. Under ``extra="forbid"`` those
    non-XRAY keys — a shared .env may carry OLLAMA_NO_ANALYTICS, DO_NOT_TRACK,
    PATH, … — would abort boot. We want the opposite contract:

      * a non-XRAY key in the .env  -> ignored (other tools may share the file);
      * a *typo'd* XRAY key         -> still rejected by forbid (a real defect).

    The wrapped source has already stripped the prefix from recognised keys, so
    after stripping the only way to tell "was XRAY-prefixed" from "wasn't" is to
    consult the raw env mapping. We therefore drop any produced key whose
    *original* (un-stripped) form did not start with the prefix.
    """

    def __init__(self, inner: PydanticBaseSettingsSource) -> None:
        super().__init__(inner.settings_cls)
        self._inner = inner

    def get_field_value(self, field: Any, field_name: str) -> tuple[Any, str, bool]:
        return self._inner.get_field_value(field, field_name)

    def __call__(self) -> dict[str, Any]:
        prefix = (self.config.get("env_prefix") or "").lower()
        produced = self._inner()
        if not prefix:
            return produced
        # Raw env mapping seen by the inner source (case-insensitive). Used to
        # (a) keep only keys that came from an XRAY_* env var, and (b) surface
        # *unrecognised* XRAY_* keys so extra="forbid" can reject typos — even on
        # the OS-env path, where pydantic-settings otherwise silently drops any
        # env var that does not map to a declared field (it cannot tell a stray
        # XRAY_DETECTOR_ENABLD from the thousands of unrelated OS vars).
        try:
            raw = {k.lower(): v for k, v in self._inner._load_env_vars().items()}  # type: ignore[attr-defined]
        except Exception:
            return produced

        kept: dict[str, Any] = {}
        for key, value in produced.items():
            # A recognised field arrives here already stripped of the prefix;
            # an unrecognised extra arrives with its full name. Accept the key
            # only if some raw env var named prefix+key (or key itself, for an
            # already-XRAY-prefixed extra) was actually present.
            if (prefix + key).lower() in raw or (key.lower() in raw and key.lower().startswith(prefix)):
                kept[key] = value

        # Known names (stripped of prefix) so we can tell a typo from a real key.
        # Includes declared fields AND read-only @property names (e.g. docs_url):
        # an env var matching a property is a benign no-op the app already
        # tolerated, so it must NOT be flagged as a typo.
        known_stripped = {n.lower() for n in self.settings_cls.model_fields}
        known_stripped |= {
            n.lower()
            for n in dir(self.settings_cls)
            if isinstance(getattr(self.settings_cls, n, None), property)
        }
        for raw_key, raw_value in raw.items():
            if not raw_key.startswith(prefix):
                continue  # non-XRAY OS env var (PATH, HOME, …) — ignore.
            stripped = raw_key[len(prefix):]
            if stripped not in known_stripped and stripped not in kept:
                # An XRAY_* var that maps to no field: feed it through under its
                # stripped name so extra="forbid" raises a clear error.
                kept[stripped] = raw_value
        return kept


class Settings(BaseSettings):
    # extra="forbid": a mistyped or stale XRAY_* key in the .env file aborts boot
    # instead of being silently dropped — a wrong setting on a security-critical
    # box is a defect, not something to swallow. Note the deliberate scope:
    #   * Only the XRAY_ prefix is consumed; non-prefixed keys in a shared .env
    #     (e.g. OLLAMA_NO_ANALYTICS) are ignored by the prefix matcher, not
    #     rejected — so other tools can co-habit one .env.
    #   * Sibling agents that legitimately share this .env (the camera capture
    #     agent reads XRAY_CAM_* directly via os.environ) declare their keys in
    #     the "shared, app-ignored" block below so forbid distinguishes a real
    #     typo from a known sibling key.
    model_config = SettingsConfigDict(env_prefix="XRAY_", env_file=".env", extra="forbid")

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        # Wrap the OS-env and .env sources so only XRAY_-prefixed keys reach the
        # model. Non-prefixed keys from a shared .env are dropped (ignored);
        # mistyped XRAY_* keys still surface and hit extra="forbid".
        return (
            init_settings,
            _PrefixFilteredSource(env_settings),
            _PrefixFilteredSource(dotenv_settings),
            file_secret_settings,
        )

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

    # -- Cargo image screening (/v1/screen) ---------------------------------
    # How many independent passes per image (self-consistency voting). Higher =
    # more accurate but proportionally slower (each pass is one VLM call).
    # 1 = fast/single-shot; 3 = accurate (default). XRAY_SCREEN_PASSES.
    screen_passes: int = 3

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

    # -- Image store (encrypted object store) --------------------------------
    # Consumed by the acquisition/store layer; declared here so the API shares
    # one .env with that layer under extra="forbid".
    store_key: str | None = None
    store_dir: str | None = None

    # -- Observability passthrough -------------------------------------------
    metrics_enable: bool = True

    # -- Shared sibling keys (declared so extra="forbid" tolerates the shared
    #    .env). The camera capture agent (camera/) reads these directly from
    #    os.environ; the API does not use them, but they live in the same .env,
    #    so we must recognise them here or a legitimate shared key would abort
    #    boot. A genuinely-mistyped XRAY_* key still fails — that is the point.
    cam_device: str | None = None
    cam_width: int | None = None
    cam_height: int | None = None
    cam_fps: int | None = None
    cam_roi: str | None = None
    cam_encode_qual: int | None = None
    cam_motion_thresh: int | None = None
    cam_out_dir: str | None = None

    # -- Acquisition sibling keys (acquisition/ + deploy compose) ------------
    acq_driver: str | None = None
    acq_scanner_id: str | None = None
    acq_lane_id: str | None = None
    acq_dicos_watch_dir: str | None = None
    acq_api_base_url: str | None = None
    grab_device: str | None = None
    version: str | None = None     # XRAY_VERSION (image tag), used by compose only

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

        # Fail-open guard (KRITIK-4): in production the DB MUST be wired. Without
        # XRAY_DB_URL the app falls back to _NullScanStore (no persistence) and
        # _LoggingAuditSink (no tamper-evident audit) — i.e. a customs box that
        # silently keeps no records. That is the exact fail-open we refuse to
        # allow. Persistence + audit are non-negotiable in prod.
        if is_prod and not self.db_url:
            raise ValueError(
                "XRAY_DB_URL is not set in a production environment. The app "
                "would fall back to a null scan store and a logging-only audit "
                "sink — running with no persistence and no tamper-evident audit. "
                "Refusing to start. Set XRAY_DB_URL to the postgres+asyncpg DSN."
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
