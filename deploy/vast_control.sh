#!/usr/bin/env bash
# ============================================================
# vast.ai Instance Controller — start/stop/status
# ============================================================
# Faqat kerak bo'lganda GPU ni yoqadi, qolgan vaqtda o'chiradi.
# Pul faqat ishlatilganda yechiladi!
#
# Usage:
#   ./deploy/vast_control.sh start   # GPU ni yoqadi + SSH tunnel ochadi
#   ./deploy/vast_control.sh stop    # GPU ni o'chiradi + tunnel yopadi
#   ./deploy/vast_control.sh status  # Holatni ko'rsatadi
#   ./deploy/vast_control.sh ssh     # SSH bilan kirib ko'rish
# ============================================================
set -euo pipefail

# ── Instance sozlamalari ──────────────────────────────────────
INSTANCE_ID="${VAST_INSTANCE_ID:-43598057}"
SSH_HOST="${VAST_SSH_HOST:-ssh5.vast.ai}"
SSH_PORT="${VAST_SSH_PORT:-38056}"
DIRECT_HOST="${VAST_DIRECT_HOST:-115.73.216.179}"
DIRECT_PORT="${VAST_DIRECT_PORT:-41846}"
LOCAL_OLLAMA_PORT=11434

# ── Ranglar ──────────────────────────────────────────────────
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m'

# ── Tunnel boshqaruvi ────────────────────────────────────────
kill_tunnel() {
    # Kill any existing SSH tunnels to vast.ai
    pkill -f "ssh.*${SSH_HOST}.*${LOCAL_OLLAMA_PORT}" 2>/dev/null || true
    pkill -f "ssh.*${DIRECT_HOST}.*${LOCAL_OLLAMA_PORT}" 2>/dev/null || true
    echo -e "${YELLOW}[vast] SSH tunnel yopildi${NC}"
}

open_tunnel() {
    kill_tunnel
    echo -e "${GREEN}[vast] SSH tunnel ochilmoqda (port ${LOCAL_OLLAMA_PORT})…${NC}"
    ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
        -p "${SSH_PORT}" "root@${SSH_HOST}" \
        -L "${LOCAL_OLLAMA_PORT}:localhost:${LOCAL_OLLAMA_PORT}" \
        -N -f 2>/dev/null
    
    # Tunnel ishlayotganini tekshirish
    sleep 2
    if curl -s --max-time 5 "http://127.0.0.1:${LOCAL_OLLAMA_PORT}/api/version" >/dev/null 2>&1; then
        echo -e "${GREEN}[vast] ✅ Tunnel ishlayapti! Ollama tayyor.${NC}"
        return 0
    else
        echo -e "${YELLOW}[vast] ⏳ Tunnel ochildi, lekin Ollama hali javob bermayapti (instance yuklanmoqda?)${NC}"
        return 1
    fi
}

check_tunnel() {
    if curl -s --max-time 3 "http://127.0.0.1:${LOCAL_OLLAMA_PORT}/api/version" >/dev/null 2>&1; then
        return 0
    else
        return 1
    fi
}

# ── vastai CLI tekshiruvi ────────────────────────────────────
ensure_vastai() {
    if ! command -v vastai >/dev/null 2>&1; then
        echo -e "${YELLOW}[vast] vastai CLI topilmadi. O'rnatilmoqda…${NC}"
        pip install --user vastai >/dev/null 2>&1
        export PATH="$HOME/.local/bin:$PATH"
    fi
}

# ── Asosiy buyruqlar ─────────────────────────────────────────
cmd="${1:-status}"

