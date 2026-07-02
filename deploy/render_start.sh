#!/usr/bin/env bash
# ============================================================
# X-ray Assistant — Render Start Script
# ============================================================
set -euo pipefail

# 1. Setup SSH key for Vast.ai SSH tunnel if provided
if [ -n "${SSH_PRIVATE_KEY:-}" ]; then
    echo "Setting up SSH key for Vast.ai tunnel..."
    mkdir -p ~/.ssh
    echo "$SSH_PRIVATE_KEY" > ~/.ssh/id_rsa
    chmod 600 ~/.ssh/id_rsa
    
    SSH_HOST="${VAST_SSH_HOST:-ssh5.vast.ai}"
    SSH_PORT="${VAST_SSH_PORT:-38056}"
    
    echo "Opening SSH tunnel to ${SSH_HOST}:${SSH_PORT}..."
    ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
        -p "$SSH_PORT" "root@${SSH_HOST}" \
        -L 11434:localhost:11434 -N -f || echo "Warning: SSH tunnel could not be opened."
fi

# 2. Run database migrations
if [ -n "${XRAY_DB_URL:-}" ]; then
    echo "Running database migrations..."
    python3 -m app.db.migrate || echo "Warning: migrations failed."
    
    # 3. Create Admin user if credentials are provided
    if [ -n "${ADMIN_USERNAME:-}" ] && [ -n "${ADMIN_PASSWORD:-}" ]; then
        echo "Creating admin user..."
        ADMIN_USERNAME="$ADMIN_USERNAME" ADMIN_PASSWORD="$ADMIN_PASSWORD" python3 deploy/create_admin.py --lane-ids "lane-1,lane-2" || echo "Warning: Admin user creation failed."
    fi
else
    echo "XRAY_DB_URL is not set. Skipping migrations and admin creation (stub mode)."
fi

# 4. Start the FastAPI application
echo "Starting FastAPI server on port ${PORT:-10000}..."
exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-10000}"
