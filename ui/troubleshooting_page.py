"""故障排查流程页面。

从一条反馈出发，按「反馈接入 → 多系统查询 → 证据关联 → 场景判定 →
结论与原始追溯」展示完整链路。页面只做编排结果的呈现。
"""

from __future__ import annotations

import json

import pandas as pd
import streamlit as st

from database.migration import initialize_database
from services.connector_config import (
    DEFAULT_ELASTICSEARCH_URL,
    DEFAULT_PROMETHEUS_URL,
    build_connector_configs,
)
from services.troubleshooting_service import TroubleshootingService
from services.evidence_service import TYPE_LABELS
from ui.theme import hero


STAGE_STATUS_LABELS = {
    "done": "完成",
    "empty": "无数据",
    "partial": "部分完成",
    "blocked": "阻塞",
    "limited": "有边界",
    "none": "未命中",
    "verified": "已验证",
    "skipped": "跳过",
}


def _build_service(db_path=None) -> TroubleshootingService:
    initialize_database(db_path)
    return TroubleshootingService(db_path=db_path)


def _feedback_label(record: dict) -> str:
    stamp = str(record.get("feedback_time") or "—")[:16]
    code = record.get("error_code") or "—"
    description = str(record.get("description") or "")[:34]
    return f"{stamp} | {code} | {description}"


def _stage_metric(stage: dict) -> str:
    status = stage.get("status") or "partial"
    return STAGE_STATUS_LABELS.get(status, status)


def _format_chain(chain: list[dict]) -> pd.DataFrame:
    rows = []

    for item in chain:
        rows.append({
            "时间": str(item.get("timestamp") or "—").replace("T", " ").replace("Z", ""),
            "角色": "锚点" if item.get("role") == "anchor" else "证据",
            "类型": item.get("type_label") or "—",
            "来源": item.get("source_name") or "—",
            "服务": item.get("service") or "—",
            "错误码": item.get("error_code") or "—",
            "Trace": item.get("trace_id") or "—",
            "分数": item.get("score") if item.get("score") is not None else "—",
            "强度": {
                "anchor": "锚点",
                "strong": "强关联",
                "medium": "参考",
                "weak": "弱关联",
                "none": "未关联",
            }.get(item.get("strength"), item.get("strength")),
            "内容": item.get("summary") or "—",
        })

    return pd.DataFrame(rows)


def _format_sources(sources: list[dict]) -> pd.DataFrame:
    return pd.DataFrame([
        {
            "数据源": item.get("platform") or item.get("id"),
            "状态": "可用" if item.get("available") else "不可用",
            "命中": item.get("count", 0),
            "入库": item.get("persisted", "—"),
            "说明": item.get("message") or "—",
        }
        for item in sources
    ])


def _format_external_ingest(reports: list[dict]) -> pd.DataFrame:
    return pd.DataFrame([
        {
            "数据源": item.get("source_name") or item.get("source_id"),
            "连接器": item.get("connector_type") or "—",
            "查询命中": item.get("total", 0),
            "新增入库": item.get("inserted", 0),
            "已存在": item.get("skipped", 0),
            "失败": item.get("failed", 0),
            "说明": item.get("error") or "—",
        }
        for item in reports
    ])


def _format_query_hits(hits: list[dict]) -> pd.DataFrame:
    return pd.DataFrame([
        {
            "平台": item.get("platform"),
            "类型": item.get("kind_label"),
            "时间": str(item.get("timestamp") or "").replace("T", " ").replace("Z", ""),
            "UID": item.get("uid") or "—",
            "错误码": item.get("error_code") or "—",
            "服务": item.get("service") or "—",
            "关联分": item.get("score", 0),
            "证据ID": item.get("evidence_id") or "—",
            "关系": {
                "correlates": "关联",
                "context": "上下文",
                "unrelated": "未关联",
            }.get(item.get("relation"), item.get("relation")),
            "摘要": item.get("summary") or "—",
        }
        for item in hits
    ])


