# WorkMesh 数据模型设计

版本：1.0
状态：首版数据库设计
数据库：SQLite
未来迁移目标：PostgreSQL

## 1. 设计目标

建立统一、可扩展、可追溯的企业业务数据模型。

首版主要处理用户反馈、客服工单和群聊记录。

未来支持：
- REST API
- Jira
- GitHub
- Notion
- 飞书
- 企业微信
- 禅道
- 自建业务系统

设计原则：
1. 原始数据与标准化数据分离。
2. 数据源与导入批次分离。
3. 所有业务数据归属于工作空间。
4. 支持企业自定义字段。
5. 数据处理过程可追溯。
6. 关键操作有审计记录。
7. 结构兼容 SQLite 和 PostgreSQL。
8. 避免将数据库逻辑耦合到 Streamlit。

## 2. 主键与时间规范

主键：
- 使用 UUID 字符串。
- 统一使用 TEXT 类型存储。

时间：
- 统一使用 UTC。
- 使用 ISO 8601 格式。
- 建议格式：2026-11-03T12:00:00Z。

JSON：
- SQLite 使用 TEXT 存储 JSON。
- 应用层负责 JSON 序列化和校验。
- PostgreSQL 阶段可以根据需要使用 JSONB。

## 3. 核心实体

### 3.1 workspaces

用途：工作空间。

字段：
- id：UUID 主键
- name：工作空间名称
- description：描述
- status：状态
- created_at：创建时间
- updated_at：更新时间

### 3.2 data_sources

用途：统一管理接入的数据源。

字段：
- id：UUID 主键
- workspace_id：所属工作空间
- name：数据源名称
- source_type：数据源类型
- connector_type：连接器类型
- config_json：非敏感配置
- status：连接状态
- created_at：创建时间
- updated_at：更新时间

注意：
敏感凭据不得明文保存在 config_json 中。
API Key 应使用环境变量或安全凭据管理机制。

source_type 示例：
- csv
- excel
- rest_api
- jira
- github
- notion
- feishu
- wecom
- chat
- ticket

### 3.3 import_batches

用途：管理每次导入或同步任务。

字段：
- id：UUID 主键
- workspace_id：所属工作空间
- source_id：数据源 ID
- file_name：文件名称
- file_size：文件大小
- file_hash：文件内容哈希
- status：任务状态
- total_records：总记录数
- processed_records：已处理数量
- success_records：成功数量
- failed_records：失败数量
- error_summary：错误摘要
- started_at：开始时间
- completed_at：完成时间
- created_at：创建时间

任务状态：
- pending
- running
- completed
- partially_failed
- failed
- cancelled

### 3.4 feedback_records

用途：统一存储标准化后的反馈记录。

字段：
- id：UUID 主键
- workspace_id：工作空间 ID
- source_id：数据源 ID
- batch_id：导入批次 ID
- external_id：外部系统记录 ID
- uid：用户标识
- feedback_time：反馈时间
- source_type：反馈来源类型
- product：产品名称
- version：产品版本
- platform：系统平台
- device：设备型号
- error_code：错误码
- description：问题描述
- status：处理状态
- raw_payload：原始数据 JSON
- extra_data：自定义扩展字段 JSON
- content_hash：内容指纹
- deleted_at：软删除时间
- created_at：创建时间
- updated_at：更新时间

设计说明：

raw_payload：
保留接入时的原始字段和值，便于追溯和重新处理。

extra_data：
保存不同企业的自定义字段。

content_hash：
用于辅助识别重复数据，但不能简单地将相同问题描述视为重复反馈。

external_id：
记录外部系统 ID。同一个外部 ID 在不同数据源中可能重复，因此不能仅对 external_id 建立全局唯一约束。

### 3.5 audit_logs

用途：记录重要操作。

字段：
- id：UUID 主键
- workspace_id：工作空间 ID
- actor_type：操作主体类型
- actor_id：操作主体 ID
- action：操作类型
- resource_type：资源类型
- resource_id：资源 ID
- details_json：操作详情
- created_at：操作时间

重要操作示例：
- import_started
- import_completed
- import_failed
- record_deleted
- batch_deleted
- connector_created
- connector_updated
- external_action_executed

### 3.6 analysis_tasks

用途：管理数据分析任务。

字段：
- id：UUID 主键
- workspace_id：工作空间 ID
- task_type：任务类型
- status：任务状态
- input_json：分析参数
- result_id：结果 ID
- error_message：错误信息
- started_at：开始时间
- completed_at：完成时间
- created_at：创建时间

## 4. 后续扩展实体

第二阶段：

analysis_results：
保存统计分析和 AI 分析结果。

analysis_evidence：
关联分析结论与具体反馈记录，保存证据来源。

reports：
保存分析报告及其版本。

knowledge_documents：
保存知识库文档元数据。

knowledge_chunks：
保存文档切片及检索信息。

