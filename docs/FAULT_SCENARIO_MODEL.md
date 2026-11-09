# WorkMesh 故障场景模型设计

> 目标：把“不同类型问题该查什么、去哪查、满足什么条件才能下什么结论”从代码里的 if/else 和提示词，变成可版本化、可校验、可回放的配置。  
> 本文只做设计，不实现加载器、不新增数据库表、不改现有诊断逻辑。

## 1. 设计原则

1. **场景是配置，不是代码**：新增一类问题不需要改 `DiagnosisService`。
2. **规则是声明式的，不执行任意代码**：禁止 `eval`、任意 SQL、任意 Python；只允许结构化条件树和受控数据源适配器。
3. **事实、推断、假设三分**：数据直接观测到的才是事实；规则推出的是假设；没有证据时明确写“未验证”，不拿默认话术填空。
4. **缺数据要显式**：数据源分 required / optional；缺 required 时场景进入 `blocked`，不是“诊断成功”。
5. **复用现有证据链**：场景只消费 `Evidence`、关联结果、反馈和报告历史，不另造一套证据模型。
6. **结论有上限**：没有 Trace ID / 请求级日志时，不允许把结论写成“根因”；置信度按策略封顶。
7. **配置可审计**：场景有 ID、版本、负责人、生效时间、变更记录；报告里保存场景快照或版本号。

## 2. 对象模型

```
FaultScenario
├── identity        场景 ID、名称、版本、优先级、适用范围
├── match           什么反馈命中这个场景
├── sources         数据源绑定（本地证据 / ES / Prometheus / 报告历史 / 人工确认）
├── signals         从数据源算出的具名信号（计数、比例、最大值、是否存在）
├── steps           排查步骤（按顺序，每步有通过/失败/未知判定）
├── rules           判断规则（条件树 → 事实补充 / 假设 / 下一步）
└── output_policy   结论边界、置信度上限、必须引用的证据、禁止项
```

运行时再产生一个 `ScenarioRun`：

```
ScenarioRun
├── scenario_id / version / matched_at
├── match_score / match_reasons
├── status：verified / partial / blocked / unmatched
├── facts[] / hypotheses[] / steps[]
├── missing_sources[] / next_actions[]
├── evidence_refs[]（E: 证据主键 / S: 信号引用）
└── confidence / limits[]
```

## 3. 配置存放与格式

- 目录：`configs/fault_scenarios/`
- 一个场景一个 JSON 文件：`<scenario_id>.json`
- 注册中心扫描目录，不依赖数据库；启动时加载并校验，校验失败只禁用该场景并记录错误，不影响整个应用。
- 选择 JSON 而不是 YAML：项目当前没有 `PyYAML` 依赖，JSON 零新增依赖、便于程序校验和 diff。
- 连接地址、API Key、Token 不放场景文件；场景只引用“连接配置 ID”，凭据仍由 `llm_config` / 未来的 `connector_profiles` 管理。

## 4. 顶层 Schema

```json
{
  "schema_version": 1,
  "id": "login.code_delivery",
  "name": "登录验证码收不到",
  "version": "2026.11",
  "owner": "feedback-platform",
  "priority": 80,
  "enabled": true,
  "effective_from": "2026-11-01",
  "tags": ["账号", "验证码", "登录"],

  "match": {},
  "sources": [],
  "signals": [],
  "steps": [],
  "rules": [],
  "output_policy": {}
}
```

### 4.1 match：场景命中

```json
{
  "products": ["账号服务"],
  "error_codes": ["E5001", "E5002"],
  "keywords_any": ["验证码", "收不到", "登录不上"],
  "symptoms_any": ["收不到验证码", "验证码无效"],
  "features_any": ["登录", "注册"],
  "platforms": ["iOS", "Android"],
  "exclude_keywords": ["建议增加", "希望支持"]
}
```

匹配打分（实现时固定规则，不做模型）：

- 产品命中：+40
- 错误码命中：+30
- 关键词 / 现象 / 功能命中：每类 +10，封顶 +30
- 平台命中：+5
- 总分低于 40 不命中；同分取 `priority` 高者；再同分取 `id` 字典序，保证可复现。
- 所有场景都不命中时，走 `generic.triage` 兜底场景；兜底场景只做“收集证据、扩大窗口”，不输出领域结论。