case "$cmd" in
    start)
        echo -e "${GREEN}============================================${NC}"
        echo -e "${GREEN}  vast.ai GPU ni yoqish (Instance: ${INSTANCE_ID})${NC}"
        echo -e "${GREEN}============================================${NC}"
        
        ensure_vastai
        
        # Instance ni yoqish
        echo -e "${GREEN}[vast] Instance yoqilmoqda…${NC}"
        vastai start instance "${INSTANCE_ID}" 2>/dev/null || true
        
        # Yuklanishini kutish
        echo -e "${YELLOW}[vast] Instance yuklanmoqda (30-60 soniya)…${NC}"
        for i in $(seq 1 60); do
            STATUS=$(vastai show instance "${INSTANCE_ID}" --raw 2>/dev/null \
                | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('actual_status','unknown'))" 2>/dev/null || echo "unknown")
            
            if [ "$STATUS" = "running" ]; then
                echo -e "${GREEN}[vast] ✅ Instance ishlayapti!${NC}"
                break
            fi
            printf "."
            sleep 3
        done
        echo ""
        
        # SSH tunnel ochish
        sleep 3
        open_tunnel
        
        # Model yuklangan ekanligini tekshirish
        if check_tunnel; then
            echo ""
            MODELS=$(curl -s "http://127.0.0.1:${LOCAL_OLLAMA_PORT}/api/tags" 2>/dev/null \
                | python3 -c "import sys,json; d=json.load(sys.stdin); [print(f'  - {m[\"name\"]} ({m[\"size\"]//1024//1024//1024}GB)') for m in d.get('models',[])]" 2>/dev/null || echo "  (tekshirib bo'lmadi)")
            echo -e "${GREEN}[vast] Yuklangan modellar:${NC}"
            echo "$MODELS"
        fi
        
        echo ""
        echo -e "${GREEN}============================================${NC}"
        echo -e "${GREEN}  ✅ GPU TAYYOR! Narx: ~\$0.136/soat${NC}"
        echo -e "${GREEN}  Ollama: http://127.0.0.1:${LOCAL_OLLAMA_PORT}${NC}"
        echo -e "${GREEN}  O'chirish: ./deploy/vast_control.sh stop${NC}"
        echo -e "${GREEN}============================================${NC}"
        ;;
    
    stop)
        echo -e "${RED}============================================${NC}"
        echo -e "${RED}  vast.ai GPU ni o'chirish${NC}"
        echo -e "${RED}============================================${NC}"
        
        # Tunnel yopish
        kill_tunnel
        
        # Instance ni to'xtatish
        ensure_vastai
        echo -e "${YELLOW}[vast] Instance to'xtatilmoqda…${NC}"
        vastai stop instance "${INSTANCE_ID}" 2>/dev/null || true
        
        echo -e "${GREEN}[vast] ✅ Instance to'xtatildi. Pul yechilmayapti.${NC}"
        echo -e "${GREEN}[vast] Disk saqlash narxi: ~\$0.006/soat${NC}"
        echo ""
        ;;
    
    destroy)
        echo -e "${RED}⚠️  Instance butunlay o'chiriladi! Model qayta yuklanadi!${NC}"
        read -p "Davom etaymi? (y/N): " confirm
        if [ "$confirm" = "y" ] || [ "$confirm" = "Y" ]; then
            kill_tunnel
            ensure_vastai
            vastai destroy instance "${INSTANCE_ID}" 2>/dev/null || true
            echo -e "${GREEN}[vast] Instance o'chirildi.${NC}"
        else
            echo "Bekor qilindi."
        fi
        ;;
    
    status)
        echo -e "${GREEN}============================================${NC}"
        echo -e "${GREEN}  vast.ai Instance holati${NC}"
        echo -e "${GREEN}============================================${NC}"
        
        # Tunnel holati
        if check_tunnel; then
            VERSION=$(curl -s "http://127.0.0.1:${LOCAL_OLLAMA_PORT}/api/version" 2>/dev/null | python3 -c "import sys,json; print(json.load(sys.stdin).get('version','?'))" 2>/dev/null || echo "?")
            echo -e "  SSH Tunnel:  ${GREEN}✅ Ishlayapti${NC}"
            echo -e "  Ollama:      ${GREEN}✅ v${VERSION}${NC}"
        else
            echo -e "  SSH Tunnel:  ${RED}❌ O'chiq${NC}"
            echo -e "  Ollama:      ${RED}❌ Ulanmagan${NC}"
        fi
        
        # vastai CLI orqali instance holati
        if command -v vastai >/dev/null 2>&1; then
            STATUS=$(vastai show instance "${INSTANCE_ID}" --raw 2>/dev/null \
                | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('actual_status','unknown'))" 2>/dev/null || echo "unknown")
            COST=$(vastai show instance "${INSTANCE_ID}" --raw 2>/dev/null \
                | python3 -c "import sys,json; d=json.load(sys.stdin); print(f\"\${d.get('dph_total',0):.3f}/hr\")" 2>/dev/null || echo "?")
            echo -e "  Instance:    ${STATUS}"
            echo -e "  Narx:        ${COST}"
        fi
        
        echo -e "${GREEN}============================================${NC}"
        ;;
    
    ssh)
        echo -e "${GREEN}[vast] SSH bilan kirish…${NC}"
        ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
            -p "${SSH_PORT}" "root@${SSH_HOST}" \
            -L "${LOCAL_OLLAMA_PORT}:localhost:${LOCAL_OLLAMA_PORT}"
        ;;
    
    tunnel)
        open_tunnel
        ;;
    
    *)
        echo "Usage: $0 {start|stop|destroy|status|ssh|tunnel}"
        echo ""
        echo "  start    - GPU ni yoqadi + SSH tunnel ochadi"
        echo "  stop     - GPU ni o'chiradi (pul tejaladi!)"
        echo "  destroy  - Instance butunlay o'chiradi"
        echo "  status   - Holatni ko'rsatadi"
        echo "  ssh      - SSH terminal ochadi"
        echo "  tunnel   - Faqat SSH tunnel ochadi"
        exit 1
        ;;
esac
