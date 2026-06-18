"""Bootstrap the first admin operator in the database.

Run this ONCE after the database is initialised to create the initial admin
account. The admin can then create operator and supervisor accounts via the
API.

Usage:
    python deploy/create_admin.py \
        --username admin \
        --password "YourSecurePassword123!" \
        --lane-ids "lane-1,lane-2"

    # Or set all values via environment variables:
    ADMIN_USERNAME=admin ADMIN_PASSWORD=secret python deploy/create_admin.py

Requires XRAY_DB_URL to be set (reads from .env automatically).
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import logging
import os
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger("create_admin")


async def create_admin(
    db_url: str,
    username: str,
    password: str,
    lane_ids: list[str],
) -> None:
    import asyncpg

    raw_url = db_url.replace("postgresql+asyncpg://", "postgresql://")
    conn = await asyncpg.connect(raw_url)

    # Hash password using bcrypt (same as app/auth/backend.py)
    import bcrypt
    hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()

    try:
        result = await conn.fetchrow(
            """
            INSERT INTO operators (username, hashed_password, role, lane_ids, is_active)
            VALUES ($1, $2, 'admin', $3, true)
            ON CONFLICT (username) DO UPDATE
                SET hashed_password = EXCLUDED.hashed_password,
                    role = 'admin',
                    is_active = true
            RETURNING operator_id, username, role
            """,
            username,
            hashed,
            lane_ids,
        )
        log.info(
            "Admin created/updated: id=%s username=%s role=%s",
            result["operator_id"],
            result["username"],
            result["role"],
        )
    finally:
        await conn.close()


def load_env() -> None:
    env_file = Path(".env")
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if "=" in line and not line.startswith("#"):
                key, _, value = line.partition("=")
                os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def main() -> None:
    load_env()

    parser = argparse.ArgumentParser(description="Create the first admin operator")
    parser.add_argument("--username", default=os.environ.get("ADMIN_USERNAME", "admin"))
    parser.add_argument("--password", default=os.environ.get("ADMIN_PASSWORD"))
    parser.add_argument("--lane-ids", default="", help="Comma-separated lane IDs")
    parser.add_argument("--db-url",   default=os.environ.get("XRAY_DB_URL"))
    args = parser.parse_args()

    if not args.db_url:
        log.error("XRAY_DB_URL is not set. Set it in .env or pass --db-url.")
        sys.exit(1)

    password = args.password
    if not password:
        password = getpass.getpass(f"Password for '{args.username}': ")
    if len(password) < 12:
        log.error("Password must be at least 12 characters.")
        sys.exit(1)

    lane_ids = [l.strip() for l in args.lane_ids.split(",") if l.strip()]

    asyncio.run(create_admin(args.db_url, args.username, password, lane_ids))
    log.info("Done. You can now log in at POST /v1/auth/login")


if __name__ == "__main__":
    main()
