# Changelog

## [0.7.0-dev] - 2026-11-09

### Added
- 直播进入房间失败场景 `live.enter_room_failure`（priority 75）：4 个 source /
  5 个 signal / 5 个 step / 5 条规则；支持 `attributes.metric_value` 点路径取值，
  指标信号不再恒为 None；规则输出带真实 `evidence_ids`。
- `MultiSourceQueryService`：本地反馈 + 本地证据 + 可选 Elasticsearch /
  Prometheus 连接器统一查询，支持跨平台过滤、去重、关联打分与原始记录追溯；
  外部源失败保留 `available:false`，不伪造数据。
- `TroubleshootingService` + 「故障排查」页面：以 F001 进入直播间失败为样例，
  串起反馈接入 → 多系统查询 → 证据关联 → 场景判定 → 结论与追溯；
  五段状态、证据链、场景事实 / 假设 / 动作、原始记录回看与 Markdown 下载。
- `FeedbackRepository.query_feedback()` 增加 `keyword`；`EvidenceRepository.query_evidence()`
  增加 `titles` / `keyword`；新增 `get_source()` 供原始来源展示。
- `DiagnosisService.build()` 返回完整 `context`，排查服务可直接复用关联结果。
- `tests/test_multi_source_query_service.py` 4 项、`tests/test_troubleshooting_service.py`
  2 项；测试总数 137 → 144。
- 故障场景模型第一阶段（`docs/FAULT_SCENARIO_MODEL.md` +
  `services/fault_scenario_service.py`）：JSON 配置 + 声明式规则，
  支持场景注册、校验、匹配、信号计算、步骤判定、规则执行、置信度降级与
  禁用根因词校验；配置目录 `configs/fault_scenarios/`。
- 四个首批场景：`login.code_delivery`（登录验证码收不到）、
  `live.enter_room_failure`（直播进入房间失败）、`payment.result_mismatch`
  （支付结果不一致）、`generic.triage`（通用兜底）。
- 场景数据源：本地 `feedback` / `evidence` / `manual`；`connector` 通过可注入
  的 `connector_provider` 预留，真实 ES / Prometheus 仍属阶段 2。
- 单条诊断接入场景运行结果：诊断页新增「故障场景」卡片（命中场景、状态、
  置信度、排查步骤表、缺失源、结论边界），Markdown 报告同步新增「故障场景」段；
  场景动作合并进「下一步建议」。
- 诊断页新增「已有请求级 Trace ID / Request ID」勾选项，驱动 `manual` 源；
  `DiagnosisService.build()` 支持 `manual_answers`。
- `FeedbackRepository.query_feedback()` 与 `EvidenceRepository.query_evidence()`
  增加按产品、错误码、UID、服务、Trace / Request ID 与时间窗口过滤。
- `docs/FUNCTIONAL_BOUNDARY_AUDIT.md`：现有代码与功能边界审计，区分真完成 /
  未验证 / 初步实现 / 仅接口。
- `tests/test_fault_scenario_service.py` 12 项回归（含直播场景与嵌套指标取值），
  全量测试 144。
- 周期性分析报告（Schema v6 `periodic_reports`，`PeriodicReportService` /
  `PeriodicReportRepository`）：按日期区间汇总反馈量、涉及用户、每日趋势、
  「产品 + 错误码」主要问题、无码现象词、报告沉淀与处理状态；
  支持全产品 / 单产品范围，内容 hash 去重、关键词检索、Markdown 下载与两步删除。
- 报告中心新增「周期报告」页签，与诊断报告、簇报告、LLM 配置并列；
  报告总数合并三类归档，待处理仍只统计可跟踪的诊断与簇报告。
- 周期报告显式写出统计口径与结论边界：只做汇总，不判断根因，不把簇当根因结论。
- 证据上下文新增跨源统计（`by_type` / `by_connector` / `source_kinds`），
  「证据关联」结果页显示跨源分布。
- `tests/test_periodic_report.py` 4 项回归。

### Changed
- Migration 升级到 v6；`tests/test_report_tracking.py` 的版本断言改为跟
  `CURRENT_VERSION` 派生，避免每次加迁移都要改测试。

### Not done
- 真实 Elasticsearch / Prometheus 实例联调与阈值校准：本机无可用实例，跳过。
- 真实 LLM 凭据线上诊断：未提供 Key，跳过。
- Jaeger / Tempo、OpenSearch / Loki 真实接入：无实例，仅保留后续扩展位。
- 大文件后台导入与分块解析：未完成，当前仍整文件读入内存。

## [0.6.0-dev] - 2026-11-09

### Added
- 症状词典（`services/symptom_lexicon.py`）：13 类用户口语现象、约 40 个功能实体、
  套话与停用词表；`normalize_text` 去客套话/URL、`extract_symptoms`、
  `extract_features`、`keywords`、`is_suggestion`。
- 无错误码聚类：`_group_without_error_code` 用「现象覆盖 + 实体覆盖 + 3-gram 相似
  + 版本/平台一致」加权打分，增量归簇；实体冲突扣分，超过 21 天时间跨度不合并。
- 未归类池：既没有现象也没有实体、或是产品建议的反馈不强行归簇，
  聚类页单独展开并说明原因。
- 簇关键词：卡片与详情展示高频词（现象词加权），详情用字号变化的 HTML 词云呈现。
- 「反馈全景」（`ui/analytics_page.py`）：可识别现象、无错误码反馈、无错误码占比、
  提及功能数，配现象/功能条形图与按现象下钻。
- 100 条无错误码样例（`data/sample/feedback_sample_no_code.csv`）与回归测试。
- `WORKMESH_DB_PATH` 环境变量可指定库文件，便于用样例库起演示实例。
- 诊断报告中心（Schema v3 `diagnosis_reports`）：单条诊断自动 / 手动归档，
  `ReportRepository` 提供保存（内容 hash 去重）、检索、详情、删除与统计。