### 4.2 sources：数据源绑定

通用字段：

```json
{
  "id": "es_sms",
  "kind": "connector",
  "connector_type": "elasticsearch",
  "required": false,
  "window_minutes": 30,
  "limit": 500,
  "description": "短信网关日志",
  "query": {}
}
```

`kind` 支持：

| kind | 用途 | query 结构 |
|---|---|---|
| `feedback` | 本地反馈窗口 | `{products, error_codes, uid, window_minutes, limit}` |
| `evidence` | 已入库证据 | `{evidence_types, service, uid, error_code, trace_id, window_minutes, limit}` |
| `connector` | 实时连接器 | 由连接器适配器定义，见下 |
| `report_history` | 历史报告 | `{products, error_codes, days, limit}` |
| `manual` | 人工确认项 | `{prompt, options}`，不自动查询 |
| `knowledge` | 知识库（预留） | `{query, top_k}`，一期不实现 |

Elasticsearch 的 `query` 只允许以下字段，禁止把用户输入拼成 DSL：

```json
{
  "index": "app-sms-*",
  "time_field": "@timestamp",
  "keyword": "{{anchor.error_code}}",
  "filters": {
    "service": "sms-gateway",
    "uid": "{{anchor.uid}}",
    "error_code": "{{anchor.error_code}}"
  },
  "size": 200
}
```

Prometheus 的 `query`：

```json
{
  "promql": "sum by (provider) (rate(sms_send_total{status=\"failed\"}[5m])) / sum by (provider) (rate(sms_send_total[5m]))",
  "window_minutes": 30,
  "step": "1m"
}
```

占位符只允许 `{{anchor.uid}}`、`{{anchor.error_code}}`、`{{anchor.service}}`、`{{anchor.product}}`、`{{window.start}}`、`{{window.end}}`。适配器必须按字段类型绑定/转义，不能把原始字符串直接塞进 PromQL 标签或 SQL。

`required` 的语义：

- `required=true` 且连接失败/无数据 → 场景 `status=blocked`，结论只能是“无法验证”，并列出缺失源。
- `required=false` 且缺失 → 场景 `status=partial`，相关规则不触发，置信度降级。

### 4.3 signals：具名信号

信号是规则和步骤唯一可引用的“事实变量”，不允许规则里直接写查询。

```json
{
  "id": "same_uid_log_count",
  "source": "es_sms",
  "kind": "count",
  "description": "同 UID 同错误码的短信网关日志条数"
}
```

`kind` 支持：

| kind | 含义 | 结果类型 |
|---|---|---|
| `count` | 满足条件的记录数 | int |
| `exists` | 是否至少一条 | bool |
| `distinct_count` | 去重计数，需指定 `field` | int |
| `max` / `min` / `avg` / `sum` | 对数值字段聚合 | float |
| `latest` | 最新一条的值/时间，需指定 `field` | any |
| `ratio` | 两个信号相除，需 `numerator` / `denominator` | float |
| `threshold_flag` | 对某信号做阈值判断，需 `signal` / `op` / `value` | bool |

示例：

```json
[
  {"id": "same_uid_log_count", "source": "es_sms", "kind": "count"},
  {"id": "provider_error_rate", "source": "prom_sms", "kind": "max", "field": "value"},
  {"id": "provider_failure_flag", "kind": "threshold_flag",
   "signal": "provider_error_rate", "op": "gte", "value": 0.05}
]
```

信号缺失时值为 `null`，不是 0。规则必须显式处理 `null`，避免“没查到”被当成“正常”。

### 4.4 steps：排查步骤

步骤是可展示的 runbook，不是自由文本。每一步必须绑定数据源和判定条件。

