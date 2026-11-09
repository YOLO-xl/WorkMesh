-- WorkMesh 迁移 003：诊断报告沉淀
-- 对应版本：3
--
-- 设计要点：
-- 1. 每条报告保存渲染好的 Markdown 与完整诊断结果（payload_json），
--    既能直接展示下载，也能回放出当时的证据引用。
-- 2. 冗余存产品、错误码与反馈摘要，便于列表筛选，不必回查反馈表。
-- 3. content_hash 用于去重：同一反馈、同一内容重复保存时复用已有报告。

CREATE TABLE IF NOT EXISTS diagnosis_reports (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    feedback_id TEXT NOT NULL,
    title TEXT NOT NULL,
    product TEXT,
    error_code TEXT,
    feedback_summary TEXT,
    severity TEXT,
    confidence TEXT,
    generated_by TEXT NOT NULL DEFAULT 'rule'
        CHECK (generated_by IN ('rule', 'llm')),
    model TEXT,
    window_minutes INTEGER NOT NULL DEFAULT 30,
    min_score REAL NOT NULL DEFAULT 30,
    matched_count INTEGER NOT NULL DEFAULT 0,
    context_count INTEGER NOT NULL DEFAULT 0,
    markdown TEXT NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}',
    content_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,

    UNIQUE (id, workspace_id),

    FOREIGN KEY (workspace_id)
        REFERENCES workspaces(id)
        ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS idx_diagnosis_reports_workspace_created
    ON diagnosis_reports (workspace_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_diagnosis_reports_feedback
    ON diagnosis_reports (feedback_id);

CREATE UNIQUE INDEX IF NOT EXISTS idx_diagnosis_reports_dedupe
    ON diagnosis_reports (workspace_id, feedback_id, content_hash);
