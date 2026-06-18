#!/usr/bin/env bash
# ============================================================
# Download Qwen3-VL-4B-Instruct before air-gapping the server
# ============================================================
# Run this ONCE on a machine with internet access, then copy
# /models/qwen3-vl-4b to the air-gapped server via USB.
#
# Usage:
#   chmod +x deploy/download_model.sh
#   ./deploy/download_model.sh
#   # Then rsync /models/qwen3-vl-4b to the production server

set -euo pipefail

MODEL_ID="Qwen/Qwen3-VL-4B-Instruct"
LOCAL_DIR="${1:-/models/qwen3-vl-4b}"

echo "Downloading $MODEL_ID → $LOCAL_DIR"
echo "This will download ~8-10 GB."
echo ""

mkdir -p "$LOCAL_DIR"

# Check huggingface-cli is available
if ! command -v huggingface-cli &> /dev/null; then
    echo "Installing huggingface-hub CLI…"
    pip install huggingface-hub
fi

# Download the model (all shards)
huggingface-cli download \
    "$MODEL_ID" \
    --local-dir "$LOCAL_DIR" \
    --local-dir-use-symlinks false \
    --include "*.safetensors" "*.json" "*.txt" "tokenizer*" "preprocessor*"

echo ""
echo "Download complete: $LOCAL_DIR"
echo ""
echo "Verify integrity:"
echo "  python -c \""
echo "  from transformers import AutoProcessor, Qwen2_5_VLForConditionalGeneration"
echo "  p = AutoProcessor.from_pretrained('$LOCAL_DIR')"
echo "  print('Processor OK:', type(p).__name__)"
echo "  \""
echo ""
echo "Next: copy $LOCAL_DIR to the air-gapped server, set in .env:"
echo "  XRAY_VLM_BACKEND=transformers"
echo "  XRAY_VLM_MODEL_PATH=$LOCAL_DIR"
echo "  HF_HUB_OFFLINE=1"
echo "  TRANSFORMERS_OFFLINE=1"
