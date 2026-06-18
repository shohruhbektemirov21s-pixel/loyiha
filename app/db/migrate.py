"""Database migration runner.

Executes ``schema.sql`` against the configured PostgreSQL database.
Called from ``deploy/start.sh`` before the API server starts.

Usage:
    # Run as module (reads XRAY_DB_URL from environment / .env):
    python -m app.db.migrate

    # Or pass a DSN directly:
    python -m app.db.migrate postgresql://xray:password@localhost:5432/xray

The script is idempotent: every CREATE TABLE statement in schema.sql uses
``IF NOT EXISTS`` and every index uses ``IF NOT EXISTS``, so re-running it
against an already-initialised database is safe.

If XRAY_DB_URL is not set the script exits 0 with an info message (so
``start.sh`` can call it unconditionally without failing on stub boxes).
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path

log = logging.getLogger("xray.db.migrate")


async def run_migrations(db_url: str) -> None:
    """Execute schema.sql against the given PostgreSQL DSN."""
    # Convert asyncpg DSN to psycopg-style for asyncpg raw connection
    # asyncpg uses postgresql+asyncpg:// scheme; raw connections use plain postgresql://
    raw_url = db_url.replace("postgresql+asyncpg://", "postgresql://")

    try:
        import asyncpg  # type: ignore[import]
    except ImportError:
        raise RuntimeError("asyncpg is required for migrations. Run: pip install asyncpg")

    schema_path = Path(__file__).parent / "schema.sql"
    if not schema_path.exists():
        raise FileNotFoundError(f"schema.sql not found at {schema_path}")

    sql = schema_path.read_text(encoding="utf-8")

    log.info("Connecting to database for migration…")
    conn = await asyncpg.connect(raw_url)
    try:
        log.info("Executing schema.sql (%d bytes)…", len(sql))
        await conn.execute(sql)
        log.info("Migrations complete.")
    finally:
        await conn.close()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    db_url = None
    if len(sys.argv) > 1:
        db_url = sys.argv[1]
    else:
        # Try loading .env
        env_file = Path(".env")
        if env_file.exists():
            for line in env_file.read_text().splitlines():
                line = line.strip()
                if line.startswith("XRAY_DB_URL="):
                    db_url = line.split("=", 1)[1].strip().strip('"').strip("'")
                    break
        db_url = db_url or os.environ.get("XRAY_DB_URL")

    if not db_url:
        log.info("XRAY_DB_URL not set — skipping migrations (stub mode).")
        sys.exit(0)

    try:
        asyncio.run(run_migrations(db_url))
    except Exception as exc:
        log.error("Migration failed: %s", exc, exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
