"""配置可解释现象规则、展示证据卡，并在独立原始数据视图聚焦。"""

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from ui.components import sidebar_controls
from pydantic import ValidationError

from core.event_models import EventConfig, OutputLimits
from core.events import detect_events
from ui.session import LabSession, fingerprint


RULE_LABELS = {
    "oscillation": "稳定目标附近持续波动", "control_limit": "控制输出接近限值",
    "measurement_change": "测量突变 / 变化率", "data_record": "数据记录异常",
}


def _observed_summary(event) -> str:
    """把已经算出的观测量说明为中文；不在界面重新计算规则。"""
    observed, threshold = event.observations, event.thresholds
    if event.category == "oscillation":
        unit = threshold["quantity_unit"]
        return (f"观测跨度 {observed['duration_s']:.6g} s，峰峰差 {observed['peak_to_peak']:.6g} {unit}，"
                f"死区交替 {observed['crossings']:.0f} 次，平均偏差 {observed['mean_error']:.6g} {unit}，"
                f"死区外时间占比 {observed['outside_time_fraction']:.2%}。"
                f"命中窗口 {observed['matched_window_count']:.0f} 个；阈值按各窗口判断，合并区间统计仅供核对。")
    if event.category == "control_limit":
        return (f"连续接近限值 {observed['duration_s']:.6g} s（要求至少 {threshold['min_duration_s']:.6g} s）；"
                f"输出范围 [{observed['control_min']:.6g}, {observed['control_max']:.6g}] {threshold['control_unit']}。"
                f"该有效片段接近此限值的时间占比 {observed['valid_interval_near_time_fraction']:.2%}。")
    if event.category == "measurement_change":
        rate, bound = observed["absolute_rate"], threshold["rate_threshold"]
        rate_text, bound_text = f"{rate:.6g}", f"{bound:.6g}"
        if rate_text == bound_text and rate != bound:
            rate_text, bound_text = f"{rate:.17g}", f"{bound:.17g}"
        return (f"相邻原始点相隔 {observed['delta_t_s']:.6g} s，跳变量 {observed['delta_actual']:.6g} {threshold['quantity_unit']}；"
                f"绝对变化率 {rate_text} {threshold['rate_unit']}，超过规则阈值 {bound_text} {threshold['rate_unit']}。")
    return f"原始数据第 {event.start_row} 行，CSV 第 {observed['csv_line']:.0f} 物理行，字段 {observed['field']}：{observed['original_message']}"