```json
{
  "id": "check_provider",
  "title": "核对短信服务商失败率",
  "when": {"signal": "provider_error_rate", "op": "exists"},
  "uses": ["prom_sms"],
  "observe": "provider_error_rate",
  "criteria": {
    "pass": {"signal": "provider_error_rate", "op": "lt", "value": 0.05},
    "fail": {"signal": "provider_error_rate", "op": "gte", "value": 0.05}
  },
  "on_missing": "mark_unverified",
  "guidance": "失败率 ≥5% 时优先联系短信服务商，并核对通道余额与模板审核状态。"
}
```

`on_missing` 取值：

- `skip`：条件不满足，直接跳过，步骤状态 `skipped`
- `mark_unverified`：保留步骤，状态 `unknown`，进入 `missing_sources`
- `block`：场景状态 `blocked`，停止后续规则

步骤状态只有 `pass` / `fail` / `unknown` / `skipped`，不写“大概正常”。

### 4.5 rules：判断规则

规则只允许条件树 + 结论，不写自然语言 if/else。条件节点：

```json
{"all": [ ... ]}
{"any": [ ... ]}
{"not": { ... }}
{"signal": "provider_error_rate", "op": "gte", "value": 0.05}
{"signal": "same_uid_log_count", "op": "eq", "value": 0}
{"signal": "same_uid_log_count", "op": "exists"}
{"signal": "provider_error_rate", "op": "in", "value": [0.05, 0.2]}
```

允许的 `op`：`eq`、`ne`、`gt`、`gte`、`lt`、`lte`、`in`、`exists`、`is_null`。

规则体：

```json
{
  "id": "provider_side",
  "priority": 10,
  "when": {
    "all": [
      {"signal": "provider_error_rate", "op": "gte", "value": 0.05},
      {"signal": "same_uid_log_count", "op": "eq", "value": 0}
    ]
  },
  "requires": ["prom_sms"],
  "then": {
    "type": "hypothesis",
    "statement": "短信服务商侧失败率升高，且没有同 UID 网关日志，优先怀疑通道侧。",
    "confidence": "medium",
    "evidence_refs": ["S:prom_sms.provider_error_rate"]
  },
  "actions": [
    "核对短信服务商通道余额、模板审核状态与失败码分布",
    "对比同时段其他产品的短信发送成功率"
  ]
}
```

约束：

- `requires` 中的 source 必须全部可用，否则规则不触发。
- `then.type` 只允许 `hypothesis` 或 `action`；`fact` 只能由数据源观测直接产生，不能由规则“推断成事实”。
- `evidence_refs` 只能引用已注册的 `E:` 证据或 `S:` 信号；校验器在加载时检查。
- 规则按 `priority` 从高到低执行；同一结论只保留最高优先级，避免多条规则重复说同一件事。
- 规则不能修改数据、不能调用外部动作；`actions` 只是建议文本 + 所需证据，不自动执行。

### 4.6 output_policy：结论边界

```json
{
  "max_confidence_without_trace": "medium",
  "forbid_root_cause": true,
  "min_evidence_refs_for_hypothesis": 1,
  "require_any_of_sources": ["es_sms", "prom_sms"],
  "disclaimer": "当前没有请求级 Trace，结论只能定位到服务/通道方向，不能定位到具体代码行。"
}
```

硬性规则：

- 没有 `trace_id` / `request_id` 证据时，`confidence` 不得为 `high`，且结论文本不得出现“根因是”“确定是”。
- `forbid_root_cause=true` 时，规则里的 `statement` 若包含“根因/确定/必然”直接校验失败。
- 至少一个 `E:` 或 `S:` 引用，否则假设不能输出。
- `blocked` 场景不输出假设，只输出缺失清单和补证动作。

## 5. 完整示例：登录验证码收不到

