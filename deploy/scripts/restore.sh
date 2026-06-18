#!/usr/bin/env bash
# restore.sh — restore from an encrypted backup.
# See ops/runbooks/disaster-recovery.md for the full procedure.
#
# Usage:
#   BACKUP_PATH=/mnt/backup-nas/xray/2025-01-15T020000Z \
#     ./scripts/restore.sh

set -euo pipefail

DEPLOY_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
[[ -f "${DEPLOY_DIR}/.env" ]] && source "${DEPLOY_DIR}/.env"

BACKUP_PATH="${BACKUP_PATH:-}"
BACKUP_KEY_FILE="${BACKUP_ENCRYPTION_KEY_FILE:-/etc/xray/backup.key}"
RESTORE_WHAT="${RESTORE_WHAT:-all}"   # all | postgres | store | tls

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
info()  { echo -e "${GREEN}[RESTORE]${NC} $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}   $*"; }
error() { echo -e "${RED}[ERROR]${NC}  $*" >&2; exit 1; }

# ── Preflight ────────────────────────────────────────────────────────────────
[[ -n "${BACKUP_PATH}" ]]    || error "BACKUP_PATH must be set"
[[ -d "${BACKUP_PATH}" ]]    || error "Backup directory not found: ${BACKUP_PATH}"
[[ -f "${BACKUP_KEY_FILE}" ]] || error "Backup key not found: ${BACKUP_KEY_FILE}"
BACKUP_KEY=$(cat "${BACKUP_KEY_FILE}")

info "Restoring from: ${BACKUP_PATH}"
info "Components:     ${RESTORE_WHAT}"

# Verify backup manifest checksums
info "Verifying backup integrity..."
if [[ -f "${BACKUP_PATH}/MANIFEST.txt" ]]; then
    # Extract expected SHA-256s from manifest and re-check
    grep -E "^[0-9a-f]{64}" "${BACKUP_PATH}/MANIFEST.txt" | while read -r hash file; do
        ACTUAL=$(sha256sum "$file" | cut -d' ' -f1)
        [[ "$ACTUAL" == "$hash" ]] || error "Backup file corrupted: $file"
    done
    info "Backup integrity verified."
else
    warn "No MANIFEST.txt — skipping integrity check"
fi

decrypt() {
    openssl enc -d -aes-256-cbc -pbkdf2 -iter 100000 \
        -pass "pass:${BACKUP_KEY}" -in "$1"
}

# ── Postgres ─────────────────────────────────────────────────────────────────
if [[ "${RESTORE_WHAT}" == "all" || "${RESTORE_WHAT}" == "postgres" ]]; then
    [[ -f "${BACKUP_PATH}/postgres.dump.enc" ]] || error "postgres.dump.enc not found"
    info "Restoring PostgreSQL..."

    # Ensure postgres is running
    docker compose -f "${DEPLOY_DIR}/docker-compose.yml" up -d postgres
    sleep 10

    # Decrypt and restore
    TMPFILE=$(mktemp /tmp/pg-restore.XXXXXX.dump)
    trap "rm -f ${TMPFILE}" EXIT
    decrypt "${BACKUP_PATH}/postgres.dump.enc" > "${TMPFILE}"

    # Copy dump into postgres container and restore
    docker cp "${TMPFILE}" "$(docker compose -f "${DEPLOY_DIR}/docker-compose.yml" ps -q postgres)":/tmp/restore.dump

    docker compose -f "${DEPLOY_DIR}/docker-compose.yml" exec -T postgres \
        pg_restore \
            -U "${POSTGRES_ADMIN_USER}" \
            -d "${POSTGRES_DB}" \
            --clean --if-exists --no-privileges \
            /tmp/restore.dump

    info "PostgreSQL restored."
fi

# ── Image store ──────────────────────────────────────────────────────────────
if [[ "${RESTORE_WHAT}" == "all" || "${RESTORE_WHAT}" == "store" ]]; then
    [[ -f "${BACKUP_PATH}/image-store.tar.gz.enc" ]] || error "image-store.tar.gz.enc not found"
    info "Restoring image store..."
    decrypt "${BACKUP_PATH}/image-store.tar.gz.enc" | tar -xzf - -C /var/lib/xray/
    info "Image store restored."
fi

# ── TLS certificates ─────────────────────────────────────────────────────────
if [[ "${RESTORE_WHAT}" == "all" || "${RESTORE_WHAT}" == "tls" ]]; then
    if [[ -f "${BACKUP_PATH}/tls.tar.gz.enc" ]]; then
        info "Restoring TLS certificates..."
        decrypt "${BACKUP_PATH}/tls.tar.gz.enc" | tar -xzf - -C "${DEPLOY_DIR}/"
        info "TLS certificates restored."
    else
        warn "No TLS backup found — will generate new self-signed cert"
    fi
fi

# ── Full stack start ─────────────────────────────────────────────────────────
if [[ "${RESTORE_WHAT}" == "all" ]]; then
    info "Starting full stack..."
    docker compose -f "${DEPLOY_DIR}/docker-compose.yml" up -d

    info "Waiting for API healthcheck..."
    for i in $(seq 1 30); do
        if docker compose -f "${DEPLOY_DIR}/docker-compose.yml" exec -T api \
               wget -qO- http://localhost:8000/health 2>/dev/null | grep -q '"ok"'; then
            info "API healthy."
            break
        fi
        [[ $i -eq 30 ]] && error "API did not become healthy — check logs"
        sleep 5
    done
fi

info "Restore complete. Run health-check.sh to verify."
