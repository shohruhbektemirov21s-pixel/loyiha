#!/usr/bin/env bash
# ============================================================
# vast.ai — find the cheapest suitable GPU and rent it for
# Qwen3-VL-8B (Ollama), fully from the CLI (no browser).
# ============================================================
# Requires: vastai CLI (pip install --user vastai) + your API key.
#
# Step 1 — set your key (once):
#   export VAST_API_KEY=<your key>          # or: vastai set api-key <key>
#
# Step 2 — SEARCH (safe, read-only) — see cheapest options:
#   ./deploy/vast_rent.sh search
#
# Step 3 — RENT the cheapest match + auto-install the model:
#   ./deploy/vast_rent.sh create            # picks cheapest from the search
#   ./deploy/vast_rent.sh create <OFFER_ID> # or rent a specific offer id
#
# After it boots, check progress:
#   vastai logs <INSTANCE_ID>
#   vastai ssh-url <INSTANCE_ID>
#
set -euo pipefail
export PATH="$HOME/.local/bin:$PATH"

MODEL="${MODEL:-qwen3-vl:8b}"
# GPUs that comfortably fit an 8B vision model. Cheapest-first.
# 24GB (3090/4090) ideal; 16GB cards (A4000/4060Ti-16/4070TiSuper) as budget.
GPU_FILTER="${GPU_FILTER:-gpu_name in [RTX_3090,RTX_4090,RTX_A4000,RTX_4060_Ti,RTX_4070_Ti_Super]}"
MIN_VRAM_GB="${MIN_VRAM_GB:-16}"
MIN_DISK_GB="${MIN_DISK_GB:-40}"
MIN_INET="${MIN_INET:-100}"        # Mbps download, for fast model pull
IMAGE="${IMAGE:-ollama/ollama:latest}"   # ollama preinstalled as entrypoint

QUERY="$GPU_FILTER gpu_ram>=$((MIN_VRAM_GB*1000)) disk_space>=$MIN_DISK_GB inet_down>=$MIN_INET num_gpus=1 rentable=true verified=true"

# onstart: pull + warm the model, then keep ollama serving.
read -r -d '' ONSTART <<EOF || true
export OLLAMA_HOST=0.0.0.0:11434
ollama serve > /var/log/ollama.log 2>&1 &
sleep 5
ollama pull ${MODEL}
ollama run ${MODEL} "Reply READY" || true
echo "=== ${MODEL} ready ===" >> /var/log/ollama.log
EOF

cmd="${1:-search}"

case "$cmd" in
  search)
    echo "[vast] Query: $QUERY"
    echo "[vast] Cheapest matching offers (\$/hr ascending):"
    echo ""
    vastai search offers "$QUERY" -o 'dph+' \
      | head -20
    echo ""
    echo "Rent the cheapest with:  ./deploy/vast_rent.sh create"
    echo "Or a specific one with:  ./deploy/vast_rent.sh create <OFFER_ID>"
    ;;

  create)
    OFFER_ID="${2:-}"
    if [ -z "$OFFER_ID" ]; then
      echo "[vast] No offer id given — picking the cheapest match…"
      OFFER_ID=$(vastai search offers "$QUERY" -o 'dph+' --raw \
        | python3 -c 'import sys,json; d=json.load(sys.stdin); print(d[0]["id"]) if d else sys.exit("no offers")')
      echo "[vast] Cheapest offer id: $OFFER_ID"
    fi
    echo "[vast] Renting offer $OFFER_ID with image $IMAGE …"
    vastai create instance "$OFFER_ID" \
      --image "$IMAGE" \
      --disk "$MIN_DISK_GB" \
      --env '-p 11434:11434' \
      --onstart-cmd "$ONSTART" \
      --ssh --direct
    echo ""
    echo "[vast] Instance requested. Watch it come up:"
    echo "   vastai show instances"
    echo "   vastai logs <INSTANCE_ID>      # wait for '=== ${MODEL} ready ==='"
    echo "   vastai ssh-url <INSTANCE_ID>"
    ;;

  *)
    echo "Usage: $0 {search|create [OFFER_ID]}"; exit 1;;
esac
