# WorkMesh 证据模型与关联分析

版本：v0.2.0-dev
实现位置：`core/`、`services/evidence_service.py`、`ui/evidence_page.py`
数据结构定义见 `docs/DATA_MODEL.md` 第 13 节。

## 1. 解决什么问题

一条用户反馈通常只有「谁、什么时候、哪个产品、什么错误码、描述」。
真正要排查生产问题，需要把它和日志、其他反馈放在一起看：

- 同一 Trace / Request 的日志，说明请求链路里到底哪一步失败。
- 同一 UID 在附近时间的其他反馈，说明是个案还是成批出现。
- 同一错误码在同一时间窗口的分布，说明故障是否集中。

本模块把这件事固化成可复用的流程，而不是每次靠人手工拼证据。

## 2. 数据结构

`Evidence` 是唯一的中间结构，三类来源都转换成它：

| 来源 | 转换方式 |
| --- | --- |
| 反馈记录 | `Evidence.from_feedback_record()` |
| 连接器输出 | `Evidence.from_connector_record()` |
| 本地 JSON/JSONL/CSV | 同上，`connector_type=file` |

统一字段：`evidence_type`、`record_id`、`timestamp`、`uid`、`request_id`、
`trace_id`、`service`、`level`、`error_code`、`title`、`message`。

其余字段进入 `attributes`，原始记录进入 `raw_data`，不做丢弃。

反馈记录里的 `extra_data` 如果包含 `trace_id` / `traceId`，
会被提取为锚点的强关联标识，用于和日志直接对接。

## 3. 关联规则

打分是显式的加法，命中项和理由会一起返回：

| 条件 | 权重 | 时间窗口 |
| --- | --- | --- |
| trace_id 一致 | 45 | 不限制 |
| request_id 一致 | 40 | 不限制 |
| uid 一致 | 25 | 限制 |
| error_code 一致 | 20 | 限制 |
| service 一致 | 10 | 限制 |
| 时间差 <= 5 分钟 | 10 | — |

两条硬性约束：

1. 必须命中 trace_id / request_id / uid 之一。服务名 + 时间接近不能单独成立。
2. trace_id / request_id 命中时，即使超出时间窗口也保留关联，并在理由中标注。

强度按命中键判定，而不是按分数：

| 命中情况 | 强度 | 含义 |
| --- | --- | --- |
| trace_id 或 request_id | strong | 同一链路/请求，可作为已验证事实 |
| uid + error_code | strong | 同一用户同一错误，窗口内集中出现 |
| 仅 uid 或仅 error_code | medium | 参考证据，需要人工判断 |

不使用纯分数阈值的原因：反馈锚点一般没有 Trace ID，
按分数划线会让「强关联」永远无法出现。

## 4. 输出口径

`EvidenceService.build_context()` 返回三类内容，界面严格分开展示：

- **已验证事实**：字段相等这类可直接验证的结论，附证据 ID 列表。
- **待验证假设**：由分布或计数引出的方向性判断，明确标注「待验证」「证据不足」。
- **关联证据**：分数、强度、命中键、逐条理由和原始数据。

这是刻意的设计：分析结论不得把推断描述成已确认事实。

## 5. 使用方式

### 界面

侧边栏「证据关联」：

1. 「证据接入」页签：上传 JSON/JSONL/CSV，或接入内置样例日志；
   也可以配置 Elasticsearch 地址与索引，测试连接后按 UID / 错误码 / 关键词查询并写入。
2. 「证据关联」页签：选择一条反馈作为锚点，设置时间窗口和最低分，生成上下文。
3. 下方「已保存的关联」可回看历史关联记录。

### 代码

```python
from services.evidence_service import EvidenceService

service = EvidenceService()

# 1. 接入外部证据（已标准化）
service.ingest_connector_records(
    workspace_id,
    normalized_records,
    source_name="Elasticsearch：app-logs",
    connector_type="elasticsearch",
)

# 2. 生成问题上下文
context = service.build_context(
    workspace_id,
    feedback_id,
    window_minutes=30,
    min_score=30,
)
```

## 6. 扩展新数据源

接入 Prometheus / Loki / Jaeger 时只需要：

1. 新建 `BaseConnector` 子类，实现 `connect` / `test_connection` / `query` / `normalize`。
2. `normalize()` 输出统一 Evidence 字段，无法归类的放 `attributes` 或 `raw_data`。
3. 在 `connectors/__init__.py` 注册。
4. 调用 `ingest_connector_records()` 写入。

关联规则不需要改动：新证据只要带 uid / trace_id / error_code / service / timestamp，
就会自动参与计算。

## 7. 已知限制

- 只做标识与时间的结构化关联，不做语义相似度；描述不同但同一根因的问题无法自动识别。
- 指标、链路追踪连接器尚未实现，当前证据以日志与反馈为主。
- 关联结果不自动判定根因，只提供事实与假设，最终判断仍需人工确认。
- 阈值与权重集中在 `core/correlation.py`，调整后建议同步更新本文档与测试。
