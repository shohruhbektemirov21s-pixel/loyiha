-- ============================================================
-- X-Ray Assistant — PostgreSQL Schema  (v1.0)
-- Authoritative DDL.  Source of truth for app/db/models.py.
-- Run as the DB owner; the API role (xray_api) is granted below.
--
-- Security posture:
--   * xray_api has NO DELETE on audit_events (append-only guarantee).
--   * xray_api has NO DROP TABLE / ALTER TABLE on any table.
--   * Sensitive columns (hashed_password) are in a table only the app reads.
--   * All timestamps are WITH TIME ZONE (stored as UTC, displayed per locale).
--   * JSONB payloads are indexed with GIN where query patterns demand it.
-- ============================================================

BEGIN;

-- ------------------------------------------------------------
-- Roles and schema
-- ------------------------------------------------------------
-- Tables live in the default `public` schema — this matches the ORM
-- (app/db/models.py uses unqualified __tablename__) and the raw
-- connections in deploy/create_admin.py. Do NOT create a separate `xray`
-- schema here: the application never sets search_path to it, so any table
-- created outside `public` would be invisible at runtime.
SET search_path TO public;

-- Application user (least-privilege role, created outside this script on a
-- self-managed box):  CREATE ROLE xray_api LOGIN PASSWORD '...';
-- On single-role managed platforms (Render, Heroku, …) this role does not
-- exist and the app connects as the DB owner — the grants below are applied
-- conditionally so their absence never aborts the migration.

-- ------------------------------------------------------------
-- Sequence for audit chain ordering
-- ------------------------------------------------------------
CREATE SEQUENCE IF NOT EXISTS audit_event_seq
    START 1 INCREMENT 1 NO MAXVALUE;

-- ------------------------------------------------------------
-- operators
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS operators (
    operator_id     UUID            PRIMARY KEY DEFAULT gen_random_uuid(),
    username        VARCHAR(64)     NOT NULL,
    hashed_password VARCHAR(128)    NOT NULL,
    role            VARCHAR(32)     NOT NULL
                    CHECK (role IN ('operator','supervisor','admin')),
    lane_ids        JSONB           NOT NULL DEFAULT '[]',
    is_active       BOOLEAN         NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ     NOT NULL DEFAULT now(),
    last_login_at   TIMESTAMPTZ,

    CONSTRAINT uq_operators_username UNIQUE (username)
);

CREATE INDEX IF NOT EXISTS ix_operators_username ON operators (username);
CREATE INDEX IF NOT EXISTS ix_operators_role     ON operators (role);

