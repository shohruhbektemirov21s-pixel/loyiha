#!/usr/bin/env bash
# ============================================================
# X-ray Assistant — to'liq ishga tushirish skripti
# ============================================================
# Bu skript:
#   1. Vast.ai GPU ni yoqadi (kerak bo'lsa)
#   2. SSH tunnel ochadi
#   3. API serverni ishga tushiradi
#   4. Console (frontend) ni ishga tushiradi
#
# Usage:
#   ./deploy/start_all.sh          # Hammasi (GPU + API + Console)
#   ./deploy/start_all.sh api      # Faqat API (GPU siz, VLM ishlamaydi)
#   ./deploy/start_all.sh stop     # Hammasini to'xtatish
# ============================================================
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_DIR"

API_PORT="${XRAY_PORT:-8001}"
CONSOLE_PORT=5173
INSTANCE_ID="${VAST_INSTANCE_ID:-43598057}"
SSH_HOST="${VAST_SSH_HOST:-ssh5.vast.ai}"
SSH_PORT="${VAST_SSH_PORT:-38056}"

GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m'

stop_all() {
    echo -e "${RED}[xray] Hammasini to'xtatish…${NC}"
    # API
    pkill -f "uvicorn app.main" 2>/dev/null || true
    # Console
    pkill -f "vite.*console" 2>/dev/null || true
    # SSH tunnel
    pkill -f "ssh.*${SSH_HOST}.*11434" 2>/dev/null || true
    # Vast.ai
    if command -v vastai >/dev/null 2>&1; then
        echo -e "${YELLOW}[xray] Vast.ai instance to'xtatilmoqda…${NC}"
        vastai stop instance "$INSTANCE_ID" 2>/dev/null || true
    fi
    echo -e "${GREEN}[xray] ✅ Hammasi to'xtatildi. Pul yechilmaydi.${NC}"
}

start_gpu() {
    echo -e "${GREEN}[xray] 🖥️  Vast.ai GPU yoqilmoqda…${NC}"
    if ! command -v vastai >/dev/null 2>&1; then
        echo -e "${RED}[xray] vastai CLI topilmadi. 'pip install vastai' bilan o'rnating.${NC}"
        return 1
    fi
    
    vastai start instance "$INSTANCE_ID" 2>/dev/null || true
    
    echo -e "${YELLOW}[xray] Instance yuklanmoqda (60 soniyagacha)…${NC}"
    for i in $(seq 1 30); do
        STATUS=$(vastai show instance "$INSTANCE_ID" --raw 2>/dev/null \
            | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('actual_status','unknown'))" 2>/dev/null || echo "unknown")
        if [ "$STATUS" = "running" ]; then
            echo -e "${GREEN}[xray] ✅ GPU ishlayapti!${NC}"
            break
        fi
        printf "."
        sleep 3
    done
    echo ""
    
    # SSH tunnel
    pkill -f "ssh.*${SSH_HOST}.*11434" 2>/dev/null || true
    sleep 1
    echo -e "${GREEN}[xray] SSH tunnel ochilmoqda…${NC}"
    ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
        -p "$SSH_PORT" "root@${SSH_HOST}" \
        -L 11434:localhost:11434 -N -f 2>/dev/null
    
    sleep 3
    if curl -s --max-time 5 http://127.0.0.1:11434/api/version >/dev/null 2>&1; then
        echo -e "${GREEN}[xray] ✅ Ollama tunnel ishlayapti!${NC}"
    else
        echo -e "${YELLOW}[xray] ⏳ Ollama hali yuklanmoqda, bir oz kuting…${NC}"
    fi
}

start_api() {
    echo -e "${GREEN}[xray] 🚀 API server ishga tushmoqda (port ${API_PORT})…${NC}"
    pkill -f "uvicorn app.main" 2>/dev/null || true
    sleep 1
    
    cd "$PROJECT_DIR"
    nohup python3 -m uvicorn app.main:app \
        --host 0.0.0.0 --port "$API_PORT" \
        > "$PROJECT_DIR/_api_server.log" 2>&1 &
    API_PID=$!
    echo -e "${GREEN}[xray] API PID: $API_PID${NC}"
    
    sleep 6
    if curl -s --max-time 5 "http://127.0.0.1:${API_PORT}/health" >/dev/null 2>&1; then
        echo -e "${GREEN}[xray] ✅ API ishlayapti: http://127.0.0.1:${API_PORT}/docs${NC}"
    else
        echo -e "${YELLOW}[xray] API log:${NC}"
        tail -5 "$PROJECT_DIR/_api_server.log" 2>/dev/null || true
    fi
}

start_console() {
    echo -e "${GREEN}[xray] 🖥️  Console ishga tushmoqda…${NC}"
    cd "$PROJECT_DIR/console"
    
    if [ ! -d "node_modules" ]; then
        echo -e "${YELLOW}[xray] node_modules topilmadi, npm install…${NC}"
        npm install 2>/dev/null
    fi
    
    nohup npm run dev > "$PROJECT_DIR/_console.log" 2>&1 &
    CONSOLE_PID=$!
    echo -e "${GREEN}[xray] Console PID: $CONSOLE_PID${NC}"
    
    sleep 4
    echo -e "${GREEN}[xray] ✅ Console: http://localhost:${CONSOLE_PORT}${NC}"
}

# ── Main ─────────────────────────────────────────────────────
cmd="${1:-all}"

case "$cmd" in
    all)
        echo -e "${GREEN}============================================${NC}"
        echo -e "${GREEN}  X-ray Assistant — to'liq ishga tushirish${NC}"
        echo -e "${GREEN}============================================${NC}"
        start_gpu
        start_api
        start_console
        echo ""
        echo -e "${GREEN}============================================${NC}"
        echo -e "${GREEN}  ✅ TAYYOR!${NC}"
        echo -e "${GREEN}  API:     http://127.0.0.1:${API_PORT}/docs${NC}"
        echo -e "${GREEN}  Console: http://localhost:${CONSOLE_PORT}${NC}"
        echo -e "${GREEN}  VLM:     qwen3-vl:8b via vast.ai GPU${NC}"
        echo -e "${GREEN}============================================${NC}"
        echo -e "${YELLOW}  O'chirish: ./deploy/start_all.sh stop${NC}"
        ;;
    api)
        start_api
        ;;
    gpu)
        start_gpu
        ;;
    console)
        start_console
        ;;
    stop)
        stop_all
        ;;
    *)
        echo "Usage: $0 {all|api|gpu|console|stop}"
        echo ""
        echo "  all     — GPU + API + Console (to'liq)"
        echo "  api     — Faqat API (GPU siz)"
        echo "  gpu     — Faqat GPU + SSH tunnel"
        echo "  console — Faqat Console"
        echo "  stop    — Hammasini to'xtatish"
        ;;
esac
