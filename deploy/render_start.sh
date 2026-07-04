#!/usr/bin/env bash
# ============================================================
# X-ray Assistant — Render Start Script
# Avtomatik: Vast.ai GPU yoqish + SSH tunnel + DB + Server
# ============================================================
set -euo pipefail

# ── 1. Vast.ai GPU'ni avtomatik yoqish (API orqali) ──────────
VAST_API_KEY="${VAST_API_KEY:-}"
VAST_INSTANCE_ID="${VAST_INSTANCE_ID:-43598057}"

if [ -n "$VAST_API_KEY" ] && [ -n "$VAST_INSTANCE_ID" ]; then
    echo "[vast] GPU instance ${VAST_INSTANCE_ID} yoqilmoqda..."
    
    # Instance'ni start qilish (REST API)
    curl -s -X PUT "https://console.vast.ai/api/v0/instances/${VAST_INSTANCE_ID}/" \
        -H "Authorization: Bearer ${VAST_API_KEY}" \
        -H "Content-Type: application/json" \
        -d '{"state": "running"}' || echo "[vast] Warning: GPU start so'rovi yuborilmadi"
    
    # GPU yuklanishini kutish (max 90 soniya)
    echo "[vast] GPU yuklanmoqda (max 90s)..."
    for i in $(seq 1 30); do
        STATUS=$(curl -s "https://console.vast.ai/api/v0/instances/${VAST_INSTANCE_ID}/" \
            -H "Authorization: Bearer ${VAST_API_KEY}" 2>/dev/null \
            | python3 -c "import sys,json; print(json.load(sys.stdin).get('actual_status','unknown'))" 2>/dev/null || echo "unknown")
        
        if [ "$STATUS" = "running" ]; then
            echo "[vast] ✅ GPU ishlayapti!"
            break
        fi
        printf "."
        sleep 3
    done
    echo ""
else
    echo "[vast] VAST_API_KEY yoki VAST_INSTANCE_ID sozlanmagan. GPU avtomatik yoqilmaydi."
fi

# ── 2. SSH tunnel ochish (Vast.ai GPU'ga) ─────────────────────
if [ -n "${SSH_PRIVATE_KEY:-}" ]; then
    echo "[ssh] SSH key sozlanmoqda..."
    mkdir -p ~/.ssh
    echo "$SSH_PRIVATE_KEY" > ~/.ssh/id_rsa
    chmod 600 ~/.ssh/id_rsa
    
    SSH_HOST="${VAST_SSH_HOST:-ssh5.vast.ai}"
    SSH_PORT="${VAST_SSH_PORT:-38056}"
    
    echo "[ssh] Tunnel ochilmoqda ${SSH_HOST}:${SSH_PORT}..."
    ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
        -p "$SSH_PORT" "root@${SSH_HOST}" \
        -L 11434:localhost:11434 -N -f 2>/dev/null || echo "[ssh] Warning: SSH tunnel ochilmadi"
    
    # Tunnel tekshirish
    sleep 2
    if curl -s --max-time 5 http://127.0.0.1:11434/api/version >/dev/null 2>&1; then
        echo "[ssh] ✅ Ollama tunnel ishlayapti!"
    else
        echo "[ssh] ⏳ Tunnel ochildi, Ollama hali javob bermayapti"
    fi
fi

# ── 3. Database migratsiyalari ────────────────────────────────
if [ -n "${XRAY_DB_URL:-}" ]; then
    echo "[db] Migratsiyalar ishga tushirilmoqda..."
    python3 -m app.db.migrate || echo "[db] Warning: migratsiya xatoligi"
    
    # Admin foydalanuvchi yaratish
    if [ -n "${ADMIN_USERNAME:-}" ] && [ -n "${ADMIN_PASSWORD:-}" ]; then
        echo "[db] Admin foydalanuvchi yaratilmoqda..."
        ADMIN_USERNAME="$ADMIN_USERNAME" ADMIN_PASSWORD="$ADMIN_PASSWORD" \
            python3 deploy/create_admin.py --lane-ids "lane-1,lane-2" || echo "[db] Warning: admin yaratish xatoligi"
    fi
else
    echo "[db] XRAY_DB_URL sozlanmagan. Stub rejim."
fi

# ── 4. FastAPI serverni ishga tushirish ───────────────────────
echo "[app] FastAPI server port ${PORT:-10000} da ishga tushmoqda..."
exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-10000}"
