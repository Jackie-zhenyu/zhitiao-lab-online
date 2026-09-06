"""CSV 导入、显式确认、质量检查及连续区间绘图的会话工作流。"""

import pandas as pd
import streamlit as st
from pydantic import ValidationError

from core.import_models import (
    IMPORT_VERSION, OPTIONAL_FIELDS, REQUIRED_FIELDS, MappingConfig,
    ParserOptions, QualityConfig,
)
from core.models import SourceKind
from core.parser import CSVImportError, read_limited, recommend_mapping
from core.quality import check_quality, prepare_experiment, select_interval
from examples.catalog import list_examples, load_example
from examples.closed_loop import list_closed_loop_examples, load_closed_loop_example
from ui.charts import build_charts
from ui.components import render_page_header, render_unimplemented, sidebar_controls
from ui.session import LabSession, fingerprint
from ui.performance import render_performance
from ui.evidence import render_evidence
from ui.comparison import render_save_experiment


def render_import_page() -> None:
    render_page_header("实验分析", "从原始日志出发，先确认含义，再检查数据与查看曲线。")
    state = st.session_state.setdefault("lab_session", LabSession())
    state.traceability = None
    state.performance = None
    with st.sidebar:
        with st.expander("导入设置 · 编码、分隔符与读取限制"):
            first, second = st.columns(2)
            encoding = first.selectbox("文件编码", ["auto", "utf-8", "utf-8-sig", "gb18030", "utf-16", "cp1252"], key="csv_encoding", format_func=lambda v: "自动（严格 UTF-8 / BOM）" if v == "auto" else v)
            delimiter = second.selectbox("分隔符", ["auto", ",", ";", "\t"], key="csv_delimiter", format_func=lambda v: {"auto": "自动识别", ",": "逗号 ,", ";": "分号 ;", "\t": "制表符 Tab"}[v])
            max_mib = first.number_input("单文件上限（MiB）", min_value=1, max_value=200, value=20, step=1, key="max_mib")
            max_rows = second.number_input("数据行数上限（不含表头）", min_value=1, max_value=2000000, value=200000, step=1000, key="max_rows")
            st.caption("按字节和 CSV 记录限制读取；超限拒绝导入，不截断为一份看似完整的数据。自动识别失败时请手动选择编码/分隔符。")
        options = ParserOptions(encoding=encoding, delimiter=delimiter, max_bytes=int(max_mib) * 1024 * 1024, max_rows=int(max_rows))
        st.subheader("01 / 导入日志")
        mode = st.radio("数据入口", ["上传 CSV", "加载示例"], horizontal=True, key="input_mode")
        if st.session_state.get("lab_input_mode") != mode:
            state.clear()
            st.session_state["lab_input_mode"] = mode
        try:
            if mode == "上传 CSV":
                uploaded = st.file_uploader("选择 CSV 文件", type=["csv", "tsv"], max_upload_size=int(max_mib), key=st.session_state.get("analysis_uploader_key", "analysis_csv"), on_change=_upload_changed)
                source_kind = st.selectbox("声明数据来源", ["unknown", "measured", "simulated"], format_func=lambda v: {"unknown": "未知 / 尚未确认", "measured": "实测数据（用户声明）", "simulated": "合成或仿真数据（用户声明）"}[v], key="source_kind")
                if uploaded is not None:
                    if uploaded.size > options.max_bytes:
                        raise CSVImportError(f"文件超过 {max_mib} MiB 限制")
                    uploaded.seek(0)
                    raw = read_limited(uploaded, options.max_bytes)
                    state.set_source(raw, uploaded.name, {"source_kind": source_kind})
                elif state.raw_bytes is not None:
                    # 离开本页后 Streamlit 会销毁上传控件，业务数据仍保留在会话中。
                    metadata = state.source_metadata if state.source_metadata.get("source_kind", "unknown") == source_kind else {"source_kind": source_kind}
                    state.set_source(state.raw_bytes, state.filename, metadata)
                    st.caption(f"会话内已保留：{state.filename}。可重新上传替换，不必因页面切换重传。")
                    if st.button("清除保留的上传数据", key="clear_retained_source"):
                        state.clear()
            else:
                _render_examples(state, options)
        except (OSError, ValueError) as exc:
            state.clear()
            st.error(str(exc))
        st.caption(f"当前读取上限：{max_mib} MiB / {max_rows:,} 数据行。未下载项目包的数据仅保留在当前会话；新会话可上传项目包恢复。")
    if state.raw_bytes is None:
        st.info("请上传 CSV，或选择一个示例并点击“一键加载示例”。")
        _future_features()
        return
    try:
        parsed = state.ensure_parsed(options)
    except (CSVImportError, ValueError) as exc:
        st.error(f"导入失败：{exc}")
        return
    st.success(f"已读取 {parsed.filename} · {len(parsed.frame):,} 数据行 · {len(parsed.frame.columns)} 列")
    if state.source_metadata.get("source_label"):
        label = state.source_metadata["source_label"]
        if label in ("闭环仿真数据，非实测", "解析函数合成数据"):
            st.warning(label)
        else:
            st.caption("原项目来源标签（未独立鉴定）：" + label)
    else:
        st.caption(f"来源声明：{state.source_metadata.get('source_kind', 'unknown')}；标签不等于真实性鉴定。")
    with st.expander("原始数据预览与文件摘要"):
        st.code(parsed.sha256, language=None)
        st.caption(f"SHA-256 · {len(parsed.raw_bytes):,} 原始字节 · 实际编码 {parsed.encoding} · 分隔符 {repr(parsed.delimiter)}；原始字节不修改。")
        preview = parsed.frame.head(15).copy()
        preview.index = preview.index + 1
        st.dataframe(preview, width="stretch")
        st.caption("预览前 15 条数据记录，索引从 1 开始，不含表头。")
    _render_mapping(state)
    if state.prepared is not None and state.quality is not None:
        results, events = st.tabs(["质量、指标与曲线", "异常证据"])
        with events:
            render_evidence(state)
        with results:
            _render_quality_and_curves(state)
    _future_features()


