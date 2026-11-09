"""故障场景运营页。

把 JSON 场景配置变成可观察的运营对象：场景库、覆盖评估、命中/阻塞统计
和单条样例试跑。
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from database.feedback_repository import FeedbackRepository
from database.migration import initialize_database
from services.fault_scenario_service import (
    ScenarioRunner,
    match_scenario,
)
from services.connector_source_provider import ConnectorSourceProvider
from services.scenario_ops_service import ScenarioOpsService
from ui.theme import hero, section


STATUS_LABELS = {
    "verified": "已验证",
    "partial": "部分完成",
    "blocked": "阻塞",
    "none": "未命中",
}

CONFIDENCE_LABELS = {
    "high": "高",
    "medium": "中",
    "low": "低",
}


def _feedback_label(record: dict) -> str:
    stamp = str(record.get("feedback_time") or "—")[:16]
    code = record.get("error_code") or "—"
    description = str(record.get("description") or "")[:30]
    return f"{stamp} | {code} | {description}"


def _format_scenarios(scenarios: list[dict], stats: dict[str, dict]) -> pd.DataFrame:
    rows = []
    for item in scenarios:
        stat = stats.get(item["id"], {})
        matched = stat.get("matched", 0)
        verified = stat.get("verified", 0)
        rows.append({
            "场景": item["name"],
            "ID": item["id"],
            "优先级": item["priority"],
            "数据源": item["source_count"],
            "外部源": len(item["connector_sources"]),
            "命中": matched,
            "已验证": verified,
            "部分完成": stat.get("partial", 0),
            "阻塞": stat.get("blocked", 0),
            "验证率": f"{verified / matched * 100:.0f}%" if matched else "—",
        })
    return pd.DataFrame(rows)


def _format_rows(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame([
        {
            "时间": str(row.get("feedback_time") or "").replace("T", " ").replace("Z", ""),
            "反馈": row.get("external_id") or row.get("feedback_id") or "—",
            "产品": row.get("product") or "—",
            "错误码": row.get("error_code") or "—",
            "场景": row.get("scenario_name") or row.get("scenario_id"),
            "得分": row.get("score", 0),
            "状态": STATUS_LABELS.get(row.get("status"), row.get("status")),
            "置信度": CONFIDENCE_LABELS.get(
                row.get("confidence"), row.get("confidence") or "—"
            ),
            "缺失源": "、".join(
                item.get("id") or "—"
                for item in row.get("missing_sources") or []
            ) or "—",
            "描述": row.get("description") or "—",
        }
        for row in rows
    ])


def _render_library(service: ScenarioOpsService) -> None:
    overview = service.overview()
    section("场景库", "配置即场景；这里检查每个场景的数据源、步骤和结论边界")

    if overview["errors"]:
        for error in overview["errors"]:
            st.error(f"{error.get('file')}：{'；'.join(error.get('errors') or [])}")

    scenarios = overview["scenarios"]
    for item in scenarios:
        scenario = service.registry.get(item["id"]) or {}
        with st.expander(
            f"{item['name']} ｜ {item['id']} ｜ 优先级 {item['priority']}"
        ):
            st.caption(
                f"版本 {item.get('version') or '—'} ｜ 负责人 {item.get('owner') or '—'} ｜ "
                f"标签 {'、'.join(item.get('tags') or []) or '—'}"
            )
            st.markdown("**匹配条件**")
            st.json(scenario.get("match") or {})
            st.markdown("**数据源**")
            st.dataframe(
                pd.DataFrame([
                    {
                        "ID": source.get("id"),
                        "类型": source.get("kind"),
                        "连接器": source.get("connector_type") or "—",
                        "必需": "是" if source.get("required") else "否",
                        "说明": source.get("description") or "—",
                    }
                    for source in scenario.get("sources") or []
                ]),
                use_container_width=True,
                hide_index=True,
            )
            st.caption(
                f"排查步骤 {len(scenario.get('steps') or [])} 个 ｜ "
                f"规则 {len(scenario.get('rules') or [])} 条"
            )
            if item.get("disclaimer"):
                st.info(item["disclaimer"])


def _render_trial(service: ScenarioOpsService, workspace_id: str) -> None:
    section("单条试跑", "选择一条反馈，看它命中哪个场景、哪些步骤被数据卡住")

    records = service.feedback_repository.list_feedback(
        workspace_id, page=1, page_size=200
    )["records"]
    if not records:
        st.info("还没有反馈数据。")
        return

    feedback_id = st.selectbox(
        "反馈记录",
        options=[record["id"] for record in records],
        format_func=lambda value: _feedback_label(
            next(record for record in records if record["id"] == value)
        ),
        key="scenario_trial_feedback",
    )

    if st.button("试跑匹配场景", type="primary", key="scenario_trial_run"):
        record = next(record for record in records if record["id"] == feedback_id)
        scenario, score, reasons = match_scenario(
            record, service.registry.enabled()
        )
        if not scenario:
            st.warning("没有匹配到场景。")
            return

        run = ScenarioRunner(
            feedback_repository=service.feedback_repository,
            db_path=service.db_path,
            connector_provider=service.connector_provider,
        ).run(
            workspace_id=workspace_id,
            feedback=record,
            scenario=scenario,
        )
        st.session_state["scenario_trial_result"] = {
            "record": record,
            "scenario": scenario,
            "score": score,
            "reasons": reasons,
            "run": run,
        }

    result = st.session_state.get("scenario_trial_result")
    if not result:
        return

    record = result["record"]
    scenario = result["scenario"]
    run = result["run"]

    st.divider()
    st.markdown(
        f"**{record.get('external_id') or '—'}** ｜ "
        f"{record.get('product') or '—'} / {record.get('error_code') or '—'}"
    )
    st.caption(
        f"命中 {scenario.get('name')}（{scenario.get('id')}）｜ "
        f"匹配分 {result['score']} ｜ 状态 "
        f"{STATUS_LABELS.get(run.get('status'), run.get('status'))} ｜ "
        f"置信度 {CONFIDENCE_LABELS.get(run.get('confidence'), run.get('confidence'))}"
    )
    st.caption("命中理由：" + "；".join(result["reasons"]))

    steps = run.get("steps") or []
    if steps:
        st.table(pd.DataFrame([
            {
                "步骤": step.get("title"),
                "状态": {
                    "pass": "通过",
                    "fail": "未通过",
                    "unknown": "未验证",
                    "skipped": "跳过",
                }.get(step.get("status"), step.get("status")),
                "观察值": step.get("observation") or "—",
                "说明": step.get("reason") or step.get("guidance") or "—",
            }
            for step in steps
        ]))

    missing = run.get("missing_sources") or []
    if missing:
        st.warning(
            "缺失数据源："
            + "；".join(
                f"{item.get('id')}（{item.get('reason') or '不可用'}）"
                for item in missing
            )
        )

    facts = run.get("facts") or []
    hypotheses = run.get("hypotheses") or []
    actions = run.get("next_actions") or []
    if facts:
        st.markdown("**已验证事实**")
        for item in facts:
            st.success(item.get("statement") or "—")
    if hypotheses:
        st.markdown("**待验证假设**")
        for item in hypotheses:
            st.warning(
                f"[{CONFIDENCE_LABELS.get(item.get('confidence'), item.get('confidence'))}] "
                f"{item.get('statement')}"
            )
    if actions:
        st.markdown("**下一步动作**")
        for item in actions:
            st.write(f"- {item}")


def _render_evaluation(service: ScenarioOpsService, workspace_id: str) -> None:
    section("覆盖评估", "对最近反馈批量匹配并执行场景，统计覆盖率与阻塞情况")

    limit = st.select_slider(
        "评估范围",
        options=[50, 100, 200, 500],
        value=100,
        key="scenario_eval_limit",
        format_func=lambda value: f"最近 {value} 条",
    )

    if st.button("评估场景覆盖", type="primary", key="scenario_eval_run"):
        with st.spinner("正在匹配并执行场景…"):
            st.session_state["scenario_eval_result"] = service.evaluate(
                workspace_id, limit=limit
            )

    result = st.session_state.get("scenario_eval_result")
    if not result:
        st.info("点击「评估场景覆盖」开始统计。")
        return

    summary = result["summary"]
    columns = st.columns(5)
    columns[0].metric("已评估", summary["evaluated"])
    columns[1].metric("场景覆盖率", f"{summary['coverage_rate'] * 100:.0f}%")
    columns[2].metric("已验证", summary["verified"])
    columns[3].metric("部分完成", summary["partial"])
    columns[4].metric("阻塞", summary["blocked"])

    st.caption(
        f"具体场景命中 {summary['specific_matched']} 条 ｜ "
        f"通用兜底 {summary['fallback']} 条 ｜ "
        f"未命中 {summary['unmatched']} 条 ｜ "
        f"具体场景验证率 {summary['verified_rate'] * 100:.0f}%"
    )

    overview = service.overview()
    st.dataframe(
        _format_scenarios(
            overview["scenarios"],
            {item["id"]: item for item in result["scenario_stats"]},
        ),
        use_container_width=True,
        hide_index=True,
    )

    st.markdown("#### 明细")
    st.dataframe(
        _format_rows(result["rows"]),
        use_container_width=True,
        hide_index=True,
    )


def render(db_path=None) -> None:
    hero(
        "故障场景",
        "把故障排查从单条样例变成可运营的场景库：看覆盖、看命中、看阻塞",
        ["Scenario Library", "Coverage", "Blocked Rate"],
    )

    initialize_database(db_path)
    repository = FeedbackRepository(db_path)
    workspace_id = repository.get_default_workspace()

    with st.expander("外部连接器（可选）"):
        use_connectors = st.checkbox(
            "场景试跑/评估时连接真实 ES 与 Prometheus",
            value=False,
            key="scenario_use_connectors",
            help="不勾选时只使用本地反馈与已入库证据；勾选后按场景配置查询外部源。",
        )
        es_url = st.text_input(
            "Elasticsearch 地址",
            value="http://localhost:9200",
            key="scenario_es_url",
            disabled=not use_connectors,
        )
        prom_url = st.text_input(
            "Prometheus 地址",
            value="http://localhost:9091",
            key="scenario_prom_url",
            disabled=not use_connectors,
        )

    connector_provider = None
    if use_connectors:
        connector_provider = ConnectorSourceProvider({
            "elasticsearch": {"hosts": [es_url]},
            "prometheus": {"base_url": prom_url},
        })

    service = ScenarioOpsService(
        db_path=db_path,
        feedback_repository=repository,
        connector_provider=connector_provider,
    )

    overview = service.overview()
    columns = st.columns(3)
    columns[0].metric("启用场景", overview["enabled_count"])
    columns[1].metric("配置错误", overview["config_error_count"])
    columns[2].metric("外部连接器场景", sum(
        1
        for item in overview["scenarios"]
        if item["connector_sources"]
    ))

    _render_library(service)
    st.divider()
    _render_evaluation(service, workspace_id)
    st.divider()
    _render_trial(service, workspace_id)
