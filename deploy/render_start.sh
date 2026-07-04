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
# Render backend Vast GPU'dagi Ollama'ga (11434) SSH tunnel orqali ulanadi.
# Bu bosqichsiz VLM (rasm "Tahlil") ishlamaydi. Shuning uchun aniq log +
# ExitOnForwardFailure (jim muvaffaqiyatsizlikni oldini oladi) + keepalive +
# qayta urinish qo'yildi.
if [ -n "${SSH_PRIVATE_KEY:-}" ]; then
    echo "[ssh] SSH key sozlanmoqda..."
    mkdir -p ~/.ssh
    KEY=~/.ssh/vast_tunnel
    # printf key format (oxirgi newline'ni saqlaydi — OpenSSH buni talab qiladi).
    printf '%s\n' "$SSH_PRIVATE_KEY" > "$KEY"
    chmod 600 "$KEY"

    SSH_HOST="${VAST_SSH_HOST:-ssh5.vast.ai}"
    SSH_PORT="${VAST_SSH_PORT:-38056}"

    echo "[ssh] Tunnel ochilmoqda ${SSH_HOST}:${SSH_PORT} -> ollama:11434 ..."
    tunnel_up=0
    for attempt in 1 2 3; do
        pkill -f "11434:localhost:11434" 2>/dev/null || true
        # -i: aniq kalit | ExitOnForwardFailure: forward bog'lanmasa darhol xato |
        # ServerAlive*: tunnel uzilmasin | -N -f: uvicorn'dan oldin fon'ga o'tsin.
        ssh -i "$KEY" \
            -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
            -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 -o ServerAliveCountMax=3 \
            -o ConnectTimeout=15 \
            -p "$SSH_PORT" "root@${SSH_HOST}" \
            -L 11434:localhost:11434 -N -f \
            && echo "[ssh] tunnel jarayoni fon'ga o'tdi (urinish ${attempt})" \
            || echo "[ssh] urinish ${attempt}: ssh ulanmadi"
        sleep 3
        if curl -fsS --max-time 6 http://127.0.0.1:11434/api/version >/dev/null 2>&1; then
            echo "[ssh] ✅ Ollama tunnel ishlayapti (urinish ${attempt})."
            tunnel_up=1
            break
        fi
        echo "[ssh] urinish ${attempt}: ollama hali javob bermayapti, qayta urinaman..."
        sleep 3
    done
    if [ "$tunnel_up" != "1" ]; then
        echo "[ssh] ⚠️  OGOHLANTIRISH: Ollama tunnel OCHILMADI — VLM (rasm Tahlili) ishlamaydi." >&2
        echo "[ssh]     Tekshiring: (1) Render'da SSH_PRIVATE_KEY to'g'ri kalitmi," >&2
        echo "[ssh]     (2) Vast instance ${VAST_INSTANCE_ID:-?} 'running' holatidami," >&2
        echo "[ssh]     (3) Vast box'da 'ollama serve' ishlab turibdimi." >&2
    fi
else
    echo "[ssh] ⚠️  SSH_PRIVATE_KEY sozlanmagan — Vast ollama tunnel yo'q, VLM ishlamaydi." >&2
fi

# ── 3. Database migratsiyalari ────────────────────────────────
# Migratsiya MAJBURIY: schema yaratilmasa ilova ishlamaydi, shuning uchun
# xatoni yutib yubormaymiz — deploy shu yerda to'xtaydi va log aniq bo'ladi.
# Admin faqat migratsiya muvaffaqiyatli o'tgandagina yaratiladi.
if [ -n "${XRAY_DB_URL:-}" ]; then
    echo "[db] Migratsiyalar ishga tushirilmoqda..."
    if ! python3 -m app.db.migrate; then
        echo "[db] FATAL: migratsiya muvaffaqiyatsiz — deploy to'xtatildi." >&2
        exit 1
    fi
    echo "[db] ✅ Migratsiya tugadi."

    # Admin foydalanuvchi yaratish (idempotent — ON CONFLICT DO UPDATE)
    if [ -n "${ADMIN_USERNAME:-}" ] && [ -n "${ADMIN_PASSWORD:-}" ]; then
        echo "[db] Admin foydalanuvchi yaratilmoqda..."
        ADMIN_USERNAME="$ADMIN_USERNAME" ADMIN_PASSWORD="$ADMIN_PASSWORD" \
            python3 deploy/create_admin.py --lane-ids "lane-1,lane-2" \
            || echo "[db] Warning: admin yaratish xatoligi (server baribir ishga tushadi)"
    else
        echo "[db] ADMIN_USERNAME/ADMIN_PASSWORD sozlanmagan — admin yaratilmadi."
    fi
else
    echo "[db] XRAY_DB_URL sozlanmagan. Stub rejim."
fi

# ── 4. FastAPI serverni ishga tushirish ───────────────────────
echo "[app] FastAPI server port ${PORT:-10000} da ishga tushmoqda..."
exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-10000}"
