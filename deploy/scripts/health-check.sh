#!/usr/bin/env bash
# health-check.sh — pre-flight / operational health check.
# Run before shift start or after any maintenance.
# Exits non-zero if any critical check fails.

set -euo pipefail

DEPLOY_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
[[ -f "${DEPLOY_DIR}/.env" ]] && source "${DEPLOY_DIR}/.env"

PASS=0; WARN=0; FAIL=0
RESULTS=()

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'

ok()   { PASS=$((PASS+1));  RESULTS+=("${GREEN}PASS${NC}  $*"); }
warn() { WARN=$((WARN+1));  RESULTS+=("${YELLOW}WARN${NC}  $*"); }
fail() { FAIL=$((FAIL+1));  RESULTS+=("${RED}FAIL${NC}  $*"); }

check_http() {
    local name="$1" url="$2" pattern="${3:-ok}"
    local resp
    resp=$(wget -qO- --timeout=5 "$url" 2>/dev/null || echo "")
    if echo "$resp" | grep -q "$pattern"; then
        ok "$name ($url)"
    else
        fail "$name — not healthy ($url)"
    fi
}

check_container() {
    local name="$1"
    local state
    state=$(docker compose -f "${DEPLOY_DIR}/docker-compose.yml" ps --format json "$name" 2>/dev/null \
            | python3 -c "import sys,json; d=json.load(sys.stdin); print(d[0].get('Health','unknown'))" 2>/dev/null || echo "missing")
    case "$state" in
        healthy)   ok "Container $name: healthy" ;;
        unhealthy) fail "Container $name: UNHEALTHY" ;;
        starting)  warn "Container $name: still starting" ;;
        *)         fail "Container $name: $state" ;;
    esac
}

echo "========================================"
echo " X-ray assistant health check"
echo " $(date -u)"
echo "========================================"

# ── Containers ───────────────────────────────────────────────────────────────
for svc in postgres api detector vlm console nginx prometheus grafana acquisition; do
    check_container "$svc"
done

# ── API endpoints ────────────────────────────────────────────────────────────
check_http "API health"      "http://localhost:8000/health" '"ok"'
check_http "Prometheus"      "http://localhost:9090/-/healthy" "Healthy"
check_http "Grafana"         "http://localhost:3000/api/health" "ok"
check_http "Alertmanager"    "http://localhost:9093/-/healthy" "OK"

# ── Disk space ───────────────────────────────────────────────────────────────
for mount in / /var/lib/xray/store /var/lib/xray/models; do
    [[ -d "$mount" ]] || continue
    PCT=$(df "$mount" | awk 'NR==2{print $5}' | tr -d '%')
    if [[ "$PCT" -lt 80 ]]; then
        ok "Disk $mount: ${PCT}% used"
    elif [[ "$PCT" -lt 95 ]]; then
        warn "Disk $mount: ${PCT}% used — monitor"
    else
        fail "Disk $mount: ${PCT}% used — CRITICAL"
    fi
done

# ── GPU availability ─────────────────────────────────────────────────────────
if command -v nvidia-smi >/dev/null 2>&1; then
    if nvidia-smi >/dev/null 2>&1; then
        GPU_MEM=$(nvidia-smi --query-gpu=memory.used,memory.total --format=csv,noheader,nounits | head -1)
        ok "GPU available: ${GPU_MEM} MiB used/total"
    else
        fail "GPU: nvidia-smi failed"
    fi
else
    warn "GPU: nvidia-smi not found (CPU-only mode?)"
fi

# ── Model weight integrity ───────────────────────────────────────────────────
MODEL_REGISTRY="/var/lib/xray/models/registry.json"
if [[ -f "$MODEL_REGISTRY" ]]; then
    python3 - <<PYEOF
import json, sys, hashlib, os

with open("${MODEL_REGISTRY}") as f:
    registry = json.load(f)

errors = []
for mtype, info in registry.get("models", {}).items():
    path = f"/var/lib/xray/models/{info['file']}"
    if not os.path.exists(path):
        errors.append(f"{mtype}: file missing ({info['file']})")
        continue
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    actual = h.hexdigest()
    if actual != info["sha256"]:
        errors.append(f"{mtype}: SHA-256 MISMATCH (expected {info['sha256'][:12]}… got {actual[:12]}…)")

if errors:
    for e in errors:
        print(f"FAIL  Model integrity: {e}")
    sys.exit(1)
else:
    for mtype in registry.get("models", {}):
        print(f"PASS  Model integrity: {mtype} OK")
PYEOF
    if [[ $? -eq 0 ]]; then
        ok "All model weights verified"
    else
        fail "Model weight integrity FAILED"
    fi
else
    warn "Model registry not found — skipping weight verification"
fi

# ── Backup freshness ─────────────────────────────────────────────────────────
if [[ -d "${BACKUP_DEST:-/mnt/backup-nas/xray}" ]]; then
    LATEST=$(ls -td "${BACKUP_DEST}"/*T*Z 2>/dev/null | head -1)
    if [[ -n "$LATEST" ]]; then
        AGE_H=$(( ($(date +%s) - $(stat -c %Y "$LATEST")) / 3600 ))
        if [[ $AGE_H -lt 26 ]]; then
            ok "Last backup: ${AGE_H}h ago ($(basename "$LATEST"))"
        else
            warn "Last backup: ${AGE_H}h ago — backup may be stale"
        fi
    else
        warn "No backups found in ${BACKUP_DEST}"
    fi
else
    warn "Backup destination not mounted — skipping backup check"
fi

# ── Audit chain ──────────────────────────────────────────────────────────────
AUDIT_RESP=$(wget -qO- --header="Authorization: Bearer $(cat /etc/xray/healthcheck.token 2>/dev/null || echo '')" \
             http://localhost:8000/v1/admin/audit/verify 2>/dev/null || echo "")
if echo "$AUDIT_RESP" | python3 -c "import sys,json; d=json.load(sys.stdin); sys.exit(0 if d.get('valid') else 1)" 2>/dev/null; then
    ok "Audit chain: valid"
else
    warn "Audit chain: could not verify (may need admin token)"
fi

# ── Results summary ──────────────────────────────────────────────────────────
echo ""
echo "========================================"
for r in "${RESULTS[@]}"; do echo -e "  $r"; done
echo "========================================"
echo -e "  ${GREEN}PASS: ${PASS}${NC}  ${YELLOW}WARN: ${WARN}${NC}  ${RED}FAIL: ${FAIL}${NC}"
echo "========================================"

[[ $FAIL -eq 0 ]]   || { echo -e "${RED}HEALTH CHECK FAILED — do not clear traffic.${NC}"; exit 1; }
[[ $WARN -eq 0 ]]   && echo -e "${GREEN}All checks passed.${NC}"
[[ $WARN -gt 0 ]]   && echo -e "${YELLOW}Warnings present — investigate before shift start.${NC}"
exit 0
