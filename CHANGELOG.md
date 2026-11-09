# Changelog

## [0.4.0-dev] - 2026-11-09

### Added
- 「系统概览」首屏（`ui/overview_page.py`）：系统定位、解决的典型问题、
  模块地图、一次排查的完整路径、实时运行状态与影响面最大的问题簇。
- 问题聚类服务（`services/cluster_service.py`）：按「产品 + 错误码」分簇，
  错误码缺失时退回文本相似度归并；输出影响面、时间集中度、现象分组与证据量。
- 「问题聚类」页（`ui/cluster_page.py`）：簇列表、簇详情、成员列表、
  现象分组、簇成员 CSV 导出，以及「用代表反馈做单条诊断」的一键跳转。
- 深色列表组件 `data_list()`、流程步骤条 `stepper()`、空状态 `empty_state()`、
  标签 `tag()`，替换反馈分析页与簇成员的原生表格。
- 页面深链接：`?page=问题聚类` 这类 URL 可直接打开指定页面。

### Changed
- 导航重排为：系统概览 / 数据接入 / 证据关联 / 问题聚类 / 反馈分析 /
  单条诊断 / 报告中心，按排查流程排列。
- 「智能诊断」更名「单条诊断」，突出「针对单个反馈」的定位，支持从问题簇带入反馈。
- 移除原「工作台首页」，其统计内容并入系统概览。

### Testing
- 新增 `tests/test_cluster_service.py`（7 例）：分簇、拆簇、孤立反馈、
  现象分组、证据计数、空库、文本相似度边界。
- 全项目测试：87 passed。

## [0.3.0-dev] - 2026-11-09

### Added
- 深色科技风视觉系统（`ui/theme.py` + `.streamlit/config.toml`）：深空底色、
  细网格、青色光晕指标卡、渐变标题、状态胶囊；首页改为实时数据概览。
- 新增「反馈分析」页：错误码 / 版本 / 平台分布与最新反馈列表。
- Prometheus 指标连接器（`connectors/metrics/prometheus.py`）：只依赖标准库，
  支持 instant / range 查询、Bearer / Basic 认证、结果点位上限。
- metric 证据归一化：指标名、标签、数值进入 `attributes`，
  `service` 从 `service` / `job` / `app` 等标签推导。
- 上下文证据规则：指标 / 变更 / 备注按「同服务 + 同窗口」进入关联，
  关系标记为 `context`，强度固定为弱，不参与「已验证事实」。
- 诊断服务（`services/diagnosis_service.py`）：事实与推断分离、
  每条结论绑定证据 ID、下一步建议带依据、可下载 Markdown 报告。
- LLM 接入：OpenAI 兼容接口，输出做引用校验；未配置或无有效引用时退回规则诊断。
- 「智能诊断」页面替换原「AI 分析」占位页。
- 样例指标 `data/sample/prometheus_sample.json`，支持离线体验。

### Changed
- `CorrelationResult` 增加 `relation` 字段；`replace_links` 支持按 link 指定关系，
  传 `relation=None` 时整体重建锚点关联。
- 证据关联页新增「关系」列和「上下文证据」指标。
- 导航调整为：工作台首页 / 数据接入 / 证据关联 / 反馈分析 / 智能诊断 / 报告中心。

### Testing
- 新增 `tests/test_prometheus_connector.py`、`tests/test_diagnosis_service.py`，
  并为 metric 上下文证据补了关联层与服务层测试。
- 全项目测试：80 passed。

## [0.2.0-dev] - 2026-11-09

### Added
- 统一 Evidence 证据模型（`core/evidence.py`）：反馈、日志等转换为同一结构，
  保留 `attributes` 与原始 `raw_data`。
- 证据关联引擎（`core/correlation.py`）：Trace ID / Request ID / UID /
  错误码 / 服务 + 时间窗口的加法打分，输出命中键与中文理由。
- Schema v2：`evidence_records`、`evidence_links` 两张表及索引。
- 版本化 Migration 框架，支持已有 v1 库增量升级到 v2。
- `EvidenceRepository`：证据幂等写入（外部 ID / 内容指纹去重）、候选粗筛、
  关联重建与关联明细查询。
- `FeedbackRepository`：单条反馈查询、同 UID / 同错误码的相关反馈查询。
- `EvidenceService`：证据接入、问题上下文生成、事实与假设分离。
- 「证据关联」页面（`ui/evidence_page.py`）：证据接入、生成上下文、回看关联。
- 样例日志 `data/sample/log_sample.jsonl`，支持离线体验关联能力。
- 文档：`docs/EVIDENCE_MODEL.md`；`docs/DATA_MODEL.md` 新增第 13 节并清理重复章节。

### Changed
- `database/migration.py` 改为按版本执行的迁移框架，返回最终版本号。
- 公共数据层工具（ID、时间、JSON 归一）抽取到 `database/utils.py`。
- 强度判定由分数阈值改为命中键判定，避免反馈锚点永远无法达到「强关联」。

### Testing
- 新增证据模型、关联规则、Repository、Migration 升级、Service 编排测试。
- 全项目测试：57 passed。

## [0.1.0-dev] - 2026-11-03

### Added
- 初始化 WorkMesh 项目结构。
- 建立 Python 3.12 虚拟环境。
- 安装首批项目依赖。
- 创建项目规划文档。
- 创建 Streamlit 工作台首页。
- 实现侧边栏导航和首页功能入口。

### Fixed
- 修复首页数据概览区域的文字对比度。
- 修复首页功能入口的页面跳转状态。

## [Unreleased]

### Added

- 建立统一 BaseConnector 连接器接口。
- 建立 ConnectorRegistry 连接器注册中心。
- 增加 Elasticsearch 日志连接器初版。
- 支持 Elasticsearch 连接测试、日志查询和统一 Evidence 格式标准化。
- 增加连接状态管理和异常处理。
- 增加 pytest 自动化测试配置。
- 新增 Elasticsearch 连接器自动化测试。

### Architecture

- 确立预置连接器与通用 API 扩展相结合的系统集成策略。
- 规划 Integration Hub，统一管理企业系统连接器。
- 明确日志、指标、链路追踪等多源证据的统一接入方向。

### Testing

- Elasticsearch 连接器测试：6 passed。
- 全项目测试：13 passed。

### Git

- Commit: `358ff6c`
- Message: `feat: add Elasticsearch log connector`

### Planned

- 真实 Elasticsearch 服务连接验证。
- Prometheus 指标连接器。
- OpenSearch、Loki 日志连接器。
- Jaeger / Tempo 链路追踪连接器。
- Integration Hub 管理界面。
- 多源证据关联与 AI 辅助诊断。

### Architecture
- 增加整体系统架构设计文档。
- 增加性能与扩展设计文档。
- 明确 Repository 数据访问模式。
- 明确原始数据与标准化数据分离原则。
- 明确 SQLite 到 PostgreSQL 的演进方向。
- 增加大文件分块处理和后台任务设计。
- 增加 AI 分层分析和性能监控设计。

### Planned
- SQLite 数据持久化。
- 导入批次管理。
- 数据库 Migration。
- 大文件分块解析。
- 导入任务进度管理。
