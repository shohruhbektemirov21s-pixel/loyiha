#!/usr/bin/env bash
# ============================================================
# X-ray Assistant — Startup script
# ============================================================
# Starts all components needed for a real production run.
#
# Usage:
#   chmod +x deploy/start.sh
#   sudo ./deploy/start.sh
#
# Prerequisites:
#   - .env file present (copy from .env.production and fill secrets)
#   - pip install -r requirements.txt [-r requirements-vlm.txt]
#   - PostgreSQL running (if XRAY_DB_URL is set)
#   - Model files at XRAY_VLM_MODEL_PATH and XRAY_DETECTOR_WEIGHTS

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

cd "$PROJECT_ROOT"

# ── Load .env ────────────────────────────────────────────────
if [ -f ".env" ]; then
    set -a
    # shellcheck disable=SC1091
    source .env
    set +a
    echo "[start.sh] Loaded .env"
else
    echo "[start.sh] WARNING: .env not found. Using environment variables as-is."
fi

# ── Validate required secrets ────────────────────────────────
_check_var() {
    local var="$1"
    local val="${!var:-}"
    if [ -z "$val" ] || [[ "$val" == REPLACE_* ]]; then
        echo "[start.sh] ERROR: $var is not set or still contains placeholder value."
        exit 1
    fi
}
_check_var XRAY_JWT_SECRET
_check_var XRAY_AUDIT_HMAC_KEY

# ── Start Ollama (if backend=ollama) ─────────────────────────
VLM_BACKEND="${XRAY_VLM_BACKEND:-vllm}"
if [ "$VLM_BACKEND" = "ollama" ]; then
    echo "[start.sh] Starting Ollama server…"
    OLLAMA_NO_ANALYTICS=1 ollama serve &
    OLLAMA_PID=$!
    sleep 3

    MODEL="${XRAY_VLM_MODEL:-qwen3-vl:4b}"
    echo "[start.sh] Pulling Ollama model: $MODEL"
    ollama pull "$MODEL" || echo "[start.sh] WARNING: ollama pull failed — model may already be present."
    echo "[start.sh] Ollama PID=$OLLAMA_PID"
fi

# ── Start vLLM (if backend=vllm) ─────────────────────────────
if [ "$VLM_BACKEND" = "vllm" ]; then
    MODEL="${XRAY_VLM_MODEL:-Qwen/Qwen3-VL-4B-Instruct}"
    PORT="${XRAY_VLM_BASE_URL##*:}"
    PORT="${PORT:-8080}"
    echo "[start.sh] Starting vLLM server: model=$MODEL port=$PORT"
    VLLM_DISABLE_USAGE_STATS=1 VLLM_NO_DEPRECATION_WARNING=1 \
    HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 \
    python -m vllm.entrypoints.openai.api_server \
        --model "$MODEL" \
        --port "$PORT" \
        --disable-log-requests \
        --trust-remote-code \
        &
    VLLM_PID=$!
    echo "[start.sh] vLLM PID=$VLLM_PID — waiting 10s for startup…"
    sleep 10
fi

# ── Database migrations ───────────────────────────────────────
if [ -n "${XRAY_DB_URL:-}" ] && [[ "${XRAY_DB_URL}" != *"REPLACE"* ]]; then
    echo "[start.sh] Running DB migrations…"
    python -m app.db.migrate || echo "[start.sh] WARNING: migration failed (DB may not be running yet)"
fi

# ── Start the FastAPI server ──────────────────────────────────
HOST="${XRAY_HOST:-127.0.0.1}"
PORT="${XRAY_PORT:-8000}"
WORKERS="${XRAY_WORKERS:-1}"

echo "[start.sh] Starting API server on $HOST:$PORT (workers=$WORKERS)…"
exec uvicorn app.main:app \
    --host "$HOST" \
    --port "$PORT" \
    --workers "$WORKERS" \
    --no-access-log