def _upload_changed() -> None:
    """用户实际移除上传文件时清除数据；导航造成控件卸载不触发此回调。"""
    if st.session_state.get(st.session_state.get("analysis_uploader_key", "analysis_csv")) is None:
        st.session_state["lab_session"].clear()


def _render_examples(state: LabSession, options: ParserOptions) -> None:
    examples = [*list_examples(), *list_closed_loop_examples()]
    catalog = {item["example_id"]: item for item in examples}
    selected = st.selectbox("选择示例数据", list(catalog), key="example_choice", format_func=lambda v: catalog[v]["title"])
    metadata = catalog[selected]
    st.caption(metadata["description"])
    st.info(f"来源：{metadata['source_label']}。解析函数示例不声称闭环仿真；闭环示例有单独方程和参数清单。加载后仍需确认字段及单位。")
    if state.source_metadata.get("example_id") not in (None, selected):
        state.clear()
    loader = load_closed_loop_example if selected.startswith("closed_loop_") else load_example
    filename, raw, metadata = loader(selected, max_bytes=options.max_bytes)
    first, second = st.columns(2)
    if first.button("一键加载示例", key="load_example", type="primary"):
        state.set_source(raw, filename, metadata)
    second.download_button("下载此示例 CSV", data=raw, file_name=filename, mime="text/csv", key="download_example", on_click="ignore")


