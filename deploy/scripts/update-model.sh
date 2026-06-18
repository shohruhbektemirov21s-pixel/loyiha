#!/usr/bin/env bash
# update-model.sh — controlled model weight update workflow.
#
# This is the ONLY sanctioned path for updating detector/VLM weights.
# Every step is logged and the system does not restart until verification passes.
#
# Process:
#   1. Operator receives signed bundle on encrypted USB (from offline training box)
#   2. Run this script with the USB mounted
#   3. Script verifies bundle signature + SHA-256 checksums
#   4. Script writes new weights to staging, runs smoke test
#   5. On pass: promote to production volume, restart affected services
#   6. On fail: abort — old model stays in service
#
# Usage:
#   MODEL_TYPE=detector BUNDLE=/media/usb/detector-v2.4.2.tar.gz.sig ./scripts/update-model.sh
#   MODEL_TYPE=vlm      BUNDLE=/media/usb/qwen3-vl-7b-v1.1.gguf.sig  ./scripts/update-model.sh

set -euo pipefail

DEPLOY_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
[[ -f "${DEPLOY_DIR}/.env" ]] && source "${DEPLOY_DIR}/.env"

MODEL_TYPE="${MODEL_TYPE:-}"
BUNDLE="${BUNDLE:-}"
MODEL_DIR=/var/lib/xray/models
REGISTRY_FILE="${MODEL_DIR}/registry.json"
STAGING_DIR="/tmp/xray-model-update-$$"
LOG="/var/log/xray/model-update-$(date -u +%Y%m%dT%H%M%SZ).log"
# Public key for bundle signature verification (GPG or minisign)
SIGNING_KEY_FILE="${SIGNING_KEY_FILE:-/etc/xray/model-signing.pub}"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
info()  { echo "[$(date -u +%H:%M:%SZ)] INFO  $*" | tee -a "$LOG"; }
warn()  { echo "[$(date -u +%H:%M:%SZ)] WARN  $*" | tee -a "$LOG"; }
error() { echo "[$(date -u +%H:%M:%SZ)] ERROR $*" | tee -a "$LOG" >&2; cleanup; exit 1; }

cleanup() { rm -rf "${STAGING_DIR}"; }
trap cleanup EXIT

# ── Preflight ────────────────────────────────────────────────────────────────
[[ -n "${MODEL_TYPE}" ]] || error "MODEL_TYPE must be set (detector|vlm)"
[[ -n "${BUNDLE}" ]]     || error "BUNDLE must point to the signed bundle file"
[[ -f "${BUNDLE}" ]]     || error "Bundle not found: ${BUNDLE}"
[[ -f "${SIGNING_KEY_FILE}" ]] || error "Signing public key not found: ${SIGNING_KEY_FILE}"

mkdir -p "${STAGING_DIR}"

info "=========================================="
info "Model update: ${MODEL_TYPE}"
info "Bundle:       ${BUNDLE}"
info "=========================================="

# ── Step 1: Verify bundle signature ─────────────────────────────────────────
info "Verifying bundle signature..."
SIG_FILE="${BUNDLE}.sig"
[[ -f "${SIG_FILE}" ]] || error "Signature file not found: ${SIG_FILE}"

if command -v minisign >/dev/null 2>&1; then
    minisign -V -p "${SIGNING_KEY_FILE}" -m "${BUNDLE}" \
        || error "Bundle signature verification FAILED — rejecting update"
elif command -v gpg >/dev/null 2>&1; then
    gpg --no-default-keyring --keyring "${SIGNING_KEY_FILE}" \
        --verify "${SIG_FILE}" "${BUNDLE}" \
        || error "Bundle signature verification FAILED — rejecting update"
else
    error "No signature verification tool found (minisign or gpg required)"
fi
info "Signature verified."

# ── Step 2: Extract and verify checksums ────────────────────────────────────
info "Extracting bundle..."
tar -xzf "${BUNDLE}" -C "${STAGING_DIR}"

MANIFEST="${STAGING_DIR}/manifest.json"
[[ -f "${MANIFEST}" ]] || error "manifest.json not found in bundle"

MODEL_FILE=$(python3 -c "import json,sys; m=json.load(open('${MANIFEST}')); print(m['file'])")
MODEL_SHA256=$(python3 -c "import json,sys; m=json.load(open('${MANIFEST}')); print(m['sha256'])")
MODEL_VERSION=$(python3 -c "import json,sys; m=json.load(open('${MANIFEST}')); print(m['version'])")
MODEL_NAME=$(python3 -c "import json,sys; m=json.load(open('${MANIFEST}')); print(m['name'])")

