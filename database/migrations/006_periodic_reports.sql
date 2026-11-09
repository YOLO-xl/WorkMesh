-- WorkMesh 迁移 006：周期性分析报告
-- 对应版本：6

CREATE TABLE IF NOT EXISTS periodic_reports (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    period_start TEXT NOT NULL,
    period_end TEXT NOT NULL,
    product TEXT NOT NULL DEFAULT '',
    title TEXT NOT NULL,
    markdown TEXT NOT NULL,
    stats TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,

    FOREIGN KEY (workspace_id)
        REFERENCES workspaces(id)
        ON DELETE RESTRICT
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_periodic_reports_dedupe
    ON periodic_reports (
        workspace_id, period_start, period_end, product, content_hash
    );

CREATE INDEX IF NOT EXISTS idx_periodic_reports_created
    ON periodic_reports (workspace_id, created_at DESC);