@sidebar_controls
def _render_mapping(state: LabSession) -> None:
    parsed = state.parsed
    st.subheader("02 / 确认字段与单位")
    st.caption("列名推荐仅供核对。目标值和实测值必须是同一物理量且单位一致；不会猜测编码器分辨率或把脉冲换算成角度。")
    recommended = recommend_mapping(list(parsed.frame.columns))
    # 用明确字符串代表未选择，避免控件把 None 当作没有提交的默认值。
    unset = "— 未映射 —"
    while unset in parsed.frame.columns:
        unset += "—"
    candidates = [unset, *parsed.frame.columns]
    fields = {}
    required_columns = st.columns(3)
    names = {"time": "时间", "target": "目标值", "actual": "实测值", "control": "控制输出", "p_term": "P 分量", "i_term": "I 分量", "d_term": "D 分量"}
    for column, field in zip(required_columns, REQUIRED_FIELDS):
        value = column.selectbox(f"{field} · {names[field]}（必需）", candidates, index=candidates.index(recommended.get(field, unset)), key=f"map_{state.revision}_{field}")
        if value != unset:
            fields[field] = value
    with st.expander("可选字段 · 控制输出及 P / I / D 分量"):
        optional_units = {}
        for field in OPTIONAL_FIELDS:
            left, right = st.columns(2)
            value = left.selectbox(f"{field} · {names[field]}", candidates, index=candidates.index(recommended.get(field, unset)), key=f"map_{state.revision}_{field}")
            if value != unset:
                fields[field] = value
                default_unit = state.source_metadata.get("optional_units", {}).get(field, state.source_metadata.get("control_unit", "") if field == "control" else "")
                optional_units[field] = right.text_input(f"{field} 单位", value=default_unit, key=f"unit_{state.revision}_{field}", placeholder="例如 V、A、1")
    first, second, third = st.columns(3)
    time_unit = first.selectbox("原始时间单位", ["s", "ms"], index=0 if state.source_metadata.get("time_unit", "s") == "s" else 1, key=f"time_unit_{state.revision}", format_func=lambda v: "秒 s" if v == "s" else "毫秒 ms")
    quantity = second.text_input("目标 / 实测物理量名称", value=state.source_metadata.get("quantity", ""), key=f"quantity_{state.revision}", placeholder="例如转速、位置、脉冲计数")
    unit = third.text_input("目标 / 实测共同单位", value=state.source_metadata.get("unit", ""), key=f"physical_unit_{state.revision}", placeholder="例如 rpm、rad、pulse、1")
    units_consistent = st.checkbox("我确认目标值与实测值是同一物理量，且原始单位一致", key=f"units_confirmed_{state.revision}")
    with st.expander("质量规则 · 采样间隔阈值"):
        lower_col, upper_col = st.columns(2)
        lower = lower_col.number_input("最小间隔 / 中位间隔", min_value=0.01, max_value=0.99, value=0.5, step=0.05, key="interval_lower")
        upper = upper_col.number_input("最大间隔 / 中位间隔", min_value=1.01, max_value=100.0, value=2.0, step=0.25, key="interval_upper")
        st.caption("以原顺序相邻有效正时间差的中位数为基准，超出比例范围即标记并断开连续区间。这是数据检查规则，不等于设备故障；修改后需重新确认。")
    draft = {"source": state.parse_key, "fields": fields, "time_unit": time_unit, "quantity": quantity, "unit": unit, "optional_units": optional_units, "units_consistent": units_consistent, "min_interval_ratio": lower, "max_interval_ratio": upper, "version": IMPORT_VERSION}
    draft_key = fingerprint(draft)
    previous = state.confirmed_key
    state.invalidate_if_changed(draft_key)
    if previous and not state.confirmed_key:
        st.warning("字段、单位或质量配置已变更，旧结果已失效。请重新确认。")
    if st.button("确认字段和单位，检查数据", type="primary", key="confirm_mapping"):
        state.invalidate()
        try:
            mapping = MappingConfig(fields=fields, time_unit=time_unit, quantity=quantity, unit=unit, optional_units=optional_units, confirmed=True, units_consistent=units_consistent)
            config = QualityConfig(min_interval_ratio=lower, max_interval_ratio=upper)
            prepared = prepare_experiment(parsed, mapping, SourceKind(state.source_metadata.get("source_kind", "unknown")))
            quality = check_quality(prepared, config)
            state.prepared, state.quality, state.confirmed_key = prepared, quality, draft_key
        except ValidationError as exc:
            st.error("确认失败：请完整映射必需字段、填写单位并勾选一致性确认。" + "；".join(error["msg"] for error in exc.errors(include_url=False)))
        except ValueError as exc:
            st.error(f"检查失败：{exc}")
    if state.prepared is None:
        st.info("映射尚未确认：不会计算质量结果或绘制曲线。")