```json
{
  "schema_version": 1,
  "id": "login.code_delivery",
  "name": "登录验证码收不到",
  "version": "2026.11",
  "owner": "feedback-platform",
  "priority": 80,
  "enabled": true,
  "effective_from": "2026-11-01",
  "tags": ["账号", "验证码", "登录"],

  "match": {
    "products": ["账号服务"],
    "error_codes": ["E5001", "E5002"],
    "keywords_any": ["验证码", "收不到", "登录不上", "短信"],
    "symptoms_any": ["收不到验证码", "验证码无效"],
    "features_any": ["登录", "注册"]
  },

  "sources": [
    {
      "id": "feedback_recent",
      "kind": "feedback",
      "required": true,
      "window_minutes": 30,
      "query": {
        "products": ["账号服务"],
        "error_codes": ["E5001", "E5002"],
        "limit": 200
      }
    },
    {
      "id": "es_sms",
      "kind": "connector",
      "connector_type": "elasticsearch",
      "required": false,
      "window_minutes": 30,
      "limit": 200,
      "query": {
        "index": "app-sms-*",
        "time_field": "@timestamp",
        "filters": {
          "service": "sms-gateway",
          "uid": "{{anchor.uid}}",
          "error_code": "{{anchor.error_code}}"
        },
        "size": 200
      }
    },
    {
      "id": "prom_sms",
      "kind": "connector",
      "connector_type": "prometheus",
      "required": false,
      "window_minutes": 30,
      "query": {
        "promql": "sum by (provider) (rate(sms_send_total{status=\"failed\"}[5m])) / sum by (provider) (rate(sms_send_total[5m]))",
        "step": "1m"
      }
    },
    {
      "id": "trace_lookup",
      "kind": "manual",
      "required": false,
      "query": {
        "prompt": "补一个请求级 Trace ID（短信发送请求）",
        "options": ["已有 Trace", "暂无 Trace"]
      }
    }
  ],

  "signals": [
    {"id": "recent_feedback_count", "source": "feedback_recent", "kind": "count"},
    {"id": "same_uid_log_count", "source": "es_sms", "kind": "count"},
    {"id": "provider_error_rate", "source": "prom_sms", "kind": "max", "field": "value"},
    {"id": "provider_failure_flag", "kind": "threshold_flag", "signal": "provider_error_rate", "op": "gte", "value": 0.05},
    {"id": "trace_available", "source": "trace_lookup", "kind": "value"}
  ],

  "steps": [
    {
      "id": "confirm_scope",
      "title": "确认反馈面",
      "uses": ["feedback_recent"],
      "observe": "recent_feedback_count",
      "criteria": {
        "pass": {"signal": "recent_feedback_count", "op": "gte", "value": 2},
        "fail": {"signal": "recent_feedback_count", "op": "lt", "value": 2}
      },
      "on_missing": "mark_unverified",
      "guidance": "只有 1 条时先按个体问题处理；≥2 条再看是否面上扩散。"
    },
    {
      "id": "check_same_uid",
      "title": "查同 UID 短信网关日志",
      "uses": ["es_sms"],
      "observe": "same_uid_log_count",
      "criteria": {
        "pass": {"signal": "same_uid_log_count", "op": "gte", "value": 1},
        "fail": {"signal": "same_uid_log_count", "op": "eq", "value": 0}
      },
      "on_missing": "mark_unverified",
      "guidance": "有同 UID 日志时，继续看错误码是通道失败、模板失败还是频控。"
    },
    {
      "id": "check_provider",
      "title": "核对短信服务商失败率",
      "uses": ["prom_sms"],
      "observe": "provider_error_rate",
      "criteria": {
        "pass": {"signal": "provider_error_rate", "op": "lt", "value": 0.05},
        "fail": {"signal": "provider_error_rate", "op": "gte", "value": 0.05}
      },
      "on_missing": "mark_unverified",
      "guidance": "失败率 ≥5% 时优先怀疑通道侧；低于 5% 时回到账号/设备侧。"
    },
    {
      "id": "collect_trace",
      "title": "补齐请求级 Trace",
      "uses": ["trace_lookup"],
      "observe": "trace_available",
      "criteria": {
        "pass": {"signal": "trace_available", "op": "eq", "value": true},
        "fail": {"signal": "trace_available", "op": "eq", "value": false}
      },
      "on_missing": "mark_unverified",
      "guidance": "没有 Trace 时，结论只能停在服务/通道方向。"
    }
  ],

  "rules": [
    {
      "id": "provider_side",
      "priority": 30,
      "when": {
        "all": [
          {"signal": "provider_error_rate", "op": "gte", "value": 0.05},
          {"signal": "same_uid_log_count", "op": "eq", "value": 0}
        ]
      },
      "requires": ["prom_sms"],
      "then": {
        "type": "hypothesis",
        "statement": "短信服务商侧失败率升高，且没有同 UID 网关日志，优先怀疑通道侧。",
        "confidence": "medium",
        "evidence_refs": ["S:prom_sms.provider_error_rate"]
      },
      "actions": [
        "核对短信服务商通道余额、模板审核状态与失败码分布",
        "对比同时段其他产品的短信发送成功率"
      ]
    },
    {
      "id": "account_or_device_side",
      "priority": 25,
      "when": {
        "all": [
          {"signal": "same_uid_log_count", "op": "gte", "value": 1},
          {"signal": "provider_error_rate", "op": "lt", "value": 0.05}
        ]
      },
      "requires": ["es_sms", "prom_sms"],
      "then": {
        "type": "hypothesis",
        "statement": "网关有该用户请求且通道整体正常，问题更可能在账号状态、设备或客户端链路。",
        "confidence": "medium",
        "evidence_refs": ["S:es_sms.same_uid_log_count", "S:prom_sms.provider_error_rate"]
      },
      "actions": [
        "核对该 UID 的账号风控/频控状态",
        "核对客户端版本、网络与短信权限",
        "补请求级 Trace 后定位具体失败节点"
      ]
    },
    {
      "id": "multi_factor",
      "priority": 20,
      "when": {
        "all": [
          {"signal": "same_uid_log_count", "op": "gte", "value": 1},
          {"signal": "provider_error_rate", "op": "gte", "value": 0.05}
        ]
      },
      "requires": ["es_sms", "prom_sms"],
      "then": {
        "type": "hypothesis",
        "statement": "用户请求与通道失败同时存在，可能是通道抖动叠加个体频控，暂不能归为单点原因。",
        "confidence": "low",
        "evidence_refs": ["S:es_sms.same_uid_log_count", "S:prom_sms.provider_error_rate"]
      },
      "actions": [
        "按失败码拆分通道失败与频控失败",
        "对比同产品不同时间段的失败率"
      ]
    },
    {
      "id": "no_data",
      "priority": 10,
      "when": {
        "all": [
          {"signal": "same_uid_log_count", "op": "is_null"},
          {"signal": "provider_error_rate", "op": "is_null"}
        ]
      },
      "then": {
        "type": "action",
        "statement": "当前没有可用的网关日志和通道指标，不能判断方向。",
        "confidence": "low",
        "evidence_refs": ["S:feedback_recent.recent_feedback_count"]
      },
      "actions": [
        "先补 ES 日志接入或粘贴同 UID 日志",
        "补 Prometheus 短信失败率指标",
        "补请求级 Trace ID"
      ]
    }
  ],

  "output_policy": {
    "max_confidence_without_trace": "medium",
    "forbid_root_cause": true,
    "min_evidence_refs_for_hypothesis": 1,
    "require_any_of_sources": ["es_sms", "prom_sms"],
    "disclaimer": "没有请求级 Trace 时，结论只能定位到服务/通道方向，不能定位到具体代码行。"
  }
}
```

