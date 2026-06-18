# Incident Response Runbook

**System**: X-ray Customs Assistant  
**Classification**: RESTRICTED

---

## Severity levels

| Level | Definition | Response time | Example |
|-------|-----------|---------------|---------|
| P1 — Critical | System down or security breach | Immediate | Audit chain tampered, model checksum fail, data exfiltration |
| P2 — High | Scan processing degraded | < 30 min | API down, FN rate > 3%, GPU OOM |
| P3 — Medium | Monitoring degraded | < 2 hours | Backup stale, Grafana down, VLM latency high |
| P4 — Low | Cosmetic / performance | < 24 hours | Disk > 70%, log rotation failed |

---

## P1 — Audit chain integrity failure

**Alert**: `AuditChainTampered`

This is a potential security incident. Treat as a breach until proven otherwise.

**Immediate actions (within 15 minutes)**:
1. **Stop clearing traffic** — notify shift supervisor immediately
2. **Preserve evidence**: do NOT restart containers; do NOT modify volumes
   ```bash
   # Snapshot the database for forensics
   docker compose exec -T postgres pg_dump -U xray_admin xray_ops \
       > /tmp/forensic-dump-$(date +%s).sql
   ```
3. **Isolate the server**: physically disconnect from LAN if breach is suspected
4. **Notify**: security team + operations lead within 15 minutes

**Investigation**:
```bash
# Identify the first broken link in the chain
wget -qO- --header="Authorization: Bearer <admin-token>" \
    https://localhost/v1/admin/audit/verify | python3 -m json.tool

# Inspect the audit_events table directly
docker compose exec postgres psql -U xray_admin -d xray_ops -c \
    "SELECT seq, event_type, created_at, event_hmac FROM xray.audit_events ORDER BY seq DESC LIMIT 20;"
```

**Recovery**: Only after security team sign-off. See `disaster-recovery.md`.

---

## P1 — Model weight checksum failure

**Alert**: `ModelWeightChecksumFailed`

**Immediate actions**:
1. **Do NOT trust any detection results** produced since the last known-good checksum
2. Disable automated verdict display (operators work with raw images only)
3. Roll back to previous weights:
   ```bash
   # API will refuse to start if checksum fails at startup
   # Manually promote the .previous file
   mv /var/lib/xray/models/detector.onnx         /var/lib/xray/models/detector.onnx.suspect
   mv /var/lib/xray/models/detector.onnx.previous /var/lib/xray/models/detector.onnx
   docker compose restart detector
   ```
4. Verify rolled-back checksum matches registry
5. Determine how the weight file was modified (check auditd logs)

---

## P1 — Unexpected network egress

**Alert**: `UnexpectedNetworkEgress`

**Immediate actions**:
1. **Physically disconnect** the LAN cable if egress is confirmed
2. Check UFW logs for blocked/allowed connections:
   ```bash
   grep UFW /var/log/kern.log | tail -50
   ```
3. Check Docker network configuration:
   ```bash
   docker network ls
   docker network inspect <network>
   ```
4. Identify the source container:
   ```bash
   docker stats --no-stream
   # Check per-container network stats (requires cAdvisor or nsenter)
   ```
5. Preserve all logs before any restart

---

## P2 — High false negative rate (FN > 3%)

**Alert**: `ElevatedFalseNegativeRate`

The system is MISSING real threats. This is the highest-risk model failure mode.

1. **Immediately**: Require 100% manual physical inspection for all cleared scans from the last 24 hours
2. Notify shift supervisor and operations lead
3. Export feedback labels (see `data-diode.md`)
4. Initiate emergency retraining (see `model-update.md`)
5. Do NOT re-enable automatic advisory until new model passes evaluation

---

## P2 — API down

```bash
docker compose logs api --tail=100
docker compose restart api

# If OOM:
docker stats api
# Increase memory limit in docker-compose.yml if RAM permits, then:
docker compose up -d api
```

---

## P2 — Database connection exhaustion

```bash
# Check active connections
docker compose exec postgres psql -U xray_admin -d xray_ops -c \
    "SELECT count(*), state FROM pg_stat_activity GROUP BY state;"

# Kill idle connections older than 5 minutes
docker compose exec postgres psql -U xray_admin -d xray_ops -c \
    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity
     WHERE state = 'idle' AND query_start < NOW() - INTERVAL '5 minutes';"
```

---

## Incident log (physical)

Every P1 and P2 incident MUST be recorded in the physical operations logbook:

```
Date/time:
Incident:
Alert triggered:
Operator on shift:
Actions taken:
Root cause:
Resolution:
Preventive measures:
Sign-off:
```