-- ------------------------------------------------------------
-- scans
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS scans (
    scan_id       UUID        PRIMARY KEY,
    scanner_id    VARCHAR(64) NOT NULL,
    lane_id       VARCHAR(64),
    subject       VARCHAR(32) NOT NULL,   -- ScanSubject value
    modality      VARCHAR(32) NOT NULL,   -- ImageModality value
    state         VARCHAR(32) NOT NULL DEFAULT 'pending'
                  CHECK (state IN ('pending','analyzing','analyzed',
                                   'verdicted','reviewing','decided','error')),
    overall_risk  VARCHAR(16) CHECK (overall_risk IN ('clear','low','medium','high')),
    acquired_at   TIMESTAMPTZ NOT NULL,
    analyzed_at   TIMESTAMPTZ,
    verdicted_at  TIMESTAMPTZ,
    decided_at    TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS ix_scans_state_acquired ON scans (state, acquired_at);
CREATE INDEX IF NOT EXISTS ix_scans_scanner_id     ON scans (scanner_id);
CREATE INDEX IF NOT EXISTS ix_scans_lane_id        ON scans (lane_id);
CREATE INDEX IF NOT EXISTS ix_scans_acquired_at    ON scans (acquired_at);

-- ------------------------------------------------------------
-- scan_detections
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS scan_detections (
    detection_id  UUID        PRIMARY KEY,
    scan_id       UUID        NOT NULL REFERENCES scans (scan_id) ON DELETE CASCADE,
    frame_id      VARCHAR(64) NOT NULL,
    category      VARCHAR(32) NOT NULL,
    native_label  VARCHAR(64) NOT NULL,
    score         REAL        NOT NULL CHECK (score >= 0 AND score <= 1),
    box_x         INTEGER     NOT NULL,
    box_y         INTEGER     NOT NULL,
    box_width     INTEGER     NOT NULL CHECK (box_width > 0),
    box_height    INTEGER     NOT NULL CHECK (box_height > 0),
    calibrated    BOOLEAN     NOT NULL DEFAULT FALSE
);

CREATE INDEX IF NOT EXISTS ix_scan_detections_scan_id  ON scan_detections (scan_id);
CREATE INDEX IF NOT EXISTS ix_scan_detections_category ON scan_detections (category);

-- ------------------------------------------------------------
-- scan_verdicts  (one per scan, UNIQUE on scan_id)
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS scan_verdicts (
    verdict_id            UUID        PRIMARY KEY,
    scan_id               UUID        NOT NULL REFERENCES scans (scan_id) ON DELETE CASCADE,
    overall_risk          VARCHAR(16) NOT NULL,
    summary_uz            TEXT        NOT NULL,
    model_name            VARCHAR(64) NOT NULL,
    model_version         VARCHAR(32) NOT NULL,
    model_weights_sha256  VARCHAR(64),
    per_detection_json    JSONB       NOT NULL DEFAULT '[]',
    generated_at          TIMESTAMPTZ NOT NULL,

    CONSTRAINT uq_scan_verdicts_scan_id UNIQUE (scan_id)
);

CREATE INDEX IF NOT EXISTS ix_scan_verdicts_scan_id     ON scan_verdicts (scan_id);
CREATE INDEX IF NOT EXISTS ix_scan_verdicts_overall_risk ON scan_verdicts (overall_risk);

-- ------------------------------------------------------------
-- scan_feedback  (one per scan, UNIQUE on scan_id)
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS scan_feedback (
    feedback_id      UUID        PRIMARY KEY,
    scan_id          UUID        NOT NULL REFERENCES scans (scan_id) ON DELETE CASCADE,
    operator_id      VARCHAR(64) NOT NULL,
    outcome          VARCHAR(32) NOT NULL,
    n_gold_labels    INTEGER     NOT NULL DEFAULT 0,
    n_hard_negatives INTEGER     NOT NULL DEFAULT 0,
    reviews_json     JSONB       NOT NULL DEFAULT '[]',
    missed_json      JSONB       NOT NULL DEFAULT '[]',
    decided_at       TIMESTAMPTZ NOT NULL,
    emitted_at       TIMESTAMPTZ NOT NULL,

    CONSTRAINT uq_scan_feedback_scan_id UNIQUE (scan_id)
);

CREATE INDEX IF NOT EXISTS ix_scan_feedback_scan_id     ON scan_feedback (scan_id);
CREATE INDEX IF NOT EXISTS ix_scan_feedback_operator_id ON scan_feedback (operator_id);
CREATE INDEX IF NOT EXISTS ix_scan_feedback_decided_at  ON scan_feedback (decided_at);

-- ------------------------------------------------------------
-- audit_events  — APPEND-ONLY; xray_api has NO DELETE/UPDATE
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS audit_events (
    event_id      UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    seq           BIGINT      NOT NULL DEFAULT nextval('audit_event_seq'),
    prev_event_id UUID        REFERENCES audit_events (event_id),
    scan_id       UUID        REFERENCES scans (scan_id),
    operator_id   VARCHAR(64),
    event_type    VARCHAR(64) NOT NULL,
    payload       JSONB       NOT NULL DEFAULT '{}',
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    event_hmac    VARCHAR(64) NOT NULL  -- HMAC-SHA256 hex; see app/audit/sink.py
);

CREATE UNIQUE INDEX IF NOT EXISTS ix_audit_events_seq
    ON audit_events (seq);
CREATE INDEX IF NOT EXISTS ix_audit_events_scan_id
    ON audit_events (scan_id);
CREATE INDEX IF NOT EXISTS ix_audit_events_operator_id
    ON audit_events (operator_id);
CREATE INDEX IF NOT EXISTS ix_audit_events_event_type
    ON audit_events (event_type);
CREATE INDEX IF NOT EXISTS ix_audit_events_created_at
    ON audit_events (created_at);

-- GIN index for payload queries (e.g. WHERE payload @> '{"risk":"HIGH"}')
CREATE INDEX IF NOT EXISTS ix_audit_events_payload_gin
    ON audit_events USING GIN (payload);

-- ------------------------------------------------------------
-- threshold_configs  (one active row per category)
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS threshold_configs (
    config_id             UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    category              VARCHAR(32) NOT NULL,
    alert_threshold       REAL        NOT NULL CHECK (alert_threshold BETWEEN 0 AND 1),
    auto_clear_threshold  REAL        NOT NULL CHECK (auto_clear_threshold BETWEEN 0 AND 1),
    updated_by            VARCHAR(64) NOT NULL,
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    is_active             BOOLEAN     NOT NULL DEFAULT TRUE,
    note                  TEXT,

    CONSTRAINT chk_threshold_ordering
        CHECK (auto_clear_threshold <= alert_threshold)
);

-- Only one active row per category at a time.
CREATE UNIQUE INDEX IF NOT EXISTS uq_threshold_configs_category_active
    ON threshold_configs (category)
    WHERE is_active = TRUE;

CREATE INDEX IF NOT EXISTS ix_threshold_configs_category
    ON threshold_configs (category, is_active);

-- ------------------------------------------------------------
-- Grants — xray_api role has no DELETE on audit_events.
-- Applied only when the role exists (self-managed deploy); on managed
-- single-role platforms the block is a no-op so migration never aborts.
-- ------------------------------------------------------------
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'xray_api') THEN
        GRANT SELECT, INSERT, UPDATE ON operators, scans,
            scan_detections, scan_verdicts, scan_feedback,
            threshold_configs TO xray_api;
        GRANT SELECT, INSERT ON audit_events TO xray_api;    -- NO UPDATE, NO DELETE
        GRANT USAGE ON SEQUENCE audit_event_seq TO xray_api;
        -- Operators table: the API also needs to update last_login_at
        GRANT UPDATE (last_login_at, is_active) ON operators TO xray_api;
    END IF;
END
$$;

-- ------------------------------------------------------------
-- Seed: default thresholds (conservative — alert early, clear cautiously)
-- ------------------------------------------------------------
INSERT INTO threshold_configs
    (category, alert_threshold, auto_clear_threshold, updated_by, note)
VALUES
    ('narcotics',        0.60, 0.20, 'system', 'Initial conservative defaults'),
    ('firearm',          0.55, 0.20, 'system', NULL),
    ('bladed_weapon',    0.55, 0.20, 'system', NULL),
    ('explosive',        0.50, 0.15, 'system', 'Explosives: alert early'),
    ('currency',         0.65, 0.25, 'system', NULL),
    ('organic_anomaly',  0.70, 0.30, 'system', NULL),
    ('metallic_anomaly', 0.70, 0.30, 'system', NULL),
    ('contraband_other', 0.65, 0.25, 'system', NULL)
ON CONFLICT DO NOTHING;

COMMIT;
