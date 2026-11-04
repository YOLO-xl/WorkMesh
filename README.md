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
- 自动化测试。

## 规划中的能力

- 完善 Elasticsearch 连接、认证和查询能力。
- 接入 Prometheus、OpenSearch、Loki。
- 接入 Jaeger / Tempo 链路追踪。
- 建立统一 Evidence 证据模型。
- 实现多源数据关联和 AI 辅助诊断。

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

## 设计原则

1. 统一接口：不同外部系统通过 Connector 接入。
2. 证据优先：分析结论应关联原始数据和证据。
3. 保留原始数据：标准化处理不覆盖原始记录。
4. 只读优先：外部系统接入以查询和分析为主。
5. 渐进扩展：先完善数据与连接器基础，再扩展 AI 诊断能力。

## 项目状态

当前处于基础工作台和数据连接能力建设阶段，部分规划功能尚未实现。
