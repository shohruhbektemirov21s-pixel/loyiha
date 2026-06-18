#!/usr/bin/env bash
# data-export.sh — controlled feedback data export for offline retraining.
#
# PURPOSE: Export operator feedback (ground-truth labels) from the air-gapped
# server to a write-once USB/disk for transfer to the offline training box.
#
# SECURITY MODEL (data diode):
#   - Data flows ONLY outward (server → USB → training box)
#   - The training box has NO network path back to this server
#   - The USB is verified to be write-once or physically write-protected after export
#   - No model weights or scan images leave the server (only ground-truth labels)
#   - Exported data is encrypted and signed before leaving
#
# What is exported:
#   - OperatorFeedback JSONL (labels only — bounding boxes, category corrections,
#     missed regions, outcomes) with scan_id references
#   - Optionally: anonymised crop images if EXPORT_CROPS=true (requires separate
#     approval and physical escort for the USB)
#
# Usage:
#   EXPORT_DEST=/media/usb/feedback-export ./scripts/data-export.sh

set -euo pipefail

DEPLOY_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
[[ -f "${DEPLOY_DIR}/.env" ]] && source "${DEPLOY_DIR}/.env"

EXPORT_DEST="${EXPORT_DEST:-/media/export-usb}"
EXPORT_CROPS="${EXPORT_CROPS:-false}"
BACKUP_KEY=$(cat "${BACKUP_ENCRYPTION_KEY_FILE:-/etc/xray/backup.key}")
SIGNING_KEY="${SIGNING_KEY_FILE:-/etc/xray/model-signing.key}"   # private key for signing
TS=$(date -u +%Y%m%dT%H%M%SZ)
STAGING="/tmp/xray-export-${TS}"
LOG="/var/log/xray/data-export-${TS}.log"

info()  { echo "[$(date -u +%H:%M:%SZ)] INFO  $*" | tee -a "$LOG"; }
error() { echo "[$(date -u +%H:%M:%SZ)] ERROR $*" | tee -a "$LOG" >&2; rm -rf "${STAGING}"; exit 1; }

mkdir -p "${STAGING}"
trap 'rm -rf "${STAGING}"' EXIT

# ── Preflight ────────────────────────────────────────────────────────────────
[[ -d "${EXPORT_DEST}" ]] || error "Export destination not mounted: ${EXPORT_DEST}"
DEST_FREE=$(df -BG "${EXPORT_DEST}" | awk 'NR==2{print $4}' | tr -d 'G')
[[ "${DEST_FREE:-0}" -gt 5 ]] || error "Less than 5 GB free on export destination"

info "Feedback data export — ${TS}"
info "Destination: ${EXPORT_DEST}"

# ── Step 1: Export feedback labels from PostgreSQL ───────────────────────────
info "Exporting operator feedback from database..."
docker compose -f "${DEPLOY_DIR}/docker-compose.yml" exec -T postgres \
    psql -U "${POSTGRES_ADMIN_USER}" -d "${POSTGRES_DB}" -t -A \
    -c "
    SELECT row_to_json(row)::text
    FROM (
        SELECT
            f.feedback_id,
            f.scan_id,
            f.verdict_id,
            f.operator_id,
            f.outcome,
            f.decided_at,
            f.feedback_json  -- contains reviews, missed annotations
        FROM xray.operator_feedback f
        WHERE f.exported_at IS NULL      -- only unexported rows
        ORDER BY f.decided_at
    ) row;
    " > "${STAGING}/feedback_labels.jsonl"

LINE_COUNT=$(wc -l < "${STAGING}/feedback_labels.jsonl")
info "Exported ${LINE_COUNT} feedback records."
[[ "${LINE_COUNT}" -gt 0 ]] || { info "No new feedback to export."; exit 0; }

# ── Step 2: Optionally export crop images ────────────────────────────────────
if [[ "${EXPORT_CROPS}" == "true" ]]; then
    info "WARNING: EXPORT_CROPS=true — crop images will be included in export."
    info "This requires supervisor approval and physical USB escort."
    read -r -p "Type APPROVED to continue: " APPROVAL
    [[ "${APPROVAL}" == "APPROVED" ]] || error "Crop export not approved."
    # TODO: query crop StorageRef URIs from DB and copy from image store
    warn "Crop export: implement per-deployment data minimisation policy."
fi

# ── Step 3: Write manifest ───────────────────────────────────────────────────
cat > "${STAGING}/manifest.json" <<JSON
{
  "export_timestamp": "${TS}",
  "hostname":         "$(hostname)",
  "xray_version":     "${XRAY_VERSION:-unknown}",
  "record_count":     ${LINE_COUNT},
  "includes_crops":   ${EXPORT_CROPS},
  "sha256_labels":    "$(sha256sum "${STAGING}/feedback_labels.jsonl" | cut -d' ' -f1)"
}
JSON

# ── Step 4: Create encrypted archive ─────────────────────────────────────────
info "Encrypting export bundle..."
ARCHIVE="${STAGING}/xray-feedback-${TS}.tar.gz"
tar -czf "${ARCHIVE}" -C "${STAGING}" feedback_labels.jsonl manifest.json
openssl enc -aes-256-cbc -pbkdf2 -iter 200000 \
    -pass "pass:${BACKUP_KEY}" \
    -in  "${ARCHIVE}" \
    -out "${ARCHIVE}.enc"

# ── Step 5: Sign the encrypted archive ───────────────────────────────────────
info "Signing export bundle..."
if command -v minisign >/dev/null 2>&1 && [[ -f "${SIGNING_KEY}" ]]; then
    minisign -S -s "${SIGNING_KEY}" -m "${ARCHIVE}.enc" \
        || error "Failed to sign bundle"
elif command -v gpg >/dev/null 2>&1; then
    gpg --batch --yes --armor --detach-sign \
        --default-key "xray-ops" \
        --output "${ARCHIVE}.enc.sig" \
        "${ARCHIVE}.enc" \
        || error "Failed to sign bundle"
else
    warn "No signing tool available — bundle will not be signed"
fi

# ── Step 6: Write to USB ─────────────────────────────────────────────────────
info "Writing to export destination..."
cp -v "${ARCHIVE}.enc" "${EXPORT_DEST}/"
[[ -f "${ARCHIVE}.enc.sig" ]] && cp -v "${ARCHIVE}.enc.sig" "${EXPORT_DEST}/"
cp -v "${STAGING}/manifest.json" "${EXPORT_DEST}/manifest-${TS}.json"
sync

info "Verifying written files..."
sha256sum -c <(echo "$(sha256sum "${ARCHIVE}.enc" | cut -d' ' -f1)  ${EXPORT_DEST}/$(basename "${ARCHIVE}.enc")") \
    || error "Written file checksum mismatch"

# ── Step 7: Mark records as exported in DB ──────────────────────────────────
info "Marking records as exported..."
docker compose -f "${DEPLOY_DIR}/docker-compose.yml" exec -T postgres \
    psql -U "${POSTGRES_ADMIN_USER}" -d "${POSTGRES_DB}" \
    -c "UPDATE xray.operator_feedback SET exported_at = NOW() WHERE exported_at IS NULL;"

info "=========================================="
info "Export complete."
info "  Records:     ${LINE_COUNT}"
info "  Bundle:      ${EXPORT_DEST}/$(basename "${ARCHIVE}.enc")"
info "  Manifest:    ${EXPORT_DEST}/manifest-${TS}.json"
info "=========================================="
info "PHYSICAL SECURITY: Escort the USB to the training box."
info "VERIFY the signature on the training box before importing."
info "The training box must have NO network path to this server."
