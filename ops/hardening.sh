#!/usr/bin/env bash
# hardening.sh — OS hardening for the air-gapped X-ray server.
# Run ONCE after OS install, before deploying the stack.
# Tested on: Ubuntu 22.04 LTS, Debian 12.
#
# After running: reboot, then verify with health-check.sh.
#
# Reference: CIS Benchmark Level 2 (Server), NIST SP 800-123.

set -euo pipefail
[[ $EUID -eq 0 ]] || { echo "Must run as root"; exit 1; }

LAN_IF="${LAN_IF:-eth0}"       # LAN NIC (operator network)
XRAY_USER="${XRAY_USER:-xray}" # service account

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
info() { echo -e "${GREEN}[HARDEN]${NC} $*"; }
warn() { echo -e "${YELLOW}[WARN]${NC}   $*"; }

# ── 1. System updates (pre-air-gap — run before disconnecting) ───────────────
info "Applying system updates..."
apt-get update -q && apt-get upgrade -y -q && apt-get autoremove -y -q

# ── 2. Remove unnecessary packages ──────────────────────────────────────────
info "Removing unnecessary packages..."
REMOVE_PKGS=(
    telnet
    rsh-client
    rsh-redone-client
    talk
    ntalk
    avahi-daemon
    cups
    isc-dhcp-server
    bind9
    vsftpd
    apache2
    nginx   # will be run in Docker, not on host
    postfix # will use internal relay, not host MTA
)
for pkg in "${REMOVE_PKGS[@]}"; do
    apt-get purge -y "$pkg" 2>/dev/null || true
done

# ── 3. Install security tools ────────────────────────────────────────────────
info "Installing security tools..."
apt-get install -y -q \
    ufw \
    fail2ban \
    auditd \
    audispd-plugins \
    rkhunter \
    aide \
    unattended-upgrades \
    apt-listchanges \
    logrotate \
    rsyslog

# ── 4. Firewall — deny-by-default, allow only operator LAN ──────────────────
info "Configuring UFW firewall (deny-by-default)..."
ufw --force reset
ufw default deny incoming
ufw default deny outgoing
ufw default deny forward

# Allow HTTPS from operator LAN subnet only
LAN_SUBNET="${LAN_SUBNET:-192.168.10.0/24}"
ufw allow in  on "${LAN_IF}" from "${LAN_SUBNET}" to any port 443 proto tcp
ufw allow in  on "${LAN_IF}" from "${LAN_SUBNET}" to any port 80  proto tcp
# Allow SSH from a dedicated management IP only
MGT_IP="${MGT_IP:-192.168.10.50}"
ufw allow in  on "${LAN_IF}" from "${MGT_IP}" to any port 22 proto tcp
# Allow NAS backup (NFS/SMB) — outbound to NAS only
NAS_IP="${NAS_IP:-192.168.10.200}"
ufw allow out on "${LAN_IF}" to "${NAS_IP}" port 445 proto tcp   # SMB
ufw allow out on "${LAN_IF}" to "${NAS_IP}" port 2049 proto tcp  # NFS
# Allow DNS to internal resolver only
DNS_IP="${DNS_IP:-192.168.10.1}"
ufw allow out on "${LAN_IF}" to "${DNS_IP}" port 53
# Allow SMTP to internal relay only
SMTP_IP="${SMTP_IP:-192.168.10.5}"
ufw allow out on "${LAN_IF}" to "${SMTP_IP}" port 25 proto tcp

# Block all other outbound (deny-by-default enforced above)
ufw --force enable
info "UFW status:"
ufw status verbose

# ── 5. sysctl hardening ──────────────────────────────────────────────────────
info "Applying sysctl hardening..."
cat > /etc/sysctl.d/99-xray-harden.conf <<SYSCTL
# Network hardening
net.ipv4.ip_forward                     = 0
net.ipv4.conf.all.send_redirects        = 0
net.ipv4.conf.default.send_redirects    = 0
net.ipv4.conf.all.accept_redirects      = 0
net.ipv4.conf.default.accept_redirects  = 0
net.ipv4.conf.all.log_martians          = 1
net.ipv4.conf.default.log_martians      = 1
net.ipv4.conf.all.rp_filter             = 1
net.ipv4.icmp_ignore_bogus_error_responses = 1
net.ipv4.tcp_syncookies                 = 1
net.ipv4.conf.all.accept_source_route   = 0
net.ipv6.conf.all.disable_ipv6          = 1    # disable IPv6 if not used
net.ipv6.conf.default.disable_ipv6      = 1

