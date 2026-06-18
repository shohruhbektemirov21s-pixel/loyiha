-- PostgreSQL initialization for the X-ray assistant.
-- Runs once at first container startup (docker-entrypoint-initdb.d).
-- Principles:
--   - Admin user (POSTGRES_ADMIN_USER) owns the schema; not used by the app.
--   - App user (xray_api) has only the minimum required privileges.
--   - audit_events is APPEND-ONLY for the app user (no UPDATE/DELETE).
--   - No SUPERUSER, CREATEDB, or CREATEROLE granted to app user.

\set ON_ERROR_STOP on

-- ---------------------------------------------------------------------------
-- App user (read from environment — set by Docker at startup)
-- ---------------------------------------------------------------------------
DO $$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'xray_api') THEN
    CREATE ROLE xray_api LOGIN PASSWORD :'POSTGRES_API_PASSWORD' NOSUPERUSER NOCREATEDB NOCREATEROLE;
  END IF;
END
$$;

-- ---------------------------------------------------------------------------
-- Schema
-- ---------------------------------------------------------------------------
CREATE SCHEMA IF NOT EXISTS xray AUTHORIZATION xray_admin;
SET search_path TO xray;

-- Grant connect to app user
GRANT CONNECT ON DATABASE xray_ops TO xray_api;
GRANT USAGE  ON SCHEMA xray TO xray_api;

-- ---------------------------------------------------------------------------
-- Tables (DDL authoritative here; mirrored in app/db/schema.sql for reference)
-- ---------------------------------------------------------------------------

-- See app/db/schema.sql for the full DDL.
-- This file only creates the initial roles and grants.
-- The ORM (alembic or init_db) runs the CREATE TABLE statements.

-- Deferred grants (run after ORM creates tables):
-- GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA xray TO xray_api;
-- REVOKE UPDATE, DELETE ON xray.audit_events FROM xray_api;
-- GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA xray TO xray_api;

-- ---------------------------------------------------------------------------
-- Row-level security placeholder
-- Operators can only see their own lane_id rows.
-- Supervisors/admins bypass RLS.
-- Enable per-table after initial deployment:
--   ALTER TABLE xray.scans ENABLE ROW LEVEL SECURITY;
--   CREATE POLICY operator_lane_policy ON xray.scans
--     USING (lane_id = current_setting('app.current_lane_id', true));
-- ---------------------------------------------------------------------------

-- ---------------------------------------------------------------------------
-- Audit: make audit_events append-only for app user
-- ---------------------------------------------------------------------------
-- Applied after ORM creates the table. Script: scripts/harden-db.sh

-- ---------------------------------------------------------------------------
-- Logging configuration (also set in postgres command args in compose)
-- ---------------------------------------------------------------------------
ALTER SYSTEM SET log_connections       = 'on';
ALTER SYSTEM SET log_disconnections    = 'on';
ALTER SYSTEM SET log_statement         = 'ddl';
ALTER SYSTEM SET log_min_duration_statement = 1000;  -- log slow queries (>1s)
SELECT pg_reload_conf();
