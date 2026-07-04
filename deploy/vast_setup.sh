#!/usr/bin/env bash
# ============================================================
# vast.ai GPU box — one-shot Ollama + Qwen3-VL-8B setup
# ============================================================
# Run this ON the rented vast.ai instance (paste into its SSH shell
# or the "onstart" box). It installs Ollama, serves it, pulls the
# Qwen3-VL-8B vision model, and warms it up — ready to serve the
# X-ray VLM seam over the network.
#
# Usage (on the vast box):
#   curl -fsSL <this-file> -o vast_setup.sh   # or scp it over
#   chmod +x vast_setup.sh
#   ./vast_setup.sh                           # default: qwen3-vl:8b
#   MODEL=qwen3-vl:8b ./vast_setup.sh         # explicit
#
# After it finishes, connect the project from your laptop with an
# SSH tunnel (keeps Ollama private — no open port on the internet):
#   ssh -p <VAST_SSH_PORT> -L 11434:127.0.0.1:11434 root@<VAST_HOST>
# then locally set in .env:
#   XRAY_VLM_BACKEND=ollama
#   XRAY_VLM_BASE_URL=http://127.0.0.1:11434
#   XRAY_VLM_MODEL=qwen3-vl:8b
#   XRAY_VLM_NAME=qwen3-vl
#   XRAY_VLM_VERSION=8b

set -euo pipefail

MODEL="${MODEL:-qwen3-vl:8b}"
# Bind to all interfaces so the model is reachable both locally and via
# an SSH tunnel. On vast.ai the box is behind NAT; use the SSH tunnel above.
OLLAMA_HOST_BIND="${OLLAMA_HOST_BIND:-0.0.0.0:11434}"

echo "==============================================================="
echo " vast.ai setup — Ollama + $MODEL"
echo "==============================================================="

# ── 0. GPU sanity check ──────────────────────────────────────
if command -v nvidia-smi >/dev/null 2>&1; then
    echo "[vast] GPU:"
    nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader
else
    echo "[vast] WARNING: nvidia-smi not found — is this a GPU instance? Continuing anyway."
fi

# ── 1. Install Ollama (official installer) ───────────────────
if ! command -v ollama >/dev/null 2>&1; then
    echo "[vast] Installing Ollama…"
    curl -fsSL https://ollama.com/install.sh | sh
else
    echo "[vast] Ollama already installed: $(ollama --version 2>/dev/null || echo present)"
fi

# ── 2. Start the Ollama server (background) ──────────────────
# Kill any stale server first so the new bind address takes effect.
pkill -f "ollama serve" 2>/dev/null || true
sleep 1
echo "[vast] Starting Ollama server on $OLLAMA_HOST_BIND …"
OLLAMA_HOST="$OLLAMA_HOST_BIND" OLLAMA_NO_ANALYTICS=1 \
    nohup ollama serve > /var/log/ollama.log 2>&1 &
# Wait for the server to answer.
for i in $(seq 1 30); do
    if curl -fsS "http://127.0.0.1:11434/api/version" >/dev/null 2>&1; then
        echo "[vast] Ollama is up."
        break
    fi
    sleep 1
done

# ── 3. Pull the model ────────────────────────────────────────
echo "[vast] Pulling $MODEL (this downloads several GB)…"
OLLAMA_HOST="127.0.0.1:11434" ollama pull "$MODEL"

# ── 4. Warm-up / smoke test (loads weights into VRAM) ────────
echo "[vast] Warming up $MODEL …"
OLLAMA_HOST="127.0.0.1:11434" ollama run "$MODEL" "Reply with the single word: READY" || true

echo ""
echo "[vast] Loaded models:"
OLLAMA_HOST="127.0.0.1:11434" ollama ls
echo ""
echo "[vast] VRAM after load:"
nvidia-smi --query-gpu=memory.used,memory.total --format=csv,noheader 2>/dev/null || true

echo ""
echo "==============================================================="
echo " DONE. Ollama is serving $MODEL on port 11434."
echo ""
echo " From your laptop, open an SSH tunnel (recommended, private):"
echo "   ssh -p <VAST_SSH_PORT> -L 11434:127.0.0.1:11434 root@<VAST_HOST>"
echo ""
echo " Then in the project .env set:"
echo "   XRAY_VLM_BACKEND=ollama"
echo "   XRAY_VLM_BASE_URL=http://127.0.0.1:11434"
echo "   XRAY_VLM_MODEL=$MODEL"
echo "   XRAY_VLM_NAME=qwen3-vl"
echo "   XRAY_VLM_VERSION=8b"
echo "==============================================================="
