-- WorkMesh 迁移 004：问题簇报告
-- 对应版本：4
--
-- 与单条诊断报告分开存：簇报告没有单一 feedback 锚点，而是一个问题簇的快照，
-- 包含影响面、成员分布、抽样诊断与共性证据。

CREATE TABLE IF NOT EXISTS cluster_reports (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    cluster_key TEXT NOT NULL,
    title TEXT NOT NULL,
    product TEXT,
    error_code TEXT,
    severity TEXT,
    confidence TEXT,
    size INTEGER NOT NULL DEFAULT 0,
    unique_users INTEGER NOT NULL DEFAULT 0,
    evidence_total INTEGER NOT NULL DEFAULT 0,
    representative_feedback_id TEXT,
    generated_by TEXT NOT NULL DEFAULT 'rule'
        CHECK (generated_by IN ('rule', 'llm')),
    model TEXT,
    markdown TEXT NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}',
    content_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,

    UNIQUE (id, workspace_id),

    FOREIGN KEY (workspace_id)
        REFERENCES workspaces(id)
        ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS idx_cluster_reports_workspace_created
    ON cluster_reports (workspace_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_cluster_reports_cluster
    ON cluster_reports (cluster_key);

CREATE UNIQUE INDEX IF NOT EXISTS idx_cluster_reports_dedupe
    ON cluster_reports (workspace_id, cluster_key, content_hash);
