#!/usr/bin/env bash
# generate-secrets.sh — emit a complete .env with cryptographically strong secrets.
# Usage:
#   ./scripts/generate-secrets.sh > .env
#   chmod 600 .env
#
# Requirements: openssl (standard on Linux)
# Run ONCE per deployment. Store the resulting .env in a hardware-backed secret
# store (HSM, encrypted USB, or offline password manager) — never in git.

set -euo pipefail

require() { command -v "$1" >/dev/null 2>&1 || { echo "Missing: $1" >&2; exit 1; }; }
require openssl
require hostname

rand_hex()  { openssl rand -hex  "$1"; }
rand_b64()  { openssl rand -base64 "$1" | tr -d '\n/+=' | head -c "$1"; }
rand_pass() { openssl rand -base64 48 | tr -d '\n'; }   # 48 bytes → 64 printable chars

LAN_IP="${LAN_IP:-$(hostname -I | awk '{print $1}')}"
TS="$(date -u +%Y%m%dT%H%M%SZ)"
# Capture the API password once so it can be reused in the asyncpg DSN below.
API_PW="$(rand_pass)"
ADMIN_PW="$(rand_pass)"

cat <<EOF
# X-ray assistant deployment secrets — generated ${TS}
# KEEP THIS FILE OFFLINE. Never commit to git.
# chmod 600 .env after writing.

# ---------------------------------------------------------------------------
# Deployment identity
# ---------------------------------------------------------------------------
XRAY_VERSION=1.0.0
XRAY_ENVIRONMENT=production
LAN_IP=${LAN_IP}

# ---------------------------------------------------------------------------
# PostgreSQL
# ---------------------------------------------------------------------------
POSTGRES_DB=xray_ops
POSTGRES_ADMIN_USER=xray_admin
POSTGRES_ADMIN_PASSWORD=${ADMIN_PW}
POSTGRES_API_USER=xray_api
POSTGRES_API_PASSWORD=${API_PW}
# Required in production (app/settings.py refuses to boot without it). Used by
# the direct-run path (deploy/start.sh); compose builds its own DSN inline.
XRAY_DB_URL=postgresql+asyncpg://xray_api:${API_PW}@127.0.0.1:5432/xray_ops?ssl=require

# ---------------------------------------------------------------------------
# API / application secrets
# ---------------------------------------------------------------------------
XRAY_JWT_SECRET=$(rand_hex 32)
XRAY_AUDIT_HMAC_KEY=$(rand_hex 32)
XRAY_STORE_KEY=$(rand_hex 32)
XRAY_STORE_DIR=/var/lib/xray/store

# ---------------------------------------------------------------------------
# Detector
# ---------------------------------------------------------------------------
XRAY_DETECTOR_ENABLED=true
DETECTOR_DEVICE=cuda
DETECTOR_BATCH_SIZE=1
DETECTOR_CONF_THRESH=0.35

# ---------------------------------------------------------------------------
# VLM
# ---------------------------------------------------------------------------
XRAY_VLM_ENABLED=true
XRAY_VLM_BACKEND=llama_cpp
XRAY_VLM_MODEL=qwen3-vl-7b-q4_k_m.gguf
VLM_CTX_SIZE=4096
VLM_N_GPU_LAYERS=35
VLM_THREADS=8

# ---------------------------------------------------------------------------
# Acquisition
# ---------------------------------------------------------------------------
XRAY_ACQ_DRIVER=dicos
XRAY_ACQ_SCANNER_ID=scanner-01
XRAY_ACQ_LANE_ID=lane-1
XRAY_ACQ_DICOS_WATCH_DIR=/var/lib/xray/incoming
XRAY_GRAB_DEVICE=/dev/video0

# ---------------------------------------------------------------------------
# Monitoring
# ---------------------------------------------------------------------------
GRAFANA_ADMIN_USER=admin
GRAFANA_ADMIN_PASSWORD=$(rand_b64 20)

ALERTMANAGER_SMTP_HOST=smtp.internal:25
ALERTMANAGER_SMTP_FROM=xray-alerts@internal.local
ALERTMANAGER_ALERT_TO=ops@internal.local

# ---------------------------------------------------------------------------
# Backup
# ---------------------------------------------------------------------------
BACKUP_DEST=/mnt/backup-nas/xray
BACKUP_ENCRYPTION_KEY_FILE=/etc/xray/backup.key
BACKUP_RETENTION_DAYS=90

# ---------------------------------------------------------------------------
# TLS
# ---------------------------------------------------------------------------
TLS_COMMON_NAME=xray.internal.local
TLS_COUNTRY=UZ
TLS_ORG=Customs
EOF