第三阶段：

users：
用户信息。

workspace_members：
工作空间成员及角色。

permissions：
权限策略。

connector_configs：
连接器配置及状态。

scheduled_tasks：
定时任务。

notifications：
通知记录。

## 5. 实体关系

workspaces 1:N data_sources

workspaces 1:N import_batches

workspaces 1:N feedback_records

workspaces 1:N audit_logs

workspaces 1:N analysis_tasks

data_sources 1:N import_batches

data_sources 1:N feedback_records

import_batches 1:N feedback_records

analysis_tasks 1:1 或 1:N analysis_results（按后续版本确定）

analysis_results 1:N analysis_evidence

## 6. 索引设计

首版重点考虑：

feedback_records：
- workspace_id
- batch_id
- source_id
- feedback_time
- (workspace_id, feedback_time)
- (workspace_id, version)
- (workspace_id, error_code)

import_batches：
- workspace_id
- source_id
- status
- created_at

audit_logs：
- workspace_id
- created_at
- resource_type
- resource_id

索引根据真实查询模式调整，避免过度建立索引影响写入性能。

## 7. 数据完整性

- 使用外键约束。
- 业务记录必须关联工作空间。
- 批次和数据源必须属于同一工作空间。
- 删除工作空间时不能无意间删除其他工作空间数据。
- 导入失败应保留可追溯的任务记录。
- 批次删除应采用事务和审计记录。
- 重要原始数据不应因标准化失败而丢失。

## 8. 重复数据策略

重复数据检测分层处理：

第一层：
外部系统 ID + 数据源 ID。

第二层：
文件哈希及导入批次识别。

第三层：
内容指纹辅助识别。

注意：
同一个用户可能多次反馈相同问题。
相同描述不代表同一条反馈。
系统应保留真实重复发生的反馈事件。

## 9. 数据保留与隐私

- UID 属于用户标识，应限制展示和访问。
- 原始数据应按工作空间权限访问。
- 支持敏感字段脱敏。
- 支持数据保留期限配置。
- 删除操作应记录审计信息。
- API 凭据不得写入普通业务表。

## 10. 数据库演进

首版：
SQLite。

企业部署阶段：
PostgreSQL。

迁移原则：
- 通过 Repository 隔离数据库操作。
- 通过版本化 Migration 管理结构变化。
- 避免依赖 SQLite 特有 SQL。
- JSON 操作通过数据访问层适配。
- 迁移前执行数据完整性检查。
- 保留备份和回滚方案。

## 11. 当前版本实现范围

首版实际创建：
- workspaces
- data_sources
- import_batches
- feedback_records
- audit_logs

analysis_tasks 可以在后台任务模块开发时加入。

其他扩展实体暂不创建，避免过早增加复杂度。

## 12. 数据库实现约束补充

### 12.1 工作空间隔离

data_sources 和 import_batches 均应建立：

UNIQUE(id, workspace_id)

feedback_records 使用复合外键：

(source_id, workspace_id)
引用 data_sources(id, workspace_id)。

(batch_id, workspace_id)
引用 import_batches(id, workspace_id)。

确保反馈记录、数据源、导入批次属于同一工作空间。

SQLite 外键必须在每个数据库连接中显式启用。

### 12.2 外部记录唯一性

外部记录 ID 采用数据源范围内的唯一约束。

同一数据源中，未软删除的 external_id 不允许重复。

不同数据源允许出现相同 external_id。

external_id 为空时不参与唯一性约束。

### 12.3 文件重复导入

file_hash 用于识别相同文件。

相同文件再次导入时，系统应提示历史导入记录，并允许用户确认重新导入。

不对 file_hash 建立全局唯一约束。

### 12.4 时间规范

数据库统一保存 UTC ISO 8601 时间字符串。

例如：

2026-11-03T08:30:00Z

原始时间值保留在 raw_payload 中。

### 12.5 JSON 字段规范

raw_payload、extra_data、config_json、details_json 等字段采用 JSON 字符串存储。

写入前由应用层验证 JSON 格式。

### 12.6 数据库迁移

使用 schema_migrations 表记录已执行的迁移版本。

迁移必须按版本顺序执行。

每次迁移应支持事务；对于 SQLite 不支持事务的 DDL 操作，应单独设计迁移策略。

### 12.7 SQLite 连接配置

每个连接必须启用：

PRAGMA foreign_keys = ON;

数据库启用 WAL 模式。

设置合理的 busy_timeout，降低并发读写冲突。

## 12. 数据库实现约束补充

### 12.1 工作空间隔离

data_sources 和 import_batches 均应建立：

UNIQUE(id, workspace_id)

feedback_records 使用复合外键：

(source_id, workspace_id)
引用 data_sources(id, workspace_id)。

(batch_id, workspace_id)
引用 import_batches(id, workspace_id)。

