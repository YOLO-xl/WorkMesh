-- WorkMesh SQLite Schema
-- Version: 1
-- Encoding: UTF-8

CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    description TEXT NOT NULL,
    applied_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS workspaces (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT,
    status TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'disabled')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS data_sources (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    name TEXT NOT NULL,
    source_type TEXT NOT NULL,
    connector_type TEXT,
    config_json TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'disabled', 'error')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,

    UNIQUE (id, workspace_id),

    FOREIGN KEY (workspace_id)
        REFERENCES workspaces(id)
        ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS import_batches (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    source_id TEXT NOT NULL,
    file_name TEXT,
    file_size INTEGER CHECK (file_size IS NULL OR file_size >= 0),
    file_hash TEXT,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (
            status IN (
                'pending',
                'running',
                'completed',
                'partially_failed',
                'failed',
                'cancelled'
            )
        ),
    total_records INTEGER NOT NULL DEFAULT 0
        CHECK (total_records >= 0),
    processed_records INTEGER NOT NULL DEFAULT 0
        CHECK (processed_records >= 0),
    success_records INTEGER NOT NULL DEFAULT 0
        CHECK (success_records >= 0),
    failed_records INTEGER NOT NULL DEFAULT 0
        CHECK (failed_records >= 0),
    error_summary TEXT,
    started_at TEXT,
    completed_at TEXT,
    created_at TEXT NOT NULL,

    UNIQUE (id, workspace_id),

    FOREIGN KEY (workspace_id)
        REFERENCES workspaces(id)
        ON DELETE RESTRICT,

    FOREIGN KEY (source_id, workspace_id)
        REFERENCES data_sources(id, workspace_id)
        ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS feedback_records (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    source_id TEXT NOT NULL,
    batch_id TEXT NOT NULL,
    external_id TEXT,
    uid TEXT,
    feedback_time TEXT,
    source_type TEXT,
    product TEXT,
    version TEXT,
    platform TEXT,
    device TEXT,
    error_code TEXT,
    description TEXT,
    status TEXT NOT NULL DEFAULT 'new',
    raw_payload TEXT NOT NULL DEFAULT '{}',
    extra_data TEXT NOT NULL DEFAULT '{}',
    content_hash TEXT,
    deleted_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,

    FOREIGN KEY (workspace_id)
        REFERENCES workspaces(id)
        ON DELETE RESTRICT,

    FOREIGN KEY (source_id, workspace_id)
        REFERENCES data_sources(id, workspace_id)
        ON DELETE RESTRICT,

    FOREIGN KEY (batch_id, workspace_id)
        REFERENCES import_batches(id, workspace_id)
        ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS audit_logs (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    actor_type TEXT NOT NULL,
    actor_id TEXT,
    action TEXT NOT NULL,
    resource_type TEXT,
    resource_id TEXT,
    details_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,

    FOREIGN KEY (workspace_id)
        REFERENCES workspaces(id)
        ON DELETE RESTRICT
);

-- Feedback query indexes
CREATE INDEX IF NOT EXISTS idx_feedback_workspace
    ON feedback_records(workspace_id);

CREATE INDEX IF NOT EXISTS idx_feedback_batch
    ON feedback_records(batch_id);

CREATE INDEX IF NOT EXISTS idx_feedback_source
    ON feedback_records(source_id);

CREATE INDEX IF NOT EXISTS idx_feedback_time
    ON feedback_records(feedback_time);

CREATE INDEX IF NOT EXISTS idx_feedback_workspace_time
    ON feedback_records(workspace_id, feedback_time);

CREATE INDEX IF NOT EXISTS idx_feedback_workspace_version
    ON feedback_records(workspace_id, version);

CREATE INDEX IF NOT EXISTS idx_feedback_workspace_error
    ON feedback_records(workspace_id, error_code);

-- External IDs are unique within one source among active records.
CREATE UNIQUE INDEX IF NOT EXISTS uq_feedback_source_external
    ON feedback_records(source_id, external_id)
    WHERE external_id IS NOT NULL
      AND deleted_at IS NULL;

-- Import batch indexes
CREATE INDEX IF NOT EXISTS idx_batches_workspace
    ON import_batches(workspace_id);

CREATE INDEX IF NOT EXISTS idx_batches_source
    ON import_batches(source_id);

CREATE INDEX IF NOT EXISTS idx_batches_status
    ON import_batches(status);

CREATE INDEX IF NOT EXISTS idx_batches_created
    ON import_batches(created_at);

-- Audit log indexes
CREATE INDEX IF NOT EXISTS idx_audit_workspace_created
    ON audit_logs(workspace_id, created_at);

CREATE INDEX IF NOT EXISTS idx_audit_resource
    ON audit_logs(resource_type, resource_id);
