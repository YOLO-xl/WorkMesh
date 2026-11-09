# WorkMesh

WorkMesh 是一个面向企业技术支持与生产问题排查场景的 AI 工作台。

## 核心目标

- 统一接入用户反馈、工单、日志和监控数据。
- 对反馈和工单进行统计分析。
- 关联多源证据，辅助定位生产问题。
- 为 AI 辅助诊断和知识检索提供数据基础。

## 当前已实现

- Streamlit 基础工作台与页面导航。
- CSV / Excel 数据导入、字段映射和质量检查。
- SQLite 数据持久化及 Repository 数据访问层。
- 反馈数据基础统计分析。
- Connector 统一连接器接口与注册中心。
- Elasticsearch 日志连接器初版。
- 统一 Evidence 证据模型：日志、反馈等接入后转换为同一结构并持久化。
- 证据关联分析：按 Trace ID / Request ID / UID / 错误码 / 服务与时间窗口计算关联分数。
- 「证据关联」页面：证据接入、生成问题上下文、回看已保存关联。
- 数据库版本化 Migration（v1 → v2）。
- 自动化测试。

## 规划中的能力

- 完善 Elasticsearch 连接、认证和查询能力。
- 接入 Prometheus、OpenSearch、Loki。
- 接入 Jaeger / Tempo 链路追踪。
- 跨数据源证据检索与问题上下文整合。
- 基于证据的 AI 辅助诊断（结论必须引用证据）。

## 技术栈

- Python 3.12
- Streamlit
- Pandas
- SQLite
- Elasticsearch Python Client
- pytest
- Git

## 本地运行

安装依赖：

```bash
pip install -r requirements.txt
```

启动工作台：

```bash
streamlit run app.py
```

运行测试：

```bash
pytest -q
```

## 快速体验证据关联

1. 启动工作台，进入「数据接入」，导入 `data/sample/feedback_sample.csv`。
2. 进入「证据关联」→「证据接入」，点击「接入内置样例日志」
   （读取 `data/sample/log_sample.jsonl`，共 10 条日志）。
3. 切到「证据关联」页签，选择反馈 `F001`（UID 10086231 / E1001），
   时间窗口 30 分钟，生成上下文。
4. 预期结果：候选证据 5 条、强关联日志 2 条、同 UID 的其他反馈 1 条，
   事实区显示 UID / 错误码 / 服务一致项。

设计说明见 `docs/EVIDENCE_MODEL.md`。

## 设计原则

1. 统一接口：不同外部系统通过 Connector 接入。
2. 证据优先：分析结论应关联原始数据和证据，事实与假设分开标注。
3. 保留原始数据：标准化处理不覆盖原始记录。
4. 只读优先：外部系统接入以查询和分析为主。
5. 渐进扩展：先完善数据与连接器基础，再扩展 AI 诊断能力。

## 项目状态

当前处于基础工作台和数据连接能力建设阶段，部分规划功能尚未实现。