@sidebar_controls
def _controls(state: LabSession) -> EventConfig:
    # 字段/单位确认也会改变物理阈值含义，不能只按文件 revision 保留确认。
    # 删除旧控件值，避免用户切回旧映射时复活曾经勾选的限幅/变化率授权。
    context = f"{state.revision}:{state.confirmed_key}"
    previous = st.session_state.get("event_controls_context")
    if previous != context:
        for key in list(st.session_state):
            if key.startswith("event_cfg_"):
                del st.session_state[key]
        st.session_state["event_controls_context"] = context
        if previous is not None:
            st.caption("字段、单位或质量确认已变化：现象规则恢复默认值，请重新核对物理阈值与限幅。")
    prefix = f"event_cfg_{state.revision}_{state.confirmed_key[:16]}_"
    unit = state.prepared.mapping.unit
    with st.expander("现象规则参数 · 默认值需按实验对象核对"):
        a, b, c = st.columns(3)
        window = a.number_input("波动观察窗 [s]", min_value=0.01, value=2.0, key=prefix+"window")
        crossings = b.number_input("最少死区交替次数", min_value=2, value=4, step=1, key=prefix+"crossings")
        peak = c.number_input(f"最小实测峰峰差 [{unit}]", min_value=1e-12, value=0.05, format="%.6g", key=prefix+"peak")
        deadband = a.number_input(f"误差交替死区 [{unit}]", min_value=0.0, value=0.01, format="%.6g", key=prefix+"deadband")
        near = b.number_input(f"靠近目标的平均误差带 [{unit}]", min_value=1e-12, value=0.2, format="%.6g", key=prefix+"near")
        flat = c.number_input(f"稳定目标容差 [{unit}]", min_value=0.0, value=1e-6, format="%.9g", key=prefix+"flat")
        fraction = a.number_input("死区外最少时间占比", min_value=0.0, max_value=1.0, value=0.25, key=prefix+"fraction")
        near_fraction = b.number_input("接近限值带 / 上下限跨度", min_value=0.001, max_value=0.499, value=0.02, key=prefix+"control_band")
        duration = c.number_input("接近限值最短持续时间 [s]", min_value=0.001, value=0.5, key=prefix+"control_duration")
        has_control = "control" in state.prepared.frame
        supplied = state.source_metadata.get("output_limits", {})
        control_unit = state.prepared.mapping.optional_units.get("control", "未映射")
        a, b = st.columns(2)
        lower_text = a.text_input(f"控制输出下限 [{control_unit}]", value=str(supplied.get("lower", "")), key=prefix+"lower", disabled=not has_control)
        upper_text = b.text_input(f"控制输出上限 [{control_unit}]", value=str(supplied.get("upper", "")), key=prefix+"upper", disabled=not has_control)
        limits_confirmed = st.checkbox("我已核对控制输出单位及上下限", key=prefix+"limits_confirmed", disabled=not has_control)
        st.caption("限值未确认或没有 control 时不判断接近限幅。示例提供的限值也须确认；本阶段不确认积分饱和。")
        rate_text = st.text_input(f"合理物理变化率上限 [{unit}/s]（可留空）", key=prefix+"rate", placeholder="留空只产生统计突变候选")
        rate_confirmed = st.checkbox("我确认该变化率上限适用于本实验对象及单位", key=prefix+"rate_confirmed")
        a, b = st.columns(2)
        factor = a.number_input("统计变化率 MAD 倍数", min_value=0.01, value=6.0, key=prefix+"factor")
        jump = b.number_input(f"统计候选最小跳变量 [{unit}]", min_value=1e-12, value=0.05, format="%.6g", key=prefix+"jump")
        st.caption("时间占比按左端状态×实际相邻dt估计；持续时间使用原始时间差。统计突变候选不能直接认定为传感器故障。")
    limits = None
    if has_control and limits_confirmed:
        if not lower_text.strip() or not upper_text.strip():
            raise ValueError("确认限幅前必须完整填写上下限")
        limits = OutputLimits(lower=float(lower_text), upper=float(upper_text), confirmed=True)
    if rate_confirmed and not rate_text.strip():
        raise ValueError("确认物理变化率上限前必须明确填写有限正数")
    return EventConfig(
        oscillation_window_s=float(window), oscillation_min_crossings=int(crossings),
        oscillation_peak_to_peak=float(peak), oscillation_deadband=float(deadband),
        near_target_band=float(near), target_flat_tolerance=float(flat),
        oscillation_min_time_fraction=float(fraction), control_near_fraction=float(near_fraction),
        control_min_duration_s=float(duration), output_limits=limits,
        rate_limit=float(rate_text) if rate_text.strip() else None,
        rate_limit_confirmed=bool(rate_confirmed), statistical_rate_factor=float(factor),
        statistical_min_jump=float(jump),
    )


def _threshold_table(config: EventConfig, unit: str) -> pd.DataFrame:
    defaults = EventConfig().model_dump()
    labels = {
        "oscillation_window_s": "波动窗 / s", "oscillation_min_crossings": "交替次数",
        "oscillation_peak_to_peak": f"峰峰差 / {unit}", "oscillation_deadband": f"死区 / {unit}",
        "near_target_band": f"平均误差带 / {unit}", "target_flat_tolerance": f"目标容差 / {unit}",
        "oscillation_min_time_fraction": "死区外时间占比", "control_near_fraction": "限值接近带比例",
        "control_min_duration_s": "接近限值持续 / s", "output_limits": "用户确认输出上下限",
        "rate_limit": f"物理变化率上限 / {unit}/s", "rate_limit_confirmed": "物理上限已确认",
        "statistical_rate_factor": "统计 MAD 倍数", "statistical_min_jump": f"统计最小跳变 / {unit}",
    }
    return pd.DataFrame([{
        "参数": labels[key], "当前值": str(value) if value is not None else "未提供",
        "默认值": str(defaults[key]) if defaults[key] is not None else "未提供",
        "来源": "默认 / 与默认一致" if value == defaults[key] else "用户设置 / 确认",
    } for key, value in config.model_dump().items()])


