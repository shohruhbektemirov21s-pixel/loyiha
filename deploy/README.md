# X-ray Customs Assistant — Deployment Guide

**Environment**: On-premise, air-gapped, security-hardened  
**Runtime**: Docker Compose on a dedicated GPU server  
**Network**: Operator LAN only — no internet egress from any component

---

## Architecture

```
Operator LAN (192.168.10.0/24)
         │
    [nginx :443]  ← TLS termination
         │
    ┌────┴──────────────┐
    │   console-net     │  React SPA (static files)
    └────┬──────────────┘
         │
    ┌────┴──────────────┐
    │   api-net         │  FastAPI :8000
    └────┬──┬───────────┘
         │  │
  ┌──────┘  └──────────────────┐
  │ db-net                     │ ml-net
  │ PostgreSQL :5432            │ Detector :8001 (GPU)
  │ (SSL required, scram-sha)   │ VLM/llama.cpp :8080 (GPU)
  └─────────────────────────────┘
         │
    acq-net
    Acquisition bridge (DICOS hot-folder / HDMI grabber)
         │
    mon-net
    Prometheus :9090 → Alertmanager :9093 → internal SMTP
    Grafana :3000 (behind nginx /grafana/, LAN-restricted)
```

**Network isolation**: every Docker network has `internal: true` except `lan-net` (nginx only). No container can initiate outbound connections outside its declared networks.

---

## Quick start

### 1. Pre-air-gap preparation (on a networked machine)

```bash
# Pull all Docker images
docker pull postgres:16-alpine
docker pull nginx:1.27-alpine
docker pull prom/prometheus:v2.55.1
docker pull prom/alertmanager:v0.27.0
docker pull grafana/grafana:11.3.1
# Build application images
docker compose build

# Save everything to the install bundle
mkdir -p bundle/images
docker save postgres:16-alpine       | gzip > bundle/images/postgres.tar.gz
docker save nginx:1.27-alpine        | gzip > bundle/images/nginx.tar.gz
docker save prom/prometheus:v2.55.1  | gzip > bundle/images/prometheus.tar.gz
docker save prom/alertmanager:v0.27.0| gzip > bundle/images/alertmanager.tar.gz
docker save grafana/grafana:11.3.1   | gzip > bundle/images/grafana.tar.gz
docker save xray/api:latest          | gzip > bundle/images/api.tar.gz
docker save xray/detector:latest     | gzip > bundle/images/detector.tar.gz
docker save xray/vlm:latest          | gzip > bundle/images/vlm.tar.gz
docker save xray/console:latest      | gzip > bundle/images/console.tar.gz

# Bundle model weights + SHA-256 checksums
cp /path/to/detector.onnx              bundle/models/
cp /path/to/qwen3-vl-7b-q4_k_m.gguf   bundle/models/
sha256sum bundle/models/*              > bundle/models/sha256sums.txt

# Copy llama-server binary (CUDA-compiled)
cp /path/to/llama-server bundle/bin/

# Write bundle to encrypted USB
```

### 2. On the air-gapped server

```bash
# 1. OS hardening (run before any deployment)
LAN_IF=eth0 LAN_SUBNET=192.168.10.0/24 MGT_IP=192.168.10.50 \
  bash ops/hardening.sh
# Reboot

# 2. Generate secrets
bash deploy/scripts/generate-secrets.sh > deploy/.env
chmod 600 deploy/.env

# 3. Install
BUNDLE_DIR=/media/usb/xray-bundle bash deploy/scripts/install.sh

# 4. Verify
bash deploy/scripts/health-check.sh
```

---

## Day-to-day operations

