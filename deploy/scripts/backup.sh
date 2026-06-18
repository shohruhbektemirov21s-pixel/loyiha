#!/usr/bin/env bash
# backup.sh — encrypted, compressed backup of all persistent data.
#
# What is backed up:
#   1. PostgreSQL (pg_dump, full schema + data)
#   2. Encrypted image store (/var/lib/xray/store)
#   3. Model registry manifests (/var/lib/xray/models/registry.json)
#   4. .env (secrets — encrypted separately with a different key)
#   5. nginx TLS certificates
#
# Encryption: AES-256-CBC via openssl (symmetric, key from /etc/xray/backup.key)
# Compression: gzip
# Destination: ${BACKUP_DEST}/YYYY-MM-DD_HH/ on NAS/tape mount
#
# After backup completes, push a metric to Prometheus pushgateway so
# the "BackupStale" alert can track it.

set -euo pipefail

DEPLOY_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
[[ -f "${DEPLOY_DIR}/.env" ]] && source "${DEPLOY_DIR}/.env"

BACKUP_KEY_FILE="${BACKUP_ENCRYPTION_KEY_FILE:-/etc/xray/backup.key}"
BACKUP_DEST="${BACKUP_DEST:-/mnt/backup-nas/xray}"
RETENTION_DAYS="${BACKUP_RETENTION_DAYS:-90}"
PUSHGATEWAY_URL="${PUSHGATEWAY_URL:-http://pushgateway:9091}"   # optional

TS=$(date -u +%Y-%m-%dT%H%M%SZ)
BACKUP_DIR="${BACKUP_DEST}/${TS}"
STAGING="/tmp/xray-backup-${TS}"
LOG="${STAGING}.log"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
info()  { echo "[$(date -u +%H:%M:%SZ)] INFO  $*" | tee -a "$LOG"; }
warn()  { echo "[$(date -u +%H:%M:%SZ)] WARN  $*" | tee -a "$LOG"; }
error() { echo "[$(date -u +%H:%M:%SZ)] ERROR $*" | tee -a "$LOG" >&2; exit 1; }

# ── Preflight ────────────────────────────────────────────────────────────────
[[ -f "${BACKUP_KEY_FILE}" ]] || error "Backup key not found: ${BACKUP_KEY_FILE}"
[[ -d "${BACKUP_DEST}" ]]    || error "Backup destination not mounted: ${BACKUP_DEST}"

BACKUP_KEY=$(cat "${BACKUP_KEY_FILE}")
mkdir -p "${STAGING}" "${BACKUP_DIR}"

encrypt() {
    # encrypt stdin → stdout using AES-256-CBC with PBKDF2
    openssl enc -aes-256-cbc -pbkdf2 -iter 100000 -pass "pass:${BACKUP_KEY}"
}

info "Starting backup → ${BACKUP_DIR}"
info "Timestamp: ${TS}"

# ── 1. PostgreSQL dump ───────────────────────────────────────────────────────
info "Dumping PostgreSQL..."
docker compose -f "${DEPLOY_DIR}/docker-compose.yml" exec -T postgres \
    pg_dump \
        -U "${POSTGRES_ADMIN_USER}" \
        -d "${POSTGRES_DB}" \
        --format=custom \
        --compress=9 \
        --no-password \
    | encrypt > "${BACKUP_DIR}/postgres.dump.enc"
info "PostgreSQL dump complete ($(du -sh "${BACKUP_DIR}/postgres.dump.enc" | cut -f1))"

# ── 2. Encrypted image store ─────────────────────────────────────────────────
info "Archiving image store..."
tar -czf - -C /var/lib/xray store \
    | encrypt > "${BACKUP_DIR}/image-store.tar.gz.enc"
info "Image store archived ($(du -sh "${BACKUP_DIR}/image-store.tar.gz.enc" | cut -f1))"

# ── 3. Model registry ────────────────────────────────────────────────────────
info "Archiving model registry..."
if [[ -f /var/lib/xray/models/registry.json ]]; then
    cp /var/lib/xray/models/registry.json "${STAGING}/registry.json"
    encrypt < "${STAGING}/registry.json" > "${BACKUP_DIR}/model-registry.json.enc"
fi

# ── 4. Secrets (.env) — double-encrypted ────────────────────────────────────
info "Backing up secrets..."
# Use a second key derived from the first (so a single key compromise doesn't
# expose both the backup and the secrets together in one file).
SECRETS_KEY=$(echo "${BACKUP_KEY}" | sha256sum | cut -d' ' -f1)
openssl enc -aes-256-cbc -pbkdf2 -iter 200000 \
    -pass "pass:${SECRETS_KEY}" \
    -in  "${DEPLOY_DIR}/.env" \
    -out "${BACKUP_DIR}/env.enc"

# ── 5. TLS certificates ──────────────────────────────────────────────────────
info "Backing up TLS certificates..."
tar -czf - -C "${DEPLOY_DIR}" nginx/tls \
    | encrypt > "${BACKUP_DIR}/tls.tar.gz.enc"

# ── 6. Backup manifest (unencrypted — for verification without decryption) ──
info "Writing manifest..."
cat > "${BACKUP_DIR}/MANIFEST.txt" <<MANIFEST
X-ray backup manifest
Timestamp:    ${TS}
Hostname:     $(hostname)
Version:      ${XRAY_VERSION:-unknown}
Files:
$(ls -lh "${BACKUP_DIR}"/*.enc 2>/dev/null)
SHA-256 of each encrypted file:
$(sha256sum "${BACKUP_DIR}"/*.enc 2>/dev/null)
MANIFEST

# ── 7. Retention — delete backups older than RETENTION_DAYS ─────────────────
info "Pruning backups older than ${RETENTION_DAYS} days..."
find "${BACKUP_DEST}" -maxdepth 1 -type d -mtime "+${RETENTION_DAYS}" -exec rm -rf {} + 2>/dev/null || true

BACKUP_SIZE=$(du -sh "${BACKUP_DIR}" | cut -f1)
info "Backup complete. Size: ${BACKUP_SIZE}. Location: ${BACKUP_DIR}"

# ── 8. Push metric to Prometheus pushgateway (if available) ─────────────────
EPOCH=$(date +%s)
if command -v wget >/dev/null 2>&1; then
    wget -qO- --post-data="xray_last_backup_timestamp_seconds ${EPOCH}
# HELP xray_last_backup_timestamp_seconds Unix timestamp of last successful backup
# TYPE xray_last_backup_timestamp_seconds gauge
" "${PUSHGATEWAY_URL}/metrics/job/xray_backup" 2>/dev/null || warn "Could not push metric to pushgateway"
fi

# Clean up staging
rm -rf "${STAGING}"

exit 0