这个示例刻意展示了三个关键点：

- 反馈窗口是 required，ES / Prometheus 是 optional；没有外部实例时场景仍能跑，但状态是 `partial`，结论封顶。
- 规则只产出假设，不产出“根因”。
- 每条假设至少引用一个 `S:` 信号；没有信号就不输出。

## 6. 典型场景覆盖建议

| 场景 ID | 命中特征 | 主要数据源 | 关键信号 | 第一条规则 |
|---|---|---|---|---|
| `login.code_delivery` | 账号服务 + E5001/E5002 + 验证码 | 反馈窗口、ES 短信网关、Prometheus 发送失败率 | 同 UID 日志数、服务商失败率 | 失败率高且无用户日志 → 通道侧假设 |
| `payment.result_mismatch` | 支付中心 + 支付失败/重复扣款 | 反馈、ES 支付网关、Prometheus 支付成功率、订单状态 | 同订单号日志、支付成功率、订单状态不一致数 | 订单已扣款但回调失败 → 回调/对账方向 |
| `openapi.auth_signature` | 开放平台 + 401/403/签名错误 | 反馈、ES API 网关、变更记录 | 同 app_id 401/403 数、签名错误占比、最近变更 | 集中 401 且无变更 → 密钥/签名实现方向 |
| `push.delivery` | 消息推送 + 收不到/延迟 | 反馈、ES 推送网关、Prometheus 推送成功率 | 同 UID 推送记录、设备 token 失效率、通道成功率 | token 失效率高 → 设备注册方向 |
| `search.empty_result` | 搜索推荐 + 搜不到/空结果 | 反馈、ES 搜索日志、Prometheus 查询量/耗时 | 空结果率、同关键词命中数、超时率 | 空结果率高且耗时正常 → 索引/召回方向 |
| `video.export_failure` | 视频编辑 + 导出失败/卡住 | 反馈、ES 导出任务日志、任务队列指标 | 同版本失败率、任务超时数、队列积压 | 同版本集中失败 → 版本/转码链路方向 |
| `generic.triage` | 未命中任何场景 | 反馈窗口、已入库证据 | 反馈量、证据条数、时间窗 | 只输出补证动作，不做领域结论 |