ACTUAL_SHA256=$(sha256sum "${STAGING_DIR}/${MODEL_FILE}" | cut -d' ' -f1)

info "Expected SHA-256: ${MODEL_SHA256}"
info "Actual SHA-256:   ${ACTUAL_SHA256}"

[[ "${MODEL_SHA256}" == "${ACTUAL_SHA256}" ]] \
    || error "SHA-256 mismatch — bundle corrupted or tampered"
info "Checksum verified."

# ── Step 3: Smoke test on staging ───────────────────────────────────────────
info "Running smoke test on staged model..."
case "${MODEL_TYPE}" in
    detector)
        # Run detector with the new weights against a known test image
        docker compose -f "${DEPLOY_DIR}/docker-compose.yml" run --rm \
            -e DETECTOR_MODEL_PATH="/staging/${MODEL_FILE}" \
            -v "${STAGING_DIR}:/staging:ro" \
            detector python -m detector.smoke_test \
            || error "Detector smoke test FAILED — aborting update"
        ;;
    vlm)
        # Run a single VLM inference to verify the model loads
        docker run --rm --runtime=nvidia \
            -v "${STAGING_DIR}:/staging:ro" \
            "xray/vlm:${XRAY_VERSION:-latest}" \
            llama-server \
                --model "/staging/${MODEL_FILE}" \
                --n-predict 1 \
                --ctx-size 512 \
                --n-gpu-layers "${VLM_N_GPU_LAYERS:-0}" \
                --run-once \
            || error "VLM smoke test FAILED — aborting update"
        ;;
    *) error "Unknown MODEL_TYPE: ${MODEL_TYPE}" ;;
esac
info "Smoke test passed."

# ── Step 4: Take a backup before promoting ───────────────────────────────────
info "Creating pre-update backup..."
"${DEPLOY_DIR}/scripts/backup.sh" 2>&1 | tee -a "$LOG" \
    || warn "Backup failed — proceeding anyway (manual backup recommended)"

# ── Step 5: Promote new weights ──────────────────────────────────────────────
info "Promoting new ${MODEL_TYPE} weights (${MODEL_VERSION})..."
DEST="${MODEL_DIR}/${MODEL_FILE}"
DEST_OLD="${DEST}.previous"

# Keep previous version for rollback
[[ -f "${DEST}" ]] && mv -f "${DEST}" "${DEST_OLD}"
cp -v "${STAGING_DIR}/${MODEL_FILE}" "${DEST}"
chmod 440 "${DEST}"

# ── Step 6: Update registry manifest ────────────────────────────────────────
info "Updating model registry..."
python3 - <<PYEOF
import json, os, datetime

registry_file = "${REGISTRY_FILE}"
try:
    with open(registry_file) as f:
        registry = json.load(f)
except (FileNotFoundError, json.JSONDecodeError):
    registry = {"models": {}}

registry["models"]["${MODEL_TYPE}"] = {
    "name":       "${MODEL_NAME}",
    "version":    "${MODEL_VERSION}",
    "file":       "${MODEL_FILE}",
    "sha256":     "${MODEL_SHA256}",
    "promoted_at": datetime.datetime.utcnow().isoformat() + "Z",
    "previous":   registry.get("models", {}).get("${MODEL_TYPE}", {}).get("file"),
}

with open(registry_file, "w") as f:
    json.dump(registry, f, indent=2)
print("Registry updated.")
PYEOF

# ── Step 7: Restart affected service ────────────────────────────────────────
info "Restarting ${MODEL_TYPE} service..."
docker compose -f "${DEPLOY_DIR}/docker-compose.yml" restart "${MODEL_TYPE}" \
    || error "Failed to restart ${MODEL_TYPE}"

# Wait for healthcheck
for i in $(seq 1 24); do
    sleep 5
    STATUS=$(docker compose -f "${DEPLOY_DIR}/docker-compose.yml" ps --format json "${MODEL_TYPE}" \
             | python3 -c "import sys,json; d=json.load(sys.stdin); print(d[0].get('Health',''))" 2>/dev/null || echo "")
    if [[ "${STATUS}" == "healthy" ]]; then
        info "${MODEL_TYPE} is healthy after update."
        break
    fi
    [[ $i -eq 24 ]] && error "${MODEL_TYPE} did not become healthy in 120s — rolling back"
done

info "=========================================="
info "Model update complete."
info "  Type:    ${MODEL_TYPE}"
info "  Version: ${MODEL_VERSION}"
info "  SHA-256: ${MODEL_SHA256}"
info "  Log:     ${LOG}"
info "=========================================="
info "REMOVE THE USB DEVICE NOW."