| Task | Command |
|------|---------|
| Health check | `bash deploy/scripts/health-check.sh` |
| View logs | `docker compose logs -f <service>` |
| Manual backup | `bash deploy/scripts/backup.sh` |
| Update detector model | `MODEL_TYPE=detector BUNDLE=/media/usb/… bash deploy/scripts/update-model.sh` |
| Update VLM model | `MODEL_TYPE=vlm BUNDLE=/media/usb/… bash deploy/scripts/update-model.sh` |
| Export feedback labels | `EXPORT_DEST=/media/usb bash deploy/scripts/data-export.sh` |
| Restore from backup | `BACKUP_PATH=/mnt/nas/xray/TIMESTAMP bash deploy/scripts/restore.sh` |
| Restart single service | `docker compose restart <service>` |
| Roll back model | See `ops/runbooks/model-update.md` |

---

## Alert response

| Alert | Runbook |
|-------|---------|
| `AuditChainTampered` | `ops/runbooks/incident-response.md` → P1 |
| `ModelWeightChecksumFailed` | `ops/runbooks/incident-response.md` → P1 |
| `UnexpectedNetworkEgress` | `ops/runbooks/incident-response.md` → P1 |
| `ElevatedFalseNegativeRate` | `ops/runbooks/model-update.md` |
| `HighFalsePositiveRate` | `ops/runbooks/model-update.md` |
| `APIDown` / `DetectorDown` | `ops/runbooks/incident-response.md` → P2 |
| `DiskSpaceCritical` | Purge old image store blobs; expand volume |
| `BackupStale` | `ops/runbooks/disaster-recovery.md` |

---

## Security controls summary

| Control | Implementation |
|---------|---------------|
| No internet egress | All Docker networks `internal: true`; UFW `deny outgoing` default |
| Deny-by-default firewall | UFW with explicit allowlist (LAN subnet + NAS + SMTP relay only) |
| TLS everywhere | Postgres SSL required; nginx TLS 1.2+; operator console HTTPS only |
| Least-privilege DB access | `xray_api` user: no superuser; audit_events append-only |
| Model weight integrity | SHA-256 verified at startup + on every update; `xray_model_weight_valid` Prometheus gauge |
| Audit chain | HMAC-SHA256 chain on every event; tampering detected within 6h |
| Secrets management | Generated by `generate-secrets.sh`; stored in `chmod 600 .env`; never in git |
| Backup encryption | AES-256-CBC with PBKDF2; key stored separately from backups |
| Data diode | One-way USB transport; direction-separated USBs; signature verification |
| OS hardening | ASLR, sysctl hardening, SSH key-only, fail2ban, auditd, AIDE |
| Container isolation | Read-only root FS where possible; no new privileges; user namespace remap |

---

## Files

```
deploy/
├── docker-compose.yml          Full stack orchestration
├── .env.example                Config template (never commit .env)
├── dockerfiles/                Multi-stage Dockerfiles per service
├── nginx/                      Reverse proxy + TLS config
├── postgres/                   DB init SQL + pg_hba.conf
├── prometheus/                 Scrape config + 4 alert rule files
├── grafana/                    Dashboard + datasource provisioning
├── scripts/
│   ├── generate-secrets.sh     One-time secret generation
│   ├── install.sh              Offline installation
│   ├── backup.sh               Encrypted daily backup
│   ├── restore.sh              Restore from backup
│   ├── update-model.sh         Controlled model weight update
│   ├── data-export.sh          Feedback label export (sneakernet out)
│   ├── health-check.sh         Pre-flight / operational health check
│   └── cron/xray.cron          Scheduled tasks
└── model-registry/
    └── registry.py             SHA-256 verified model manifest CLI

ops/
├── hardening.sh                OS hardening (UFW, sysctl, SSH, auditd, AIDE)
└── runbooks/
    ├── disaster-recovery.md    Full restore procedure + RTO/RPO
    ├── model-update.md         Model weight update + rollback
    ├── data-diode.md           Sneakernet export/import procedure
    └── incident-response.md    P1–P4 response actions

app/
└── metrics.py                  Prometheus instrumentation for FastAPI
```