一期不建议一次铺满所有场景。先做 `login.code_delivery` + `payment.result_mismatch` + `generic.triage` 三个，验证模型和 UI，再扩。

## 7. 运行时架构（建议）

```
FaultScenarioRegistry
  ├─ load(configs/fault_scenarios/*.json)
  ├─ validate(schema + 语义)
  └─ list_enabled()

ScenarioMatcher
  └─ match(feedback) -> (scenario, score, reasons)

ScenarioRunner
  ├─ SourceAdapter（feedback / evidence / connector / report_history / manual）
  ├─ SignalEvaluator
  ├─ StepEvaluator
  ├─ RuleEngine
  └─ OutputPolicy
        -> ScenarioRun
```

与现有代码的接入点：

- `DiagnosisService.build()`：先 `ScenarioMatcher.match(anchor)`；命中则用 `ScenarioRun` 替换 `_next_steps`，并在 `conclusions` 中加入场景假设；未命中走 `generic.triage`。
- `ClusterService.build()`：二期以簇的代表错误码/产品匹配场景，生成“簇级场景分布”，不在一期改。
- `PeriodicReportService`：二期统计各场景命中量、blocked 量、缺失源 Top N。
- UI：诊断页增加“命中场景 / 排查清单 / 缺失数据源 / 结论边界”卡片；不新增独立页面，先挂在单条诊断里。

## 8. 配置校验规则

加载时逐项校验，失败场景禁用并记录：

1. `id` 唯一，格式 `^[a-z][a-z0-9_.-]{2,64}$`。
2. `schema_version` 已知；`version`、`owner`、`effective_from` 必填。
3. `match` 至少有一个产品、错误码或关键词条件；只有明确的兜底场景允许 `always: true`，且必须保持最低优先级。
4. `sources[].id` 唯一；`kind` 在允许列表；`required` 为布尔。
5. `signals[].id` 唯一；引用的 source 存在；`ratio` / `threshold_flag` 引用的信号存在。
6. `steps[].uses` / `observe` 引用存在；`criteria` 的 pass/fail 至少一个；`on_missing` 合法。
7. `rules[].when` 的条件树只引用已注册信号；`requires` 引用已注册 source；`then.evidence_refs` 引用已注册的 E/S。
8. `output_policy` 必填；`forbid_root_cause` 为真时检查所有 statement 的禁用词。
9. 禁止出现 `api_key`、`token`、`password`、`secret`、`http://`、`https://` 等敏感/连接字段。
10. 步骤依赖图不能有环（一期步骤是线性的，二期才允许 `next`）。

建议用标准库实现一个 `validate_scenario(dict) -> list[str]`，不引入 `jsonschema` 依赖；等场景数量上来再换正式 schema 校验器。

## 9. 结论输出契约