def _render_stages(result: dict) -> None:
    stages = result.get("stages") or []
    if not stages:
        return

    st.markdown("#### 排查链路")
    columns = st.columns(len(stages))

    for column, index, stage in zip(columns, range(1, len(stages) + 1), stages):
        with column:
            status_label = _stage_metric(stage)
            st.metric(
                f"{index}. {stage.get('title')}",
                status_label,
            )
            st.caption(stage.get("summary") or "—")
            for metric in stage.get("metrics") or []:
                st.write(
                    f"{metric.get('label')}：**{metric.get('value')}**"
                )

    st.caption(
        "每一段都保留输入、输出和证据 ID；场景结论没有证据引用时，"
        "只作为排查方向，不作为根因。"
    )


def _render_conclusions(result: dict) -> None:
    scenario = result.get("scenario") or {}
    diagnosis = result.get("diagnosis") or {}
    context = result.get("context") or {}

    st.markdown("#### 已验证事实")
    facts = (scenario.get("facts") or []) + (context.get("facts") or [])
    if facts:
        for fact in facts:
            evidence_ids = fact.get("evidence_ids") or []
            suffix = (
                f"（证据 {', '.join(evidence_ids[:4])}）"
                if evidence_ids else ""
            )
            st.success(f"{fact.get('statement')}{suffix}")
    else:
        st.caption("当前没有可直接验证的标识一致项。")

    st.markdown("#### 待验证假设")
    hypotheses = (scenario.get("hypotheses") or []) + (
        context.get("hypotheses") or []
    )
    if hypotheses:
        for item in hypotheses:
            status = item.get("status") or item.get("confidence") or "待验证"
            st.warning(f"[{status}] {item.get('statement')}")
    else:
        st.caption("当前没有足够证据形成假设。")

    st.markdown("#### 排查动作")
    next_steps = diagnosis.get("next_steps") or []
    if next_steps:
        for step in next_steps:
            st.write(f"- {step.get('action')}")
    else:
        st.caption("暂无下一步动作。")

    limits = (scenario.get("limits") or []) + (
        diagnosis.get("limits") or []
    )
    if limits:
        with st.expander("结论边界"):
            for limit in limits:
                st.caption(f"- {limit}")


def _render_traceability(result: dict, service: TroubleshootingService, workspace_id: str) -> None:
    chain = result.get("chain") or []
    traceability = result.get("traceability") or {}

    st.markdown("#### 原始证据追溯")
    st.caption(
        "展开任意一条证据，可看到入库时的原始记录、来源和 Trace / Request ID；"
        "这些字段就是结论引用的依据。"
    )

    if traceability.get("all_conclusions_traceable"):
        st.success("所有场景结论的证据 ID 都能在证据链中找到。")
    else:
        st.warning(
            "以下证据 ID 未出现在证据链中："
            + "、".join(traceability.get("missing_evidence_ids") or [])
        )

    for item in chain:
        title = (
            f"{item.get('type_label')} ｜ {item.get('source_name')} ｜ "
            f"{item.get('record_id') or '—'}"
        )
        with st.expander(title):
            st.caption(
                f"时间 {item.get('timestamp')} ｜ 服务 {item.get('service') or '—'} ｜ "
                f"错误码 {item.get('error_code') or '—'} ｜ "
                f"Trace {item.get('trace_id') or '—'} ｜ "
                f"Request {item.get('request_id') or '—'}"
            )
            if item.get("reasons"):
                st.write("关联理由：" + "；".join(item["reasons"]))
            st.json(item.get("raw") or {})

            if item.get("evidence_id"):
                if st.button(
                    "读取数据库原始记录",
                    key=f"trace_btn_{item['evidence_id']}",
                ):
                    traced = service.trace(workspace_id, item["evidence_id"])
                    st.session_state["troubleshooting_trace"] = traced

            traced = st.session_state.get("troubleshooting_trace")
            if traced and traced.get("evidence_id") == item.get("evidence_id"):
                st.caption(
                    f"来源：{(traced.get('source') or {}).get('name') or '—'} ｜ "
                    f"记录：{traced.get('record_id') or '—'}"
                )
                st.json(traced.get("raw") or {})


