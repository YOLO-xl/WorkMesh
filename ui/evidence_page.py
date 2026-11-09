"""证据关联页面。

把「证据接入」和「证据关联」放在一个页面里，按数据流顺序排列：
接入外部证据 → 选择反馈生成问题上下文 → 查看关联明细。

页面只负责展示与交互，取证和关联逻辑全部在 services/evidence_service.py。
"""

import json
from io import BytesIO
from pathlib import Path

import pandas as pd
import streamlit as st

from connectors import connector_registry
from core.correlation import DEFAULT_MIN_SCORE, DEFAULT_WINDOW_MINUTES
from database.evidence_repository import EvidenceRepository
from database.feedback_repository import FeedbackRepository
from database.migration import initialize_database
from services.evidence_service import TYPE_LABELS, EvidenceService


SAMPLE_LOG_PATH = (
    Path(__file__).resolve().parent.parent / "data" / "sample" / "log_sample.jsonl"
)

EVIDENCE_COLUMNS = [
    "时间",
    "类型",
    "服务/产品",
    "级别",
    "错误码",
    "分数",
    "强度",
    "命中键",
    "内容",
]


def _build_service(db_path=None) -> EvidenceService:
    initialize_database(db_path)
    return EvidenceService(
        evidence_repository=EvidenceRepository(db_path),
        feedback_repository=FeedbackRepository(db_path),
        db_path=db_path,
    )


def _read_sample_logs(path: Path = SAMPLE_LOG_PATH) -> list[dict]:
    if not path.exists():
        return []

    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        records.append(json.loads(line))

    return records


def _parse_uploaded_evidence(uploaded_file) -> tuple[list[dict], str]:
    """解析本地证据文件，返回 (记录列表, 错误信息)。"""

    name = uploaded_file.name.lower()
    content = uploaded_file.getvalue()

    if name.endswith(".jsonl"):
        records = [
            json.loads(line)
            for line in content.decode("utf-8-sig").splitlines()
            if line.strip()
        ]
        return records, ""

    if name.endswith(".json"):
        data = json.loads(content.decode("utf-8-sig"))
        if isinstance(data, dict):
            data = [data]
        if not isinstance(data, list):
            return [], "JSON 结构应为对象或对象数组"
        return data, ""

    if name.endswith(".csv"):
        df = pd.read_csv(BytesIO(content), encoding="utf-8-sig")
        df = df.where(pd.notna(df), None)
        return df.to_dict(orient="records"), ""

    return [], "仅支持 JSON、JSONL、CSV 格式"


def _format_matches(matches: list[dict]) -> pd.DataFrame:
    rows = []

    for item in matches:
        evidence = item["evidence"]
        rows.append({
            "时间": (
                evidence.timestamp.strftime("%m-%d %H:%M:%S")
                if evidence.timestamp
                else "—"
            ),
            "类型": TYPE_LABELS.get(
                evidence.evidence_type, evidence.evidence_type
            ),
            "服务/产品": evidence.service or "—",
            "级别": evidence.level or "—",
            "错误码": evidence.error_code or "—",
            "分数": item["score"],
            "强度": {
                "strong": "强关联",
                "medium": "参考",
                "weak": "弱关联",
            }.get(item["strength"], item["strength"]),
            "命中键": "、".join(item["matched_keys"]),
            "内容": evidence.display_label(),
        })

    return pd.DataFrame(rows, columns=EVIDENCE_COLUMNS)