```json
{
  "scenario_id": "login.code_delivery",
  "scenario_version": "2026.11",
  "status": "partial",
  "match": {"score": 80, "reasons": ["产品命中：账号服务", "错误码命中：E5001"]},
  "facts": [
    {"statement": "窗口内共有 3 条同产品同错误码反馈", "evidence_refs": ["S:feedback_recent.recent_feedback_count"]}
  ],
  "hypotheses": [
    {"statement": "短信服务商侧失败率升高……", "confidence": "medium", "evidence_refs": ["S:prom_sms.provider_error_rate"]}
  ],
  "steps": [
    {"id": "confirm_scope", "status": "pass", "observation": "3"},
    {"id": "check_same_uid", "status": "unknown", "reason": "ES 未接入或无数据"},
    {"id": "check_provider", "status": "fail", "observation": "0.08"}
  ],
  "missing_sources": ["es_sms", "trace_lookup"],
  "next_actions": ["补同 UID 网关日志", "补请求级 Trace"],
  "evidence_refs": ["S:feedback_recent.recent_feedback_count", "S:prom_sms.provider_error_rate"],
  "confidence": "medium",
  "limits": ["无 ES 日志，未验证用户侧请求", "无 Trace，不能定位到具体代码行"]
}
```

渲染规则：

- `facts` 用“已验证”样式；`hypotheses` 用“待验证/推断”样式，不能混在一起。
- `unknown` 步骤必须显示原因，不能显示成绿色通过。
- `blocked` 时隐藏假设区，只显示缺失源和补证动作。
- 报告归档保存 `scenario_id` / `version` / `status` / `confidence`，保证以后能解释“当时为什么这么判”。

## 10. 分阶段落地建议

**阶段 0（已完成）**：文档 + 示例 JSON，不改代码。

**阶段 1（已完成，2026-11-09）**：
- `FaultScenarioRegistry` + `validate_scenario()` + `match_scenario()` + `ScenarioRunner`，落在 `services/fault_scenario_service.py`。
- `feedback` / `evidence` / `manual` 三类本地 source 已接入；`connector` 通过可注入的 `connector_provider` 预留，未接真实 ES / Prometheus。
- `login.code_delivery`、`payment.result_mismatch`、`generic.triage` 三个场景；单条诊断页增加场景卡片、排查步骤表、缺失源、结论边界；Markdown 报告同步增加「故障场景」段。
- `DiagnosisService.build()` 支持 `manual_answers`，诊断页用「已有请求级 Trace」勾选项驱动 `trace_lookup`；场景动作会合并进下一步建议。
- 单测：注册表加载、匹配、`blocked`、`partial`、`verified`、条件树、仓储过滤与诊断接入；全量测试 137 passed。

**阶段 1.5（下一轮本地闭环）**：
- 把 `payment.result_mismatch` 的 E6003 退款咨询拆成独立场景，避免错误码命中跨界。
- 补 `report_history` / `knowledge` source；报告中心保存场景快照。
- 在簇报告、周期报告中按场景聚合命中率、blocked 率。

**阶段 2（接外部）**：
- `connector` source 适配器，复用现有 ES / Prometheus 连接器。
- 真实实例联调后再打开 `required=true` 的场景。
- 报告保存场景快照；簇级场景分布。

**阶段 3（运营化）**：
- 人工确认步骤结果、场景效果统计（命中率/blocked 率/人工采纳率）。
- 场景版本对比、灰度启用、按 workspace 覆盖。
- 周期报告按场景聚合，定时任务。

## 11. 风险与边界

- **场景配置会膨胀**：场景按产品线拆，不按单个错误码拆；一个场景覆盖一类现象，错误码放 `match.error_codes`。
- **PromQL / ES DSL 仍是半任意查询**：配置视为受信任代码，必须走代码评审；用户输入只通过受控占位符绑定。
- **场景不等于根因**：模型只能把“该查什么、查到什么、能推到哪一步”固化，不能替代研发定位。
- **没有真实数据源时不要假装验证**：`required=false` 的场景输出 `partial`，UI 必须显式标注缺失源。
- **旧报告兼容**：新增场景字段后，旧报告没有 `scenario_id` 时按“历史规则诊断”展示，不回填、不猜测。
