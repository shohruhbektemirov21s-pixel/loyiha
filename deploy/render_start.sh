#!/usr/bin/env bash
# ============================================================
# X-ray Assistant — Render Start Script
# Avtomatik: Vast.ai GPU yoqish + SSH tunnel + DB + Server
#
# Tunnel MANGU emas: Vast instance boot vaqtida hali ko'tarilmagan
# bo'lishi yoki keyin uzilishi mumkin. Shuning uchun tunnel'ni bir marta
# ochib qo'ymaymiz — fon'da watchdog uni uzluksiz kuzatib, uzilsa
# (yoki umuman ochilmagan bo'lsa) Vast'ni qayta yoqib, tunnel'ni tiklaydi.
# Bu bosqichsiz VLM (rasm "Tahlil") ishlamaydi; watchdog uni o'z-o'zidan
# tiklaganda operatorga qayta redeploy shart bo'lmaydi.
# ============================================================
set -euo pipefail

VAST_API_KEY="${VAST_API_KEY:-}"
VAST_INSTANCE_ID="${VAST_INSTANCE_ID:-43598057}"
SSH_HOST="${VAST_SSH_HOST:-ssh5.vast.ai}"
SSH_PORT="${VAST_SSH_PORT:-38056}"
MODEL="${XRAY_VLM_MODEL:-qwen3-vl:8b}"
KEY=~/.ssh/vast_tunnel
# Watchdog qanchalik tez-tez tekshirsin (soniya).
TUNNEL_WATCH_INTERVAL="${TUNNEL_WATCH_INTERVAL:-30}"

# ── Yordamchi: Ollama tunnel orqali javob berayaptimi? ────────
ollama_reachable() {
    curl -fsS --max-time 6 http://127.0.0.1:11434/api/version >/dev/null 2>&1
}

# ── Vast API'dan instance'ni bir marta so'rab, "status|ssh_host|ssh_port" ─
# qaytaradi. SSH endpoint MUHIM: Vast instance to'xtatilib qayta yoqilganda
# ssh_host/ssh_port O'ZGARADI — shuning uchun uni qattiq yozib qo'ymay, har
# safar API'dan o'qiymiz (aks holda eski endpoint'ga ulanib tunnel yiqiladi).
vast_query() {
    curl -s "https://console.vast.ai/api/v0/instances/${VAST_INSTANCE_ID}/" \
        -H "Authorization: Bearer ${VAST_API_KEY}" 2>/dev/null \
        | python3 -c '
import sys, json
try:
    d = json.load(sys.stdin)
except Exception:
    print("unknown||"); sys.exit(0)
# Javob root da yoki {"instances": {...}} ichida bo\x27lishi mumkin.
inst = d.get("instances", d) if isinstance(d, dict) else {}
if isinstance(inst, list):
    inst = inst[0] if inst else {}
status = inst.get("actual_status", "unknown")
host = inst.get("ssh_host", "") or ""
port = inst.get("ssh_port", "") or ""
print(f"{status}|{host}|{port}")
' 2>/dev/null || echo "unknown||"
}

# ── 1. Vast.ai GPU'ni yoqilganiga ishonch hosil qilish ────────
# API orqali instance'ni 'running' holatiga o'tkazadi va (max 90s) kutadi.
# Running bo'lgach, joriy SSH endpoint'ni (host/port) global'ga yozadi.
# Idempotent: allaqachon ishlab tursa, tez qaytadi.
vast_ensure_running() {
    [ -n "$VAST_API_KEY" ] && [ -n "$VAST_INSTANCE_ID" ] || {
        echo "[vast] VAST_API_KEY yoki VAST_INSTANCE_ID sozlanmagan — GPU yoqilmaydi."
        return 1
    }
    echo "[vast] GPU instance ${VAST_INSTANCE_ID} 'running' holatiga o'tkazilmoqda..."
    curl -s -X PUT "https://console.vast.ai/api/v0/instances/${VAST_INSTANCE_ID}/" \
        -H "Authorization: Bearer ${VAST_API_KEY}" \
        -H "Content-Type: application/json" \
        -d '{"state": "running"}' >/dev/null 2>&1 || echo "[vast] Warning: start so'rovi yuborilmadi"

    for _ in $(seq 1 30); do
        IFS='|' read -r STATUS API_HOST API_PORT <<< "$(vast_query)" || true
        if [ "$STATUS" = "running" ]; then
            # API bergan joriy endpoint'ni ishlatamiz (o'zgargan bo'lishi mumkin).
            [ -n "$API_HOST" ] && SSH_HOST="$API_HOST"
            [ -n "$API_PORT" ] && SSH_PORT="$API_PORT"
            echo "[vast] ✅ GPU ishlayapti! SSH endpoint: ${SSH_HOST}:${SSH_PORT}"
            return 0
        fi
        printf "."
        sleep 3
    done
    echo ""
    echo "[vast] ⚠️  GPU 90s ichida 'running' bo'lmadi (holat: ${STATUS:-unknown})."
    return 1
}

