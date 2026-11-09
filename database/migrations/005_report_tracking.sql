-- WorkMesh 迁移 005：报告处理记录
-- 对应版本：5
--
-- 单条诊断与簇报告存放在不同表，处理状态放在统一表里，
-- 用 scope 区分，避免给两张报告表各加一遍状态字段。

CREATE TABLE IF NOT EXISTS report_tracking (
    workspace_id TEXT NOT NULL,
    report_scope TEXT NOT NULL
        CHECK (report_scope IN ('feedback', 'cluster')),
    report_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'handled', 'ignored')),
    owner TEXT,
    note TEXT,
    updated_at TEXT NOT NULL,

    PRIMARY KEY (workspace_id, report_scope, report_id),

    FOREIGN KEY (workspace_id)
        REFERENCES workspaces(id)
        ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS idx_report_tracking_status
    ON report_tracking (workspace_id, status);