def _render_query(result: dict) -> None:
    query = result.get("query") or {}

    st.markdown("#### 多系统查询")
    st.caption(
        "同一组过滤条件同时查本地反馈、本地证据和可选外部连接器；"
        "外部源不可用时保留状态，不用伪造数据补齐。"
    )

    sources = query.get("sources") or []
    if sources:
        st.dataframe(
            _format_sources(sources),
            use_container_width=True,
            hide_index=True,
        )

    external_ingest = query.get("external_ingest") or []
    if external_ingest:
        st.caption(
            "外部连接器结果已通过统一 Evidence 入口落库；"
            "同数据源同 record_id 的重复查询只复用已有记录。"
        )
        st.dataframe(
            _format_external_ingest(external_ingest),
            use_container_width=True,
            hide_index=True,
        )
        failed = [
            item for item in external_ingest if item.get("failed")
        ]
        if failed:
            st.warning(
                "以下外部证据未完全入库，诊断只使用成功写入的记录："
                + "、".join(
                    f"{item.get('source_name') or item.get('source_id')}"
                    f"（{item.get('error') or '部分失败'}）"
                    for item in failed
                )
            )

    hits = query.get("hits") or []
    if hits:
        st.dataframe(
            _format_query_hits(hits),
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.caption("当前过滤条件下没有命中记录。")


def _render_external_sources() -> list[dict]:
    """可选的跨平台查询源；没有实例时只保留不可用状态。"""

    sources: list[dict] = []

    with st.expander("外部数据源（可选）"):
        st.caption(
            "勾选后会把同一组过滤条件发到外部系统；连接失败时保留不可用状态，"
            "不会用本地数据冒充外部结果。"
        )

        use_es = st.checkbox(
            "查询 Elasticsearch",
            key="troubleshooting_use_es",
        )
        if use_es:
            hosts = st.text_input(
                "Elasticsearch 地址",
                DEFAULT_ELASTICSEARCH_URL,
                key="troubleshooting_es_hosts",
            )
            index = st.text_input(
                "索引",
                "app-live-*",
                key="troubleshooting_es_index",
            )
            username = st.text_input(
                "ES 用户名（可选）",
                key="troubleshooting_es_user",
            )
            password = st.text_input(
                "ES 密码（可选）",
                type="password",
                key="troubleshooting_es_password",
            )
            api_key = st.text_input(
                "ES API Key（可选）",
                type="password",
                key="troubleshooting_es_api_key",
            )
            keyword = st.text_input(
                "ES 关键词（可选）",
                key="troubleshooting_es_keyword",
            )

            if hosts and index:
                config = build_connector_configs(
                    elasticsearch_urls=hosts,
                    elasticsearch_index=index,
                    elasticsearch_username=username,
                    elasticsearch_password=password,
                    elasticsearch_api_key=api_key,
                )["elasticsearch"]

                query = {"index": index, "size": 100}
                if keyword:
                    query["keyword"] = keyword

                sources.append({
                    "id": "es_live",
                    "connector_type": "elasticsearch",
                    "config": config,
                    "query": query,
                    "display_name": "Elasticsearch",
                })

        use_prom = st.checkbox(
            "查询 Prometheus",
            key="troubleshooting_use_prom",
        )
        if use_prom:
            base_url = st.text_input(
                "Prometheus 地址",
                DEFAULT_PROMETHEUS_URL,
                key="troubleshooting_prom_url",
            )
            promql = st.text_input(
                "PromQL",
                "sum(live_player_init_fail_rate)",
                key="troubleshooting_promql",
            )
            token = st.text_input(
                "Bearer Token（可选）",
                type="password",
                key="troubleshooting_prom_token",
            )

            if base_url and promql:
                config = build_connector_configs(
                    prometheus_url=base_url,
                    prometheus_bearer_token=token,
                )["prometheus"]

                sources.append({
                    "id": "prom_live",
                    "connector_type": "prometheus",
                    "config": config,
                    "query": {"promql": promql},
                    "display_name": "Prometheus",
                })

    return sources


def _render_result(
    result: dict,
    service: TroubleshootingService,
    workspace_id: str,
) -> None:
    anchor = result.get("anchor") or {}
    st.divider()
    st.markdown(
        f"**当前样例**：{anchor.get('service') or '—'} / "
        f"{anchor.get('error_code') or '—'} ｜ {anchor.get('message') or '—'}"
    )
    st.caption(
        f"UID {anchor.get('uid') or '—'} ｜ 时间 {anchor.get('timestamp') or '—'} ｜ "
        f"场景 {(result.get('scenario') or {}).get('name') or '未命中'}"
    )

    _render_stages(result)

    st.divider()
    _render_conclusions(result)

    st.divider()
    chain = result.get("chain") or []
    st.markdown(f"#### 证据链（{len(chain)} 条）")
    st.dataframe(
        _format_chain(chain),
        use_container_width=True,
        hide_index=True,
    )

    _render_query(result)

    st.divider()
    _render_traceability(result, service, workspace_id)

    st.divider()
    st.download_button(
        "下载排查记录（Markdown）",
        data=(result.get("markdown") or "").encode("utf-8"),
        file_name="workmesh_troubleshooting.md",
        mime="text/markdown",
        use_container_width=True,
    )


def render(db_path=None) -> None:
    """渲染故障排查页面。"""

    hero(
        "故障排查",
        "从一条用户反馈出发，串起多系统查询、证据关联、场景判定和原始证据追溯",
        ["Feedback → Evidence", "Cross-source Query", "Traceable"],
    )

    service = _build_service(db_path)
    workspace_id = service.feedback_repository.get_default_workspace()

    records = service.feedback_repository.list_feedback(
        workspace_id, page=1, page_size=500
    )["records"]

    if not records:
        st.info("还没有反馈数据，请先到「数据接入」导入反馈文件。")
        return

    st.markdown("#### 选择排查对象")
    st.caption(
        "内置样例 F001 是「进入直播间后一直加载无法播放」，"
        "已在库中对应到 trace-0001 的直播日志和 4 条直播指标。"
    )

    external_sources = _render_external_sources()

    col1, col2, col3 = st.columns([2.2, 1, 1])

    with col1:
        feedback_id = st.selectbox(
            "反馈记录",
            options=[record["id"] for record in records],
            format_func=lambda value: _feedback_label(
                next(record for record in records if record["id"] == value)
            ),
            key="troubleshooting_feedback",
        )

    with col2:
        sample_clicked = st.button(
            "运行内置样例",
            use_container_width=True,
            help="F001 进入直播间失败",
        )

    with col3:
        run_clicked = st.button(
            "开始排查",
            type="primary",
            use_container_width=True,
        )

    if sample_clicked:
        sample = service.find_live_sample(workspace_id)
        if sample:
            st.session_state["troubleshooting_result"] = service.run(
                workspace_id,
                sample["id"],
                external_sources=external_sources,
            )
        else:
            st.warning("库中没有找到直播样例 F001。")

    elif run_clicked:
        st.session_state["troubleshooting_result"] = service.run(
            workspace_id,
            feedback_id,
            external_sources=external_sources,
        )

    result = st.session_state.get("troubleshooting_result")
    if not result:
        st.info(
            "点击「运行内置样例」直接看直播进入房间失败的完整证据链，"
            "或选择任意反馈后开始排查。"
        )
        return

    _render_result(result, service, workspace_id)