# ── 2. Vast box'da ollama'ni ta'minlab, SSH tunnel'ni ochish ──
# Self-healing: instance qayta yoqilganda 'ollama serve' o'zi qaytmasligi
# mumkin; kerak bo'lsa remote'da qayta ishga tushiramiz va model yo'q bo'lsa
# fon'da yuklab olamiz. So'ng lokal 11434 -> remote 11434 tunnel ochamiz.
tunnel_ensure() {
    [ -n "${SSH_PRIVATE_KEY:-}" ] || {
        echo "[ssh] ⚠️  SSH_PRIVATE_KEY sozlanmagan — tunnel yo'q, VLM ishlamaydi." >&2
        return 1
    }
    if [ ! -f "$KEY" ]; then
        mkdir -p ~/.ssh
        printf '%s\n' "$SSH_PRIVATE_KEY" > "$KEY"
        chmod 600 "$KEY"
    fi

    echo "[ssh] Vast box'da ollama holati tekshirilmoqda..."
    ssh -i "$KEY" -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
        -o ConnectTimeout=15 -p "$SSH_PORT" "root@${SSH_HOST}" \
        "MODEL='$MODEL' bash -s" <<'REMOTE' 2>&1 | sed 's/^/[vast] /' || echo "[ssh] remote ensure: ulanmadi (kalit/instance holatini tekshiring)"
set +e
if ! curl -fsS --max-time 5 http://127.0.0.1:11434/api/version >/dev/null 2>&1; then
    echo "ollama ishlamayapti — ishga tushirilmoqda..."
    pkill -f "ollama serve" 2>/dev/null; sleep 1
    OLLAMA_HOST=0.0.0.0:11434 OLLAMA_NO_ANALYTICS=1 nohup ollama serve >/var/log/ollama.log 2>&1 &
    for i in $(seq 1 30); do
        curl -fsS --max-time 3 http://127.0.0.1:11434/api/version >/dev/null 2>&1 && { echo "ollama ishga tushdi"; break; }
        sleep 1
    done
else
    echo "ollama allaqachon ishlayapti"
fi
if OLLAMA_HOST=127.0.0.1:11434 ollama ls 2>/dev/null | grep -q "${MODEL%%:*}"; then
    echo "model $MODEL mavjud"
else
    echo "model $MODEL YO'Q — fon'da yuklab olinmoqda (bir necha daqiqa)..."
    OLLAMA_HOST=127.0.0.1:11434 nohup ollama pull "$MODEL" >/var/log/ollama-pull.log 2>&1 &
fi
REMOTE

    echo "[ssh] Tunnel ochilmoqda ${SSH_HOST}:${SSH_PORT} -> ollama:11434 ..."
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
        if ollama_reachable; then
            echo "[ssh] ✅ Ollama tunnel ishlayapti (urinish ${attempt})."
            return 0
        fi
        echo "[ssh] urinish ${attempt}: ollama hali javob bermayapti, qayta urinaman..."
        sleep 3
    done
    echo "[ssh] ⚠️  Tunnel bu urinishda ochilmadi — watchdog qayta urinadi." >&2
    return 1
}

# ── Bitta to'liq tiklash sikli: Vast'ni yoqish + tunnel ochish ─
recover_vlm_link() {
    vast_ensure_running || true
    tunnel_ensure || true
}

# ── Fon watchdog: tunnel'ni uzluksiz tirik ushlab turadi ──────
# Har TUNNEL_WATCH_INTERVAL soniyada Ollama'ni tekshiradi; javob bermasa
# to'liq tiklash siklini qayta yuritadi (Vast o'chib qolgan bo'lsa yoqadi,
# ollama tushib qolgan bo'lsa ko'taradi, tunnel uzilgan bo'lsa qayta ochadi).
tunnel_watchdog() {
    while true; do
        sleep "$TUNNEL_WATCH_INTERVAL"
        if ! ollama_reachable; then
            echo "[watchdog] Ollama tunnel javob bermayapti — tiklanmoqda..." >&2
            recover_vlm_link
        fi
    done
}

# ── Boot: bir marta tiklash + doimiy watchdog'ni fon'ga qo'yish ─
recover_vlm_link
if ollama_reachable; then
    echo "[ssh] ✅ VLM link boot'da tayyor."
else
    echo "[ssh] ⚠️  OGOHLANTIRISH: VLM link boot'da ochilmadi — watchdog fon'da tiklashga urinadi." >&2
    echo "[ssh]     Tekshiring: (1) Render'da SSH_PRIVATE_KEY to'g'ri kalitmi," >&2
    echo "[ssh]     (2) Vast instance ${VAST_INSTANCE_ID:-?} 'running' holatidami," >&2
    echo "[ssh]     (3) VAST_API_KEY to'g'rimi (auto-start shu bilan ishlaydi)." >&2
fi
tunnel_watchdog &
echo "[watchdog] Tunnel watchdog fon'da ishga tushdi (har ${TUNNEL_WATCH_INTERVAL}s)."

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
