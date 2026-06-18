# Disaster Recovery Runbook

**System**: X-ray Customs Assistant  
**Classification**: RESTRICTED — Operations Only  
**RTO target**: 4 hours (return to operational scan processing)  
**RPO target**: 24 hours (maximum data loss = one day of feedback labels)

---

## Prerequisites

Before this runbook is needed, confirm the following are in place:

- [ ] Backup encryption key (`/etc/xray/backup.key`) stored in a **separate** offline location (hardware token, offline password manager, sealed envelope in safe)
- [ ] Replacement server or restore target is provisioned (same GPU capability)
- [ ] Install bundle USB is current (images, weights, binaries)
- [ ] `.env` file (or ability to re-run `generate-secrets.sh` with stored secret material)

---

## Scenario A: Service crash / single container failure

**Symptoms**: One service is unhealthy; others are running.

```bash
# Identify the failing service
docker compose ps
docker compose logs <service> --tail=100

# Attempt restart
docker compose restart <service>

# If restart loop: pull logs, then recreate
docker compose stop <service>
docker compose up -d <service>
```

**Escalate to Scenario C if**: postgres or api fails to recover after 2 restart attempts.

---

## Scenario B: Host OS failure (hardware still functional)

**Symptoms**: System won't boot, or OS is corrupted.

1. Boot from live USB (Ubuntu 22.04 minimal)
2. Mount the data volumes:
   ```bash
   mount /dev/sdXN /mnt/xray-data    # identify the correct partition
   ```
3. Verify data integrity:
   ```bash
   du -sh /mnt/xray-data/pg-data     # should be non-zero
   du -sh /mnt/xray-data/store
   ```
4. If data is intact: reinstall OS, then proceed from **Step 3** of Scenario C with existing volumes.
5. If data is corrupted: proceed to **Scenario C** (full restore from backup).

---

## Scenario C: Full restore from encrypted backup

### Step 1 — Provision target server

- Install Ubuntu 22.04 LTS (minimal, no GUI)
- Install Docker Engine (offline, from bundle)
- Copy deploy directory from the install USB

### Step 2 — Run OS hardening

```bash
LAN_IF=eth0 LAN_SUBNET=192.168.10.0/24 MGT_IP=192.168.10.50 \
  bash ops/hardening.sh
# Reboot
```

### Step 3 — Restore secrets

Option A: if `.env` is available from offline storage:
```bash
cp /path/to/backup/.env deploy/.env
chmod 600 deploy/.env
```

Option B: if only secret material is available (from hardware token):
```bash
bash deploy/scripts/generate-secrets.sh > deploy/.env
# Then manually update XRAY_AUDIT_HMAC_KEY and XRAY_STORE_KEY
# to the ORIGINAL values — otherwise audit chain and image store are unreadable
chmod 600 deploy/.env
source deploy/.env
```

> **CRITICAL**: `XRAY_AUDIT_HMAC_KEY` and `XRAY_STORE_KEY` must match the
> original deployment. If lost, audit chain verification will fail and
> encrypted images cannot be decrypted. This is by design.

### Step 4 — Restore backup encryption key

```bash
# Copy from offline storage (hardware token or sealed envelope)
install -m 400 /path/to/backup.key /etc/xray/backup.key
```

### Step 5 — Load Docker images

```bash
for tar in /media/install-usb/images/*.tar; do
    docker load < "$tar"
done
```

### Step 6 — Restore PostgreSQL

```bash
# Mount backup NAS
mount -t cifs //192.168.10.200/backup /mnt/backup -o credentials=/etc/xray/nas.creds

# Find latest backup
LATEST=$(ls -td /mnt/backup/xray/*T*Z | head -1)
echo "Restoring from: $LATEST"

# Decrypt
BACKUP_KEY=$(cat /etc/xray/backup.key)
openssl enc -d -aes-256-cbc -pbkdf2 -iter 100000 \
    -pass "pass:${BACKUP_KEY}" \
    -in  "${LATEST}/postgres.dump.enc" \
    -out /tmp/postgres.dump

# Restore (start postgres container first)
docker compose up -d postgres
sleep 10
docker compose exec -T postgres pg_restore \
    -U ${POSTGRES_ADMIN_USER} \
    -d ${POSTGRES_DB} \
    --clean --if-exists \
    /tmp/postgres.dump   # bind mount required
```

### Step 7 — Restore image store

```bash
openssl enc -d -aes-256-cbc -pbkdf2 -iter 100000 \
    -pass "pass:${BACKUP_KEY}" \
    -in  "${LATEST}/image-store.tar.gz.enc" \
    | tar -xzf - -C /var/lib/xray/
```

### Step 8 — Install model weights

```bash
# From install USB or the model-weights volume backup
cp /media/install-usb/models/*.onnx /var/lib/xray/models/
cp /media/install-usb/models/*.gguf /var/lib/xray/models/
sha256sum -c /media/install-usb/models/sha256sums.txt
```

### Step 9 — Full stack start

```bash
bash deploy/scripts/install.sh     # generates TLS cert + starts stack
bash deploy/scripts/health-check.sh
```

### Step 10 — Verify audit chain

```bash
# Should return {"valid": true}
wget -qO- --header="Authorization: Bearer <admin-token>" \
    https://localhost/v1/admin/audit/verify
```

### Step 11 — Notify operations

- Notify shift supervisor that the system is restored
- Log the incident in the physical operations logbook
- Initiate post-incident review within 48 hours

---

## Recovery time estimates

| Step | Estimated time |
|------|---------------|
| OS install + hardening | 45 min |
| Image load | 15 min |
| DB restore (depends on size) | 15–60 min |
| Image store restore | 30–120 min |
| Start + healthcheck | 10 min |
| **Total** | **2–4 hours** |

---

## Post-recovery checklist

- [ ] All container healthchecks green
- [ ] Audit chain valid
- [ ] Model weight checksums pass
- [ ] Operator can log in
- [ ] Test scan processed end-to-end
- [ ] Backup resumes on schedule
- [ ] Incident logged in physical logbook
