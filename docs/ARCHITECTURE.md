# WorkMesh 系统架构设计

版本：1.0
状态：架构设计阶段
项目名称：WorkMesh
产品定位：Enterprise AI Workspace

## 1. 项目定位

WorkMesh 是面向企业多来源业务信息的 AI 分析工作台。

目标是连接不同企业产品和业务系统，将用户反馈、客服工单、群聊记录、知识库及业务 API 等信息统一接入，经过标准化处理后，提供查询、统计、关联分析和 AI 辅助决策能力。

首个应用场景：企业用户反馈智能分析。

## 2. 核心设计原则

1. 多数据源统一接入。
2. 原始数据与标准化数据分离。
3. 数据库与业务页面解耦。
4. 连接器与业务分析逻辑解耦。
5. 支持自定义字段和扩展数据。
6. 分析结论必须能够追溯数据证据。
7. 支持从单机本地部署向企业服务架构演进。
8. 优先保证数据安全、可靠性和可维护性。

## 3. 总体架构

系统划分为以下层级：

### 3.1 用户交互层

当前技术：
- Streamlit

负责：
- 工作台首页
- 数据源管理
- 文件上传
- 字段映射
- 数据预览
- 反馈分析
- AI 对话
- 报告展示
- 任务进度展示

未来可根据产品需求迁移至独立 Web 前端。

### 3.2 应用服务层

当前技术：
- Python

主要模块：
- 数据导入服务
- 数据标准化服务
- 反馈分析服务
- AI 模型服务
- 报告服务
- 审计服务

业务逻辑不直接依赖 Streamlit 页面。

未来可增加 FastAPI，为前端、连接器和外部系统提供统一 API。

### 3.3 核心编排层

主要模块：
- AI Orchestrator
- Tool Registry
- Permission Layer
- Connector Registry

负责：
- 用户意图识别
- 分析任务规划
- 工具选择
- 权限检查
- 多数据源调用
- 结果整合

### 3.4 数据接入层

首期连接器：
- CSV Connector
- Excel Connector
- Markdown Knowledge Connector
- REST API Connector

后续连接器：
- Jira
- GitHub
- Notion
- 飞书
- 企业微信
- 禅道
- 自建 CRM
- MCP Connector

不同连接器负责各自的鉴权、分页、字段转换、限流和异常处理。

### 3.5 数据存储层

当前：
- SQLite
- 本地文件存储

主要存储：
- 工作空间
- 数据源
- 导入批次
- 原始数据
- 标准化反馈记录
- 分析任务
- 分析结果
- 报告
- 审计日志

未来：
- PostgreSQL
- 对象存储
- 专用搜索引擎
- 向量检索系统

## 4. 数据处理流程

标准数据流程：

数据源
  -> 文件或 API 接入
  -> 原始数据暂存
  -> 数据解析
  -> 字段识别
  -> 字段映射
  -> 数据标准化
  -> 数据质量检查
  -> 数据库持久化
  -> 数据统计分析
  -> AI 辅助分析
  -> 报告生成

原始数据应保留，标准化过程应可追溯。

## 5. 统一反馈数据模型

核心标准字段：

- id
- workspace_id
- source_id
- batch_id
- external_id
- uid
- feedback_time
- source_type
- product
- version
- platform
- device
- error_code
- description
- status
- raw_payload
- extra_data
- created_at
- updated_at

字段说明：

raw_payload：
保存原始记录，便于数据追溯和重新处理。

extra_data：
保存不同企业的自定义字段，避免每增加一个字段就修改数据库表结构。

## 6. 数据库设计

当前数据库：SQLite。

设计要求：
- UUID 字符串作为主键。
- 统一时间格式，数据库中使用 UTC。
- JSON 扩展字段采用可迁移的文本存储方案。
- 使用外键约束。
- 根据查询场景建立索引。
- 使用事务管理批量写入。
- 数据访问统一经过 Repository。
- 数据库结构通过版本化 Migration 管理。

未来迁移目标：PostgreSQL。

禁止在 Streamlit 页面中直接编写业务 SQL。

## 7. 核心数据库实体

第一阶段：
- workspaces
- data_sources
- import_batches
- feedback_records
- audit_logs

第二阶段：
- analysis_tasks
- analysis_results
- analysis_evidence
- reports
- knowledge_documents
- knowledge_chunks

第三阶段：
- users
- workspace_members
- permissions
- connector_configs
- scheduled_tasks
- notifications
- operation_logs

## 8. AI 分析架构

采用统计预处理与大模型分析相结合的方式。

处理流程：

原始业务数据
  -> SQL/Python 统计
  -> 问题归并和聚类
  -> 代表性样本提取
  -> 大模型分析
  -> 证据关联
  -> 报告生成

原则：
- 不将大量原始记录无差别地发送给大模型。
- 统计计算优先由 SQL/Python 完成。
- 大模型负责语义理解、归纳和推理。
- 分析结论应保留数据证据和统计口径。
- 模型、供应商和参数应支持配置。

## 9. 任务处理架构

小规模任务：
- 本地同步执行。

较大规模任务：
- 后台任务执行。
- 任务状态持久化。
- 记录处理进度。
- 记录成功、失败和跳过数量。

企业部署阶段：
- 独立任务队列。
- 工作进程。
- 任务重试。
- 幂等控制。
- 任务超时和资源限制。

## 10. 安全设计

- API Key 不写入代码或 Git 仓库。
- 敏感配置通过环境变量或安全配置管理。
- 上传文件进行格式和大小校验。
- 文件名和文件路径进行安全处理。
- 不默认执行外部系统写操作。
- 外部操作必须经过权限检查。
- 重要操作写入审计日志。
- 不同工作空间的数据必须隔离。
- UID 等用户标识应支持脱敏展示。

## 11. 当前技术栈

| 模块 | 技术 |
|---|---|
| 用户界面 | Streamlit |
| 后端 | Python |
| 数据处理 | Pandas |
| 数据库 | SQLite |
| 模型接入 | OpenAI-compatible API |
| 文件格式 | CSV / Excel / Markdown |
| 测试 | Pytest |
| 版本管理 | Git |

## 12. 未来技术演进

当前阶段不提前引入所有企业级组件。

按实际需求逐步演进：

- Streamlit -> 独立 Web 前端
- Python 服务 -> FastAPI
- SQLite -> PostgreSQL
- Pandas -> Pandas / Polars
- 本地任务 -> 后台任务队列
- 本地文件 -> 对象存储
- SQL 查询 -> 专用搜索引擎
- 基础检索 -> 向量检索

具体组件以性能测试和实际业务需求为依据。