def _render_ingest(service: EvidenceService, workspace_id: str) -> None:
    st.subheader("接入外部证据")
    st.caption(
        "日志、指标、链路追踪接入后统一存储为 Evidence，"
        "原始字段完整保留在 raw_data 中。"
    )

    repository = service.evidence_repository
    st.metric("已接入证据", repository.count_evidence(workspace_id))

    mode = st.radio(
        "接入方式",
        ["本地证据文件", "Elasticsearch 日志"],
        horizontal=True,
        key="evidence_ingest_mode",
    )

    if mode == "本地证据文件":
        st.write(
            "支持 JSON / JSONL / CSV。字段建议包含："
            "`record_id`、`timestamp`、`uid`、`request_id`、"
            "`trace_id`、`service`、`level`、`error_code`、`message`。"
        )

        uploaded = st.file_uploader(
            "上传证据文件",
            type=["json", "jsonl", "csv"],
            key="evidence_file_uploader",
        )

        col1, col2 = st.columns([1, 1])

        with col1:
            ingest_file = st.button(
                "接入上传的文件",
                disabled=uploaded is None,
                use_container_width=True,
            )

        with col2:
            ingest_sample = st.button(
                "接入内置样例日志",
                use_container_width=True,
                help="data/sample/log_sample.jsonl，用于离线体验关联能力",
            )

        if ingest_file and uploaded is not None:
            try:
                records, error = _parse_uploaded_evidence(uploaded)
            except (ValueError, UnicodeDecodeError) as exc:
                st.error(f"文件解析失败：{exc}")
                records, error = [], ""

            if error:
                st.error(error)
            elif not records:
                st.warning("文件中没有可接入的证据记录。")
            else:
                result = service.ingest_connector_records(
                    workspace_id,
                    records,
                    source_name=f"文件：{uploaded.name}",
                    source_type="log",
                    connector_type="file",
                )
                st.session_state["evidence_ingest_result"] = result

        if ingest_sample:
            records = _read_sample_logs()

            if not records:
                st.error("未找到内置样例日志 data/sample/log_sample.jsonl")
            else:
                result = service.ingest_connector_records(
                    workspace_id,
                    records,
                    source_name="内置样例日志",
                    source_type="log",
                    connector_type="file",
                )
                st.session_state["evidence_ingest_result"] = result

        result = st.session_state.get("evidence_ingest_result")

        if result:
            st.divider()
            c1, c2, c3 = st.columns(3)
            c1.metric("写入", result["inserted"])
            c2.metric("已存在跳过", result["skipped"])
            c3.metric("失败", result["failed"])
            st.caption(f"数据源：{result.get('source_name', '—')}")

            if result["errors"]:
                st.dataframe(
                    pd.DataFrame(result["errors"]),
                    use_container_width=True,
                )

    else:
        st.write("查询范围为只读，连接信息仅保存在当前会话中。")

        with st.form("es_evidence_form"):
            hosts = st.text_input(
                "Elasticsearch 地址", "http://localhost:9200"
            )
            index = st.text_input("索引", "app-logs")
            username = st.text_input("用户名（可选）")
            password = st.text_input("密码（可选）", type="password")

            uid = st.text_input("UID（可选）")
            error_code = st.text_input("错误码（可选）")
            keyword = st.text_input("关键词（可选）")

            submitted = st.form_submit_button("测试连接并查询")

        if submitted:
            config = {
                "hosts": [
                    item.strip()
                    for item in hosts.split(",")
                    if item.strip()
                ],
                "index": index,
            }

            if username and password:
                config["username"] = username
                config["password"] = password

            try:
                connector = connector_registry.create(
                    "elasticsearch", config
                )
                test = connector.test_connection()

                if not test["success"]:
                    st.error(test["message"])
                else:
                    st.success(
                        f"连接成功：{test.get('cluster_name')} "
                        f"v{test.get('version')}"
                    )

                    records = connector.query(
                        index=index,
                        uid=uid or None,
                        error_code=error_code or None,
                        keyword=keyword or None,
                        size=100,
                    )
                    normalized = connector.normalize(records)

                    result = service.ingest_connector_records(
                        workspace_id,
                        normalized,
                        source_name=f"Elasticsearch：{index}",
                        source_type="log",
                        connector_type="elasticsearch",
                    )
                    st.info(
                        f"查询 {len(records)} 条，写入 {result['inserted']} 条，"
                        f"跳过 {result['skipped']} 条。"
                    )
            except Exception as exc:
                st.error(f"Elasticsearch 查询失败：{exc}")


def _render_context(service: EvidenceService, workspace_id: str) -> None:
    st.subheader("生成问题上下文")

    feedback_repository = service.feedback_repository

    page = feedback_repository.list_feedback(
        workspace_id, page=1, page_size=100
    )["records"]

    if not page:
        st.info("还没有反馈数据，请先到「数据接入」导入反馈文件。")
        return

    def label(record):
        created = (record.get("feedback_time") or "—")[:16]
        description = (record.get("description") or "")[:24]
        return f"{created} | {record.get('error_code') or '—'} | {description}"

    feedback_id = st.selectbox(
        "选择一条反馈作为关联锚点",
        options=[record["id"] for record in page],
        format_func=lambda value: label(
            next(record for record in page if record["id"] == value)
        ),
        key="evidence_anchor_feedback",
    )

    col1, col2 = st.columns(2)

    with col1:
        window_minutes = st.slider(
            "关联时间窗口（分钟）",
            min_value=5,
            max_value=240,
            value=DEFAULT_WINDOW_MINUTES,
            step=5,
        )

    with col2:
        min_score = st.slider(
            "最低关联分数",
            min_value=10,
            max_value=80,
            value=int(DEFAULT_MIN_SCORE),
            step=5,
        )

    include_related = st.checkbox(
        "同时检索其他反馈（同 UID / 同错误码）",
        value=True,
    )

    if not st.button("生成证据上下文", type="primary"):
        if "evidence_context" in st.session_state:
            _render_context_result(st.session_state["evidence_context"])
        return

    try:
        context = service.build_context(
            workspace_id,
            feedback_id,
            window_minutes=window_minutes,
            min_score=min_score,
            include_related_feedback=include_related,
        )
    except LookupError as exc:
        st.error(str(exc))
        return

    st.session_state["evidence_context"] = context
    _render_context_result(context)