def _focus_view(state: LabSession, event) -> None:
    st.write("**证据聚焦：原始数据视图**")
    st.caption("聚焦仅改变此图显示范围，不改变下方性能指标的分析区间。")
    st.caption("证据图每个连续片段最多抽取1000个原始点；缩放不重新抽点，可能漏显尖峰。此显示规则独立于下方主曲线点数设置，检测仍使用完整数据。")
    padding = max(2, (event.end_row - event.start_row + 1) // 10)
    first, last = max(1, event.start_row-padding), min(len(state.prepared.frame), event.end_row+padding)
    data_record = event.category == "data_record"
    fields = ["control"] if event.category == "control_limit" else ["target", "actual"]
    if "error" in event.charts and not data_record:
        fields = ["error"]
    chart = go.Figure()
    seen = set()
    for segment in state.quality.valid_intervals:
        lo, hi = max(first, segment.start_row), min(last, segment.end_row)
        if lo > hi:
            continue
        chunk = state.prepared.frame.loc[lo-1:hi-1]
        display = chunk.iloc[np.linspace(0, len(chunk)-1, min(1000, len(chunk)), dtype=int)]
        for field in fields:
            if field not in display:
                continue
            chart.add_trace(go.Scatter(
                x=(display.index.to_numpy()+1) if data_record else display.time.to_numpy(),
                y=display[field].to_numpy(), customdata=np.column_stack((display.index.to_numpy()+1, display.time.to_numpy())),
                mode="lines+markers", marker={"size":3}, connectgaps=False,
                name={"target":"目标值", "actual":"实测值", "control":"控制输出", "error":"误差"}[field],
                legendgroup=field, showlegend=field not in seen,
                hovertemplate="原始行 %{customdata[0]}<br>时间 %{customdata[1]:.6g} s<br>数值 %{y:.6g}<extra>%{fullData.name}</extra>",
            ))
            seen.add(field)
    x0, x1 = (event.start_row, event.end_row) if data_record else (event.start_s, event.end_s)
    if x0 is not None:
        if x0 == x1:
            chart.add_vline(x=x0, line_dash="dash", line_color="#BA6B37")
        else:
            chart.add_vrect(x0=x0, x1=x1, fillcolor="rgba(186,107,55,.15)", line_width=0)
    shown_unit = state.prepared.mapping.optional_units.get("control", "1") if fields == ["control"] else state.prepared.mapping.unit
    chart.update_layout(title=event.phenomenon, xaxis_title="原始数据行（时间异常时不强行时间对齐）" if data_record else "时间 [s]", yaxis_title=f"数值 [{shown_unit}]", height=330, template="plotly_white", margin={"l":30,"r":20,"t":45,"b":40})
    st.plotly_chart(chart, width="stretch", key=f"evidence_plot_{event.event_id}", config={"scrollZoom":True,"displaylogo":False})
    if data_record:
        preview = state.parsed.frame.iloc[first-1:min(last, first+99)].copy()
        preview.index += 1
        st.dataframe(preview, width="stretch")
        st.caption("图中只连接各连续有效片段，问题行以原始记录表核对；空图不表示没有问题。表格最多显示当前聚焦范围的前100行，原始数据未截断。")


def render_evidence(state: LabSession) -> None:
    st.subheader("异常现象与证据")
    st.caption("对整份日志的各连续有效片段执行规则，并列出全文件记录问题。只检测现象，不冒充根因诊断。")
    try:
        config = _controls(state)
        st.dataframe(_threshold_table(config, state.prepared.mapping.unit), hide_index=True, width="stretch")
        key = fingerprint({"experiment":state.prepared.experiment.experiment_id, "quality":state.quality.config.model_dump(), "rules":config.model_dump(), "version":"evidence-rules-v4.0"})
        if state.evidence_key != key or state.evidence is None:
            state.evidence = detect_events(state.prepared, state.quality, config)
            state.evidence_key = key
            state.focused_event_id = None
    except (ValueError, ValidationError) as exc:
        state.evidence, state.evidence_key, state.focused_event_id = None, "", None
        st.error(f"现象规则未执行：{exc}")
        return
    report = state.evidence
    st.write(f"检测到 {len(report.events)} 条现象证据。规则未发现异常，不等于证明系统完全正常。")
    with st.expander("规则适用性与未执行原因"):
        statuses = {"checked":"已检查", "not_applicable":"不适用", "insufficient_data":"数据不足", "invalid_data":"数据异常"}
        st.dataframe(pd.DataFrame([{"规则":RULE_LABELS[c.rule],"状态":statuses[c.status],"原因":c.reason} for c in report.checks]), hide_index=True, width="stretch")
    if not report.events:
        return
    page_count = max(1, (len(report.events)+9)//10)
    page = st.number_input("证据页码（每页10条）", min_value=1, max_value=page_count, value=1, step=1, key=f"evidence_page_{key}")
    if state.focused_event_id and st.button("取消证据图表聚焦", key="clear_evidence_focus"):
        state.focused_event_id = None
    for event in report.events[(int(page)-1)*10:int(page)*10]:
        with st.container(border=True):
            st.write(f"**检测到的现象：{event.phenomenon}**")
            timing = "时间无法可靠确定，按原始行定位" if event.start_s is None else f"{event.start_s:.6g}–{event.end_s:.6g} s"
            st.caption(f"{timing} · 原始数据行 {event.start_row}–{event.end_row} · {event.event_id}")
            st.write(f"尚不能确定的原因：{event.undetermined_causes}")
            st.write(f"检测规则：{RULE_LABELS[event.category]}（{event.rule}）")
            st.write(_observed_summary(event))
            with st.expander("阈值、观测量、关联图表与限制"):
                for limitation in event.limitations:
                    st.write(limitation)
                st.json(event.model_dump(mode="json"))
            if st.button("聚焦这条证据", key=f"focus_evidence_{event.event_id}"):
                state.focused_event_id = event.event_id
            if state.focused_event_id == event.event_id:
                _focus_view(state, event)