# Kernel hardening
kernel.randomize_va_space               = 2    # ASLR full
kernel.kptr_restrict                    = 2    # hide kernel pointers
kernel.dmesg_restrict                   = 1
kernel.yama.ptrace_scope                = 1    # restrict ptrace
fs.protected_hardlinks                  = 1
fs.protected_symlinks                   = 1
fs.suid_dumpable                        = 0    # no setuid core dumps
kernel.core_uses_pid                    = 1
SYSCTL
sysctl --system -q

# ── 6. SSH hardening ─────────────────────────────────────────────────────────
info "Hardening SSH..."
SSHD_CONF=/etc/ssh/sshd_config.d/99-xray.conf
cat > "${SSHD_CONF}" <<SSH
Protocol 2
PermitRootLogin no
PasswordAuthentication no
ChallengeResponseAuthentication no
PubkeyAuthentication yes
AuthorizedKeysFile .ssh/authorized_keys
PermitEmptyPasswords no
X11Forwarding no
AllowAgentForwarding no
AllowTcpForwarding no
MaxAuthTries 3
LoginGraceTime 30
ClientAliveInterval 300
ClientAliveCountMax 2
Banner /etc/ssh/banner
# Restrict to management IP — double-enforced (UFW + SSH)
AllowUsers *@${MGT_IP}
SSH
cat > /etc/ssh/banner <<BANNER
WARNING: This system is property of Customs Authority.
Unauthorised access is prohibited and will be prosecuted.
All sessions are logged and monitored.
BANNER
systemctl restart sshd

# ── 7. fail2ban ──────────────────────────────────────────────────────────────
info "Configuring fail2ban..."
cat > /etc/fail2ban/jail.d/xray.conf <<F2B
[sshd]
enabled  = true
port     = 22
maxretry = 3
bantime  = 3600
findtime = 600

[nginx-http-auth]
enabled  = true
maxretry = 5
bantime  = 1800
F2B
systemctl enable fail2ban
systemctl restart fail2ban

# ── 8. auditd — log privileged actions ───────────────────────────────────────
info "Configuring auditd..."
cat >> /etc/audit/rules.d/xray.rules <<AUDIT
# Log all authentication events
-w /var/log/auth.log -p wa -k auth
# Log writes to sensitive files
-w /etc/xray/ -p wa -k xray_secrets
-w /opt/xray/ -p wa -k xray_deploy
-w /var/lib/xray/models/ -p wa -k model_weights
# Log Docker socket access
-w /var/run/docker.sock -p rwa -k docker
# Log use of privileged commands
-a always,exit -F arch=b64 -S execve -F uid=0 -k root_cmd
AUDIT
augenrules --load
systemctl enable auditd
systemctl restart auditd

# ── 9. AIDE (filesystem integrity monitoring) ────────────────────────────────
info "Initialising AIDE (filesystem integrity database)..."
aide --init && mv /var/lib/aide/aide.db.new /var/lib/aide/aide.db
# Daily AIDE check via cron
cat > /etc/cron.daily/aide-check <<AIDECRON
#!/bin/bash
aide --check | mail -s "[XRAY] AIDE integrity check" root 2>/dev/null || true
AIDECRON
chmod +x /etc/cron.daily/aide-check

# ── 10. Docker security ──────────────────────────────────────────────────────
info "Hardening Docker daemon..."
cat > /etc/docker/daemon.json <<DOCKER
{
  "icc":              false,
  "no-new-privileges": true,
  "log-driver":       "json-file",
  "log-opts":         { "max-size": "100m", "max-file": "5" },
  "userns-remap":     "default",
  "live-restore":     true,
  "default-ulimits":  { "nofile": { "hard": 65536, "soft": 65536 } },
  "storage-driver":   "overlay2",
  "dns":              ["${DNS_IP}"],
  "dns-search":       ["internal.local"]
}
DOCKER
systemctl restart docker

# ── 11. Restrict /proc and /sys ──────────────────────────────────────────────
info "Restricting /proc..."
echo "proc /proc proc defaults,hidepid=2,gid=proc 0 0" >> /etc/fstab

# ── 12. Service account ──────────────────────────────────────────────────────
info "Creating xray service account..."
useradd -r -s /bin/false -d /opt/xray "${XRAY_USER}" 2>/dev/null || true
usermod -aG docker "${XRAY_USER}"  # Docker socket access

# ── 13. Logrotate ────────────────────────────────────────────────────────────
cat > /etc/logrotate.d/xray <<LOGROTATE
/var/log/xray/*.log {
    daily
    rotate 90
    compress
    delaycompress
    missingok
    notifempty
    create 640 root xray
}
LOGROTATE

info "Hardening complete. REBOOT REQUIRED."
warn "After reboot: run health-check.sh to verify all controls are in effect."
warn "Then: disconnect from any external network if still connected."
