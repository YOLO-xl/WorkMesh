# Changelog

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