- 「报告中心」页面：报告指标、关键词与生成方式筛选、展开全文、下载 Markdown、
  两步删除；同一页签「LLM 配置」管理接入。
- LLM 配置双通道（`services/llm_config.py`）：环境变量优先、本机
  `.workmesh/llm.local.json` 兜底；支持保存、测试连接、清除，文件 0600 原子写。
- LLM 服务商预设：OpenAI、DeepSeek、通义千问、Kimi、智谱 GLM、SiliconFlow、
  本地 Ollama 与自定义网关；切换服务商自动带出兼容地址和常用模型，模型仍可改。
- 诊断页新增「生成后自动归档到报告中心」与「保存到报告中心」入口。
- 问题簇报告（Schema v4 `cluster_reports`）：从问题聚类页一键汇总整簇，
  内容含影响面、时间集中度、现象 / 版本 / 平台分布、抽样诊断与共性证据，
  规则汇总不调用 LLM，结论边界明确「簇不等于根因」。
- `ClusterReportService` 抽样成员（代表 + 最新 + 最早，再按时间补齐）做单条诊断，
  抽样条数可在簇详情选择 3 / 5 / 8 条；汇总证据引用。
- `ClusterReportRepository` 支持保存去重、检索、删除、统计与同问题历史快照。
- 报告中心新增「簇报告」页签，支持关键词 / 产品 / 严重程度筛选、展开全文、
  下载与两步删除；同一产品 + 错误码的报告显示较上次快照的反馈 / 用户 / 证据变化。
- 顶部指标合并单条与簇报告；`tests/test_cluster_report.py` 6 项回归，测试总数 112。
- 报告处理闭环（Schema v5 `report_tracking`）：单条诊断与簇报告统一加
  「待处理 / 已处理 / 已忽略」状态、负责人与备注，按报告类型关联同一张表。
- 报告中心两张列表都新增处理状态筛选，指标区新增「待处理」；保存处理记录后
  列表即时刷新，删除报告同时清理处理记录。
- 批量处理：按当前筛选结果一次更新多份报告，适合先按产品 / 严重度缩小范围，
  再统一标记已处理或已忽略；负责人 / 备注留空时保留各报告原值，并在提交前要求确认。
- 报告列表标题显示处理状态（如 `[已处理]`）；已处理 / 已忽略默认锁定处理记录，
  需要修改时先点「重新打开处理」回到待处理，避免已关闭报告被随手改动。
- `tests/test_report_tracking.py` 10 项回归，测试总数 122。

### Changed
- 报告中心指标区把「LLM 生成」替换为「待处理」，优先暴露需要跟进的报告。
- 无错误码的簇标题改为「产品 · 主导现象」；现象标签词表补充白屏、卡死、数据等。
- 诊断服务读取 `resolve_settings()`：环境变量或本机文件任一可用即启用 LLM，
  并暴露 `llm_source`（env / file / none / injected）。

### Fixed
- 无错误码的簇报告按「产品 + 标题」归并快照，避免同一产品下不同现象被当成同一个问题。
- Streamlit 1.67 展开器偶发停在收起高度，点开报告看不到正文；统一放开
  `details[open]` 高度并压缩报告正文标题字号。
- 重新生成诊断时沿用上一次的归档提示（改为每次生成先重置归档状态）。
- `.gitignore` 补充 `.workmesh/`，本机 LLM 凭据不会误提交。

## [0.5.0-dev] - 2026-11-09

### Added
- 顶部导航栏（`ui/topbar.py`）：品牌、当前页面、全局搜索、通知、帮助、个人入口；
  通知读最近反馈，帮助给使用说明，个人入口展示工作空间与运行信息。
- 首页欢迎区：问候语 + 一句话核心价值 + 日期时间 + 4 个常用操作入口。
- 工作动态区：最近访问（本次会话）、待处理事项、运行状态、最新反馈、常用工具。
- 首页模块搜索：按模块名 / 标签过滤；命中错误码或反馈编号时可直接跳到单条诊断。

### Changed
- 首页重构为三层布局：顶栏 → 欢迎区 → 核心模块卡片 → 工作动态；
  模块按「核心排查链路 / 分析与沉淀」分组，卡片含图标、简介、标签、真实数据状态。
  卡片整块可点击（透明按钮覆盖整卡），悬停有轻微上浮与描边变化。
- 设计系统重做：深蓝黑底 + 蓝紫渐变强调、玻璃面板、统一 16px 圆角与细边框、
  克制阴影；移除网格、霓虹描边等装饰，保留克制的悬停与入场位移动效。

### Fixed
- 样式只在首次运行注入，页面一跳转就整体失效（改为每次 rerun 重新注入）。
- 首页反馈时间显示为 ISO 字符串（`2026-11-03T15:11`），统一为 `YYYY-MM-DD HH:MM`。
- 问题聚类页「查看簇详情」点击后详情始终渲染在整页最底部，视觉上像没反应；
  改为点哪张簇卡片就在哪张下面就地展开，再点一次收起。

## [0.4.1-dev] - 2026-11-09

### Changed
- 首屏改为独立的介绍层（`ui/landing_page.py`）：不再作为侧边栏模块之一，
  默认进入；侧边栏只保留 6 个功能模块，未选中时留空。
- 首屏内容精简为：一句话定位 + 4 个关键数字 + 3 条能力说明 + 6 个模块入口，
  移除系统定位长段、痛点三卡、五步流程条与问题簇表格。
- 模块页新增「← 返回首页」入口；首屏与模块通过 `wm_view` 会话状态切换。
- 深链接补充 `?view=home` 回首屏，`?page=` 仍直接进模块。

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