def _render_quality_and_curves(state: LabSession) -> None:
    prepared, report = state.prepared, state.quality
    st.subheader("03 / 数据质量")
    duration = "无法确定" if report.duration_s is None else f"{report.duration_s:.6g} s"
    first, second, third = st.columns(3)
    first.metric("原始数据行数", f"{report.row_count:,}")
    second.metric("记录时长", duration)
    third.metric("问题记录数", f"{len(report.issues):,}")
    st.caption("时长按首尾时间差给出；时间无效或倒退时不可作为总时长。问题按行、字段和规则计数，同一行可能命中多项。")
    stats = report.interval_stats
    st.dataframe(pd.DataFrame([{
        "有效正间隔数": stats.get("count"), "最小间隔 (s)": stats.get("min_s"),
        "中位间隔 (s)": stats.get("median_s"), "最大间隔 (s)": stats.get("max_s"),
        "平均间隔 (s)": stats.get("mean_s"), "标准差 (s)": stats.get("std_s"),
    }]), hide_index=True, width="stretch")
    st.caption("间隔统计仅使用原顺序相邻两行的有限、正时间差，包含命中间隔阈值的正间隔；不跨缺失时间计算。没有可计算值时显示空值。")
    if report.issues:
        with st.expander("问题位置与检查原因", expanded=True):
            page_size = 100
            page_count = max(1, (len(report.issues) + page_size - 1) // page_size)
            page = st.number_input("问题列表页码（每页 100 条）", min_value=1, max_value=page_count, value=1, step=1, key=f"issues_page_{state.confirmed_key}")
            offset = (int(page) - 1) * page_size
            rows = [{"规则": item.code, "数据行": item.row, "CSV 起始物理行": item.csv_line, "字段": item.field, "原因": item.message} for item in report.issues[offset:offset + page_size]]
            st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
            st.caption(f"第 {offset + 1}–{min(offset + page_size, len(report.issues))} 条 / 共 {len(report.issues)} 条。数据行从 1 开始、不含表头；CSV 物理行含表头和引号内换行。疑似重置是启发式标记，需要核对采集日志。")
    if report.blocking:
        st.error("存在阻断性数据问题，禁止直接使用整份文件绘图。请选择一个连续有效区间；原始文件与其他区间仍完整保留。")
    else:
        st.success("当前规则未发现阻断性数据问题。这不代表设备一定正常。")
    intervals = report.valid_intervals
    if not intervals:
        st.warning("没有至少两个采样点的连续有效区间，无法绘图。请核对数据或检查规则。")
        return
    with st.sidebar:
        st.subheader("04 / 选择区间与基础曲线")
        choices = [None, *range(len(intervals))]
        default = 0 if report.blocking else 1
        selected = st.selectbox("选择连续有效区间", choices, index=default, key=f"valid_interval_{state.confirmed_key}", format_func=lambda i: "请明确选择一个区间" if i is None else f"数据行 {intervals[i].start_row}–{intervals[i].end_row} ｜ {intervals[i].start_s:.6g}–{intervals[i].end_s:.6g} s")
        if selected is None:
            state.invalidate_step()
            st.info("尚未选择有效区间，不显示跨越问题位置的曲线。")
            return
        bounds = intervals[selected]
        start_col, end_col = st.columns(2)
        start = start_col.number_input("起始数据行", min_value=bounds.start_row, max_value=bounds.end_row, value=bounds.start_row, key=f"start_{state.confirmed_key}_{selected}")
        end = end_col.number_input("结束数据行", min_value=bounds.start_row, max_value=bounds.end_row, value=bounds.end_row, key=f"end_{state.confirmed_key}_{selected}")
    interval_identity = (int(start), int(end))
    if state.metric_interval != interval_identity:
        state.invalidate_step()
        state.metric_interval = interval_identity
    try:
        selected_frame = select_interval(prepared, report, int(start), int(end))
    except ValueError as exc:
        st.error(str(exc))
        return
    start_s, end_s = float(selected_frame["time"].iloc[0]), float(selected_frame["time"].iloc[-1])
    st.info(f"实际使用：原始数据行 {start}–{end}（闭区间），时间 {start_s:.6g}–{end_s:.6g} s，共 {len(selected_frame):,} 行；区间时长 {end_s - start_s:.6g} s。")
    max_points = st.sidebar.number_input("每条曲线最多显示点数", min_value=100, max_value=200000, value=5000, step=100, key="display_points")
    st.caption(f"显示 {min(len(selected_frame), int(max_points)):,} / {len(selected_frame):,} 点。等索引抽点仅作用于显示副本，可能遗漏尖峰；质量检查仍使用全部原始行。缩放不会重新抽点，可提高显示上限。未排序、删行、填补、平滑或插值。")
    performance = render_performance(state, selected_frame)
    if performance is not None and performance.step is not None:
        render_save_experiment(state)
    st.subheader("07 / 曲线与指标标记")
    for index, (title, figure) in enumerate(build_charts(selected_frame, prepared.mapping, int(max_points), response=performance.response if performance else None)):
        st.plotly_chart(figure, width="stretch", key=f"curve_{index}", config={"scrollZoom": True, "displaylogo": False})
    state.traceability = {
        "experiment_id": prepared.experiment.experiment_id,
        "source": prepared.experiment.source.model_dump(mode="json"),
        "source_label": state.source_metadata.get("source_label", "用户声明来源"),
        "algorithm_version": IMPORT_VERSION, "configuration_fingerprint": state.confirmed_key,
        "parser": {"encoding": state.parsed.encoding, "delimiter": state.parsed.delimiter},
        "mapping": prepared.mapping.model_dump(mode="json"),
        "quality_rules": report.config.model_dump(),
        "used_interval": {"start_row": int(start), "end_row": int(end), "start_s": start_s, "end_s": end_s},
        "operations": [*prepared.operations, f"用户选择原始数据行闭区间 [{start}, {end}]，保留其他原始行", f"显示副本等索引抽点，上限 {max_points}，不影响检查数据"],
        "performance": performance.model_dump(mode="json") if performance else None,
    }
    with st.expander("本次数据区间、配置与转换记录"):
        st.json(state.traceability)


def _future_features() -> None:
    with st.expander("后续功能（本阶段尚未实现）"):
        render_unimplemented("频域及多变量指标、根因诊断、Word和PDF报告")
