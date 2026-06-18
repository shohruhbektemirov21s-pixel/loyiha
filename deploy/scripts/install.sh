#!/usr/bin/env bash
# install.sh — offline install on a fresh air-gapped server.
# Run as root (or with sudo).
#
# Prerequisites on the install bundle USB/NAS:
#   bundle/
#     images/          — docker save output (*.tar)
#     wheels/          — Python wheels
#     bin/             — pre-compiled binaries (llama-server, node_exporter, etc.)
#     models/          — GGUF weights + ONNX weights + SHA-256 manifest
#
# Usage:
#   BUNDLE_DIR=/media/usb/xray-bundle ./scripts/install.sh

set -euo pipefail

BUNDLE_DIR="${BUNDLE_DIR:-/opt/xray-bundle}"
DEPLOY_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
XRAY_DIR=/opt/xray
MODEL_DIR=/var/lib/xray/models
STORE_DIR=/var/lib/xray/store
INCOMING_DIR=/var/lib/xray/incoming
LOG_DIR=/var/log/xray

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
info()  { echo -e "${GREEN}[INSTALL]${NC} $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}   $*"; }
error() { echo -e "${RED}[ERROR]${NC}  $*" >&2; exit 1; }

# ── Preflight checks ─────────────────────────────────────────────────────────
[[ $EUID -eq 0 ]] || error "Must run as root"
command -v docker   >/dev/null 2>&1 || error "Docker not installed"
command -v openssl  >/dev/null 2>&1 || error "openssl not found"
[[ -f "${DEPLOY_DIR}/.env" ]] || error ".env not found. Run generate-secrets.sh first."
source "${DEPLOY_DIR}/.env"

info "Installing X-ray assistant to ${XRAY_DIR}"
info "Bundle: ${BUNDLE_DIR}"

# ── Directory structure ──────────────────────────────────────────────────────
info "Creating directories..."
install -d -m 750 "${XRAY_DIR}"
install -d -m 700 "${MODEL_DIR}"
install -d -m 750 "${STORE_DIR}"
install -d -m 755 "${INCOMING_DIR}"
install -d -m 750 "${LOG_DIR}"
install -d -m 700 /etc/xray

# ── Docker images (offline load) ────────────────────────────────────────────
info "Loading Docker images from bundle..."
for tar in "${BUNDLE_DIR}"/images/*.tar; do
    [[ -f "$tar" ]] || continue
    img=$(basename "$tar" .tar)
    info "  Loading ${img}..."
    docker load < "$tar"
done

# ── Model weights ────────────────────────────────────────────────────────────
info "Installing model weights..."
cp -v "${BUNDLE_DIR}"/models/*.onnx "${MODEL_DIR}/" 2>/dev/null || warn "No ONNX weights found"
cp -v "${BUNDLE_DIR}"/models/*.gguf "${MODEL_DIR}/" 2>/dev/null || warn "No GGUF weights found"

info "Verifying model weight checksums..."
if [[ -f "${BUNDLE_DIR}/models/sha256sums.txt" ]]; then
    (cd "${MODEL_DIR}" && sha256sum --check "${BUNDLE_DIR}/models/sha256sums.txt") \
        || error "Model weight checksum verification FAILED — bundle may be corrupted"
    info "Checksums OK"
else
    warn "No sha256sums.txt found in bundle — skipping checksum verification"
fi

# ── Binaries ─────────────────────────────────────────────────────────────────
if [[ -d "${BUNDLE_DIR}/bin" ]]; then
    info "Installing binaries..."
    cp -v "${BUNDLE_DIR}"/bin/llama-server    /usr/local/bin/
    cp -v "${BUNDLE_DIR}"/bin/node_exporter   /usr/local/bin/ 2>/dev/null || true
    chmod +x /usr/local/bin/llama-server
fi

# ── TLS certificate (self-signed) ────────────────────────────────────────────
info "Generating TLS certificate..."
CERT_DIR="${DEPLOY_DIR}/nginx/tls"
mkdir -p "${CERT_DIR}"
if [[ ! -f "${CERT_DIR}/server.crt" ]]; then
    openssl req -x509 -nodes -newkey rsa:4096 \
        -keyout "${CERT_DIR}/server.key" \
        -out    "${CERT_DIR}/server.crt" \
        -days   3650 \
        -subj   "/C=${TLS_COUNTRY}/O=${TLS_ORG}/CN=${TLS_COMMON_NAME}" \
        -addext "subjectAltName=IP:${LAN_IP},DNS:${TLS_COMMON_NAME}"
    chmod 600 "${CERT_DIR}/server.key"
    info "TLS certificate generated: ${CERT_DIR}/server.crt"
else
    info "TLS certificate already exists, skipping"
fi

# ── Postgres SSL cert ────────────────────────────────────────────────────────
info "Generating PostgreSQL server TLS certificate..."
PG_CERT_DIR="${XRAY_DIR}/pg-certs"
mkdir -p "${PG_CERT_DIR}"
if [[ ! -f "${PG_CERT_DIR}/server.crt" ]]; then
    openssl req -x509 -nodes -newkey rsa:4096 \
        -keyout "${PG_CERT_DIR}/server.key" \
        -out    "${PG_CERT_DIR}/server.crt" \
        -days   3650 \
        -subj   "/CN=postgres"
    chmod 600 "${PG_CERT_DIR}/server.key"
fi

# ── Backup encryption key ────────────────────────────────────────────────────
if [[ ! -f /etc/xray/backup.key ]]; then
    info "Generating backup encryption key..."
    openssl rand -hex 32 > /etc/xray/backup.key
    chmod 400 /etc/xray/backup.key
    warn "IMPORTANT: Back up /etc/xray/backup.key to a SEPARATE offline location."
    warn "Without this key, encrypted backups cannot be restored."
fi

# ── systemd service ──────────────────────────────────────────────────────────
info "Installing systemd service..."
cat > /etc/systemd/system/xray-stack.service <<UNIT
[Unit]
Description=X-ray Customs Assistant Stack
Requires=docker.service
After=docker.service network.target

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=${DEPLOY_DIR}
EnvironmentFile=${DEPLOY_DIR}/.env
ExecStart=/usr/bin/docker compose up -d --remove-orphans
ExecStop=/usr/bin/docker compose down
TimeoutStartSec=300
TimeoutStopSec=120
Restart=on-failure

[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
systemctl enable xray-stack.service

# ── Cron jobs ────────────────────────────────────────────────────────────────
info "Installing cron jobs..."
cp "${DEPLOY_DIR}/scripts/cron/xray.cron" /etc/cron.d/xray
chmod 644 /etc/cron.d/xray

# ── Startup ──────────────────────────────────────────────────────────────────
info "Starting stack..."
cd "${DEPLOY_DIR}"
docker compose up -d

info "Waiting for API healthcheck..."
for i in $(seq 1 30); do
    if docker compose exec -T api wget -qO- http://localhost:8000/health 2>/dev/null | grep -q '"ok"'; then
        info "API is healthy."
        break
    fi
    [[ $i -eq 30 ]] && error "API did not become healthy in time. Check: docker compose logs api"
    sleep 5
done

info "Installation complete."
info "Operator console: https://${LAN_IP}/"
info "Grafana:          https://${LAN_IP}/grafana/"
warn "Remove the USB bundle from the server now."