def _render_context_result(context: dict) -> None:
    st.divider()

    anchor = context["anchor"]
    st.markdown(f"**锚点反馈**：{anchor.message or '（无描述）'}")
    st.caption(
        f"UID {anchor.uid or '—'} ｜ 错误码 {anchor.error_code or '—'} ｜ "
        f"产品 {anchor.service or '—'} ｜ "
        f"时间 {anchor.timestamp or '—'} ｜ "
        f"窗口 {context['window_minutes']} 分钟"
    )

    counts = context["counts"]

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("候选证据", counts["candidates"])
    c2.metric("关联证据", counts["matched"])
    c3.metric("强关联", counts["strong"])
    c4.metric("其他反馈", counts["related_feedback"])

    st.info(context["summary"])

    st.markdown("#### 已验证事实")
    if context["facts"]:
        for fact in context["facts"]:
            st.success(
                f"{fact['statement']}（证据 {', '.join(fact['evidence_ids'][:5])}）"
            )
    else:
        st.caption("没有可直接验证的标识一致项。")

    st.markdown("#### 待验证假设")
    for hypothesis in context["hypotheses"]:
        st.warning(f"[{hypothesis['status']}] {hypothesis['statement']}")

    st.markdown("#### 关联证据")
    if not context["matches"]:
        st.caption("当前窗口内没有可关联证据。")
        return

    st.dataframe(
        _format_matches(context["matches"]),
        use_container_width=True,
        hide_index=True,
    )

    with st.expander("查看单条证据的关联理由与原始数据"):
        for item in context["matches"]:
            evidence = item["evidence"]
            st.markdown(
                f"**{TYPE_LABELS.get(evidence.evidence_type, evidence.evidence_type)}"
                f"｜{evidence.display_label()}**"
            )
            st.caption(
                f"分数 {item['score']} ｜ 强度 {item['strength']} ｜ "
                f"命中键 {'、'.join(item['matched_keys'])}"
            )
            for reason in item["reasons"]:
                st.write(f"- {reason}")
            st.json(evidence.raw_data or {"record_id": evidence.record_id})
            st.divider()


def _render_saved_links(service: EvidenceService, workspace_id: str) -> None:
    st.subheader("已保存的关联")

    feedback_repository = service.feedback_repository

    page = feedback_repository.list_feedback(
        workspace_id, page=1, page_size=100
    )["records"]

    if not page:
        st.caption("暂无反馈数据。")
        return

    feedback_id = st.selectbox(
        "反馈记录",
        options=[record["id"] for record in page],
        format_func=lambda value: next(
            (
                f"{(record.get('description') or '')[:24]} | "
                f"{record.get('error_code') or '—'}"
                for record in page
                if record["id"] == value
            ),
            value,
        ),
        key="evidence_saved_links_feedback",
    )

    links = service.evidence_repository.list_linked_evidence(
        workspace_id, "feedback", feedback_id
    )

    if not links:
        st.caption("该反馈还没有保存的关联，先在上方生成一次上下文。")
        return

    st.dataframe(
        pd.DataFrame([
            {
                "时间": item["timestamp"],
                "类型": TYPE_LABELS.get(
                    item["evidence_type"], item["evidence_type"]
                ),
                "服务/产品": item.get("service"),
                "错误码": item.get("error_code"),
                "分数": item["link_score"],
                "强度": item["link_strength"],
                "理由": "；".join(item["reasons"]),
            }
            for item in links
        ]),
        use_container_width=True,
        hide_index=True,
    )


def render(db_path=None) -> None:
    """渲染证据关联页面。"""

    st.title("证据关联")
    st.caption(
        "把用户反馈和日志、其他反馈放在同一条时间线上，"
        "给出可解释的关联结果，并区分已验证事实与待验证假设。"
    )
    st.divider()

    service = _build_service(db_path)
    workspace_id = service.feedback_repository.get_default_workspace()

    tab_ingest, tab_context = st.tabs(["证据接入", "证据关联"])

    with tab_ingest:
        _render_ingest(service, workspace_id)

    with tab_context:
        _render_context(service, workspace_id)
        st.divider()
        _render_saved_links(service, workspace_id)
