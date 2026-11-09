-- WorkMesh 迁移 002：统一证据模型与证据关联
-- 对应版本：2
--
-- 设计要点：
-- 1. evidence_records 保存从任意连接器接入的标准化证据（日志、指标、链路、变更等）。
-- 2. 反馈记录仍保存在 feedback_records，分析时转换为 Evidence，避免重复存储。
-- 3. evidence_links 记录「锚点（反馈/证据/分析）↔ 证据」的可解释关联，
--    分数、命中键和理由全部落库，保证结论可回溯。

CREATE TABLE IF NOT EXISTS evidence_records (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    source_id TEXT NOT NULL,
    evidence_type TEXT NOT NULL
        CHECK (
            evidence_type IN (
                'feedback', 'log', 'metric', 'trace', 'change', 'note'
            )
        ),
    connector_type TEXT,
    record_id TEXT,
    timestamp TEXT,
    uid TEXT,
    request_id TEXT,
    trace_id TEXT,
    service TEXT,
    level TEXT,
    error_code TEXT,
    title TEXT,
    message TEXT,
    attributes_json TEXT NOT NULL DEFAULT '{}',
    raw_data TEXT NOT NULL DEFAULT '{}',
    content_hash TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,

    UNIQUE (id, workspace_id),

    FOREIGN KEY (workspace_id)
        REFERENCES workspaces(id)
        ON DELETE RESTRICT,

    FOREIGN KEY (source_id, workspace_id)
        REFERENCES data_sources(id, workspace_id)
        ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS evidence_links (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    anchor_type TEXT NOT NULL
        CHECK (anchor_type IN ('feedback', 'evidence', 'analysis')),
    anchor_id TEXT NOT NULL,
    evidence_id TEXT NOT NULL,
    relation TEXT NOT NULL DEFAULT 'correlates'
        CHECK (
            relation IN ('correlates', 'supports', 'contradicts', 'context')
        ),
    score REAL NOT NULL DEFAULT 0 CHECK (score >= 0 AND score <= 100),
    strength TEXT NOT NULL DEFAULT 'weak'
        CHECK (strength IN ('strong', 'medium', 'weak')),
    matched_keys_json TEXT NOT NULL DEFAULT '[]',
    reasons_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,

    UNIQUE (workspace_id, anchor_type, anchor_id, evidence_id, relation),

    FOREIGN KEY (workspace_id)
        REFERENCES workspaces(id)
        ON DELETE RESTRICT,

    FOREIGN KEY (evidence_id, workspace_id)
        REFERENCES evidence_records(id, workspace_id)
        ON DELETE CASCADE
);

-- Evidence query indexes
CREATE INDEX IF NOT EXISTS idx_evidence_workspace_type
    ON evidence_records(workspace_id, evidence_type);

CREATE INDEX IF NOT EXISTS idx_evidence_workspace_time
    ON evidence_records(workspace_id, timestamp);

CREATE INDEX IF NOT EXISTS idx_evidence_uid
    ON evidence_records(workspace_id, uid);

CREATE INDEX IF NOT EXISTS idx_evidence_trace
    ON evidence_records(workspace_id, trace_id);

CREATE INDEX IF NOT EXISTS idx_evidence_request
    ON evidence_records(workspace_id, request_id);

CREATE INDEX IF NOT EXISTS idx_evidence_error
    ON evidence_records(workspace_id, error_code, timestamp);

-- 同一数据源内，外部记录 ID 唯一；重复接入同一批日志时直接复用已有记录。
CREATE UNIQUE INDEX IF NOT EXISTS uq_evidence_source_record
    ON evidence_records(source_id, record_id)
    WHERE record_id IS NOT NULL;

-- Evidence link indexes
CREATE INDEX IF NOT EXISTS idx_link_anchor
    ON evidence_links(workspace_id, anchor_type, anchor_id);

CREATE INDEX IF NOT EXISTS idx_link_evidence
    ON evidence_links(evidence_id);