确保反馈记录、数据源、导入批次属于同一工作空间。

SQLite 外键必须在每个数据库连接中显式启用。

### 12.2 外部记录唯一性

外部记录 ID 采用数据源范围内的唯一约束。

同一数据源中，未软删除的 external_id 不允许重复。

不同数据源允许出现相同 external_id。

external_id 为空时不参与唯一性约束。

### 12.3 文件重复导入

file_hash 用于识别相同文件。

相同文件再次导入时，系统应提示历史导入记录，并允许用户确认重新导入。

不对 file_hash 建立全局唯一约束。

### 12.4 时间规范

数据库统一保存 UTC ISO 8601 时间字符串。

例如：

2026-11-03T08:30:00Z

原始时间值保留在 raw_payload 中。

### 12.5 JSON 字段规范

raw_payload、extra_data、config_json、details_json 等字段采用 JSON 字符串存储。

写入前由应用层验证 JSON 格式。

### 12.6 数据库迁移

使用 schema_migrations 表记录已执行的迁移版本。

迁移必须按版本顺序执行。

每次迁移应支持事务；对于 SQLite 不支持事务的 DDL 操作，应单独设计迁移策略。

### 12.7 SQLite 连接配置

每个连接必须启用：

PRAGMA foreign_keys = ON;

数据库启用 WAL 模式。

设置合理的 busy_timeout，降低并发读写冲突。

## 12. 数据库实现约束补充

### 12.1 工作空间隔离

data_sources 和 import_batches 均应建立：

UNIQUE(id, workspace_id)

feedback_records 使用复合外键：

(source_id, workspace_id)
引用 data_sources(id, workspace_id)。

(batch_id, workspace_id)
引用 import_batches(id, workspace_id)。

确保反馈记录、数据源、导入批次属于同一工作空间。

SQLite 外键必须在每个数据库连接中显式启用。

### 12.2 外部记录唯一性

外部记录 ID 采用数据源范围内的唯一约束。

同一数据源中，未软删除的 external_id 不允许重复。

不同数据源允许出现相同 external_id。

external_id 为空时不参与唯一性约束。

### 12.3 文件重复导入

file_hash 用于识别相同文件。

相同文件再次导入时，系统应提示历史导入记录，并允许用户确认重新导入。

不对 file_hash 建立全局唯一约束。

### 12.4 时间规范

数据库统一保存 UTC ISO 8601 时间字符串。

例如：

2026-11-03T08:30:00Z

原始时间值保留在 raw_payload 中。

### 12.5 JSON 字段规范

raw_payload、extra_data、config_json、details_json 等字段采用 JSON 字符串存储。

写入前由应用层验证 JSON 格式。

### 12.6 数据库迁移

使用 schema_migrations 表记录已执行的迁移版本。

迁移必须按版本顺序执行。

每次迁移应支持事务；对于 SQLite 不支持事务的 DDL 操作，应单独设计迁移策略。

### 12.7 SQLite 连接配置

每个连接必须启用：

PRAGMA foreign_keys = ON;

数据库启用 WAL 模式。

设置合理的 busy_timeout，降低并发读写冲突。

## 12. 数据库实现约束补充

### 12.1 工作空间隔离

data_sources 和 import_batches 均应建立：

UNIQUE(id, workspace_id)

feedback_records 使用复合外键：

(source_id, workspace_id)
引用 data_sources(id, workspace_id)。

(batch_id, workspace_id)
引用 import_batches(id, workspace_id)。

确保反馈记录、数据源、导入批次属于同一工作空间。

SQLite 外键必须在每个数据库连接中显式启用。

### 12.2 外部记录唯一性

外部记录 ID 采用数据源范围内的唯一约束。

同一数据源中，未软删除的 external_id 不允许重复。

不同数据源允许出现相同 external_id。

external_id 为空时不参与唯一性约束。

### 12.3 文件重复导入

file_hash 用于识别相同文件。

相同文件再次导入时，系统应提示历史导入记录，并允许用户确认重新导入。

不对 file_hash 建立全局唯一约束。

### 12.4 时间规范

数据库统一保存 UTC ISO 8601 时间字符串。

例如：

2026-11-03T08:30:00Z

原始时间值保留在 raw_payload 中。

### 12.5 JSON 字段规范

raw_payload、extra_data、config_json、details_json 等字段采用 JSON 字符串存储。

写入前由应用层验证 JSON 格式。

### 12.6 数据库迁移

使用 schema_migrations 表记录已执行的迁移版本。

迁移必须按版本顺序执行。

每次迁移应支持事务；对于 SQLite 不支持事务的 DDL 操作，应单独设计迁移策略。

### 12.7 SQLite 连接配置

每个连接必须启用：

PRAGMA foreign_keys = ON;

数据库启用 WAL 模式。

设置合理的 busy_timeout，降低并发读写冲突。
