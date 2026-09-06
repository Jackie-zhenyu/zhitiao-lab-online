"""会话实验快照、用户确认共同窗口与 A/B 描述性对比。"""

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from ui.components import sidebar_controls
from copy import deepcopy
from core.event_models import EventConfig
from pydantic import ValidationError

from core.comparison import capture_experiment, available_duration, compare_experiments
from core.comparison_models import ComparisonConfig, ExperimentConditions
from ui.components import render_page_header
from ui.performance import STATUS_LABELS
from ui.session import LabSession, fingerprint


@sidebar_controls
def render_save_experiment(state: LabSession) -> None:
    with st.expander("保存当前实验及确认阶跃，用于 A/B 对比", expanded=True):
        st.caption("保存当前会话内的独立快照，含原始字节、质量结果、当前评价配置与单次阶跃；后续改动工作实验不会悄悄修改快照。最多保留8份；下载项目包后可在新会话或重启后恢复。")
        name = st.text_input("实验快照名称", value=state.filename, key=f"snapshot_name_{state.revision}")
        if st.button("保存已确认实验", key="save_current_experiment"):
            try:
                conditions = ExperimentConditions.model_validate(state.source_metadata.get("conditions", {}))
                saved = capture_experiment(state.prepared, state.quality, state.performance, name, conditions)
                if len(state.saved_experiments) >= 8 and saved.snapshot_id not in state.saved_experiments:
                    st.error("当前会话已保留8份快照；请在对比页明确移除不需要的快照后再保存。")
                else:
                    state.saved_experiments[saved.snapshot_id] = saved
                    state.saved_event_configs[saved.snapshot_id] = deepcopy(state.evidence.config) if state.evidence else EventConfig()
                    state.comparison = None
                    state.comparison_key = ""
                    st.success(f"已保存：{saved.name}。可加载另一实验并同样确认、保存，再进入“实验对比”。")
            except (ValueError, ValidationError) as exc:
                st.error(f"未保存：{exc}")
        st.caption(f"当前会话已保留 {len(state.saved_experiments)} 份确认快照。")


def _condition_inputs(side: str, saved) -> ExperimentConditions:
    with st.expander(f"{side} · 实验条件（未知请留空）", expanded=True):
        labels = {"plant":"被控对象", "load":"负载", "sampling":"采样设置", "environment":"其他环境 / 噪声", "controller":"PI / PID 参数记录"}
        values = {}
        for key, label in labels.items():
            text = st.text_input(f"{side} · {label}", value=getattr(saved.conditions, key) or "", key=f"ab_condition_{side}_{saved.snapshot_id}_{key}")
            values[key] = text.strip() or None
        st.caption("这些是实验条件记录。未知或不同会给警告；即使记录一致也不会自动归因于 PID 参数。")
    return ExperimentConditions(**values)


def _plot_side(figure, saved, side, letter, *, error=False, max_points=5000):
    native = saved.prepared.frame.loc[side.start_row-1:side.end_row-1]
    display = native.iloc[np.linspace(0, len(native)-1, min(max_points, len(native)), dtype=int)].copy()
    fields = ["error"] if error else ["actual", "target"]
    color = "#137A65" if letter == "A" else "#BF6D25"
    for field in fields:
        figure.add_trace(go.Scatter(
            x=display.time.to_numpy()-side.step.t0, y=display[field].to_numpy(),
            name=f"{letter} · {'误差' if error else ('实测' if field == 'actual' else '目标')}",
            mode="lines", connectgaps=False,
            line={"color":color,"dash":"dot" if field == "target" else "solid","width":2},
            customdata=np.column_stack((display.index.to_numpy()+1, display.time.to_numpy())),
            hovertemplate="距阶跃 %{x:.6g} s<br>原始时间 %{customdata[1]:.6g} s<br>原始行 %{customdata[0]}<br>数值 %{y:.6g}<extra>%{fullData.name}</extra>",
        ))


def _comparison_charts(result, a, b):
    st.subheader("按阶跃时刻对齐的原始响应")
    st.caption("横轴为各自 t−t0；仅减去各自阶跃时刻，无对齐插值。每侧图形最多显示5000个原始点，全部指标仍使用原始有效样本。")
    if result.can_overlay:
        for error, title in ((False,"A/B 目标与实测"),(True,"A/B 跟踪误差")):
            fig = go.Figure()
            _plot_side(fig, a, result.a, "A", error=error)
            _plot_side(fig, b, result.b, "B", error=error)
            fig.add_vline(x=0, line_dash="dash", line_color="#758597")
            fig.update_layout(title=title, xaxis_title="相对于各自阶跃时刻 t−t0 [s]", yaxis_title=f"{'误差' if error else result.a.quantity} [{result.a.unit}]", xaxis_range=[0,result.config.common_duration_s], height=340, template="plotly_white", legend={"orientation":"h"})
            st.plotly_chart(fig, width="stretch", key=f"ab_chart_{error}", config={"scrollZoom":True,"displaylogo":False})
    else:
        st.warning("物理量或单位不同，按各自单位分别显示，不在同一物理纵轴叠加。")
        for saved, side, letter in ((a,result.a,"A"),(b,result.b,"B")):
            fig = go.Figure()
            _plot_side(fig, saved, side, letter)
            fig.update_layout(title=f"{letter} · {side.name}", xaxis_title="相对于阶跃时刻 [s]", yaxis_title=f"{side.quantity} [{side.unit}]", height=310, template="plotly_white")
            st.plotly_chart(fig, width="stretch", key=f"ab_separate_{letter}", config={"scrollZoom":True,"displaylogo":False})


def render_comparison_page() -> None:
    render_page_header("实验对比", "在用户确认的共同观察时长内，分别用原始时间戳计算并描述 A/B 差异。")
    state = st.session_state.setdefault("lab_session", LabSession())
    library = state.saved_experiments
    if not library:
        a, b = st.columns(2)
        a.selectbox("选择 A 实验", [], disabled=True, index=None, key="ab_empty_a")
        b.selectbox("选择 B 实验", [], disabled=True, index=None, key="ab_empty_b")
        st.button("开始对比", disabled=True, key="start_comparison")
        st.info("请先在“实验分析”导入实验、确认字段及单次阶跃，再保存实验快照。报告预览可导出HTML、JSON和指标CSV；Word和PDF尚未实现。")
        st.caption("示例下拉框已提供 PI A / PI B；分别加载、确认并保存即可比较。所有闭环示例均为“闭环仿真数据，非实测”。")
        return
    with st.sidebar:
        ids = list(library)
        left, right = st.columns(2)
        fmt = lambda key: f"{library[key].name} · t0={library[key].performance.step.t0:.6g} s · {key[-8:]}"
        a_id = left.selectbox("选择 A 实验", ids, format_func=fmt, key="ab_selected_a")
        b_id = right.selectbox("选择 B 实验", ids, index=min(1,len(ids)-1), format_func=fmt, key="ab_selected_b")
        a, b = library[a_id], library[b_id]
        left.caption(f"A 来源：{a.performance.source_label} · SHA-256 {a.prepared.parsed.sha256}")
        right.caption(f"B 来源：{b.performance.source_label} · SHA-256 {b.prepared.parsed.sha256}")
        for panel, saved in ((left, a), (right, b)):
            response = saved.performance
            step = response.step
            panel.caption(f"已确认阶跃原始行 {response.step_start_row}–{response.step_end_row}；t0={step.t0:.9g} s；r0={step.r0:.9g}、r1={step.r1:.9g}、y0={step.y0:.9g} [{response.mapping.unit}]。")
            panel.caption(f"初值来源：{'有效阶跃前基线' if step.y0_source == 'baseline' else '用户明确提供'}；在实验分析页选择不同观察区间后可另存快照。")
        with st.expander("管理当前会话快照"):
            remove_id = st.selectbox("选择待移除快照", ids, format_func=fmt, key="ab_remove_id")
            if st.button("移除所选快照", key="remove_saved_experiment"):
                del library[remove_id]
                state.saved_event_configs.pop(remove_id, None)
                state.comparison, state.comparison_key = None, ""
                # 只清理已经失效的选择键；原始工作实验不变。
                for key in ("ab_selected_a", "ab_selected_b", "ab_remove_id"):
                    if st.session_state.get(key) == remove_id:
                        del st.session_state[key]
                st.rerun()
        with left:
            conditions_a = _condition_inputs("A", a)
        with right:
            conditions_b = _condition_inputs("B", b)
        try:
            duration_a, duration_b = available_duration(a), available_duration(b)
        except ValueError as exc:
            state.comparison = None
            st.error(f"快照无法比较：{exc}")
            return
        pair_key = fingerprint({"a":a_id,"b":b_id})[:16]
        common_max = min(duration_a,duration_b)
        st.write(f"已确认阶跃的可用观察时长：A={duration_a:.9g} s，B={duration_b:.9g} s。")
        common = st.number_input("共同请求观察时长 [s]", min_value=min(1e-9,common_max), max_value=float(common_max), value=float(common_max), format="%.9g", key=f"ab_duration_{pair_key}")
        st.caption("默认取两侧可用时长的较小值。实际计算只截取原始点，不人工插入截止点；两侧实际边界在结果中列明。")
        window_key = fingerprint({"pair":pair_key,"duration":common})[:16]
        confirmed = st.checkbox("我确认采用该共同观察时长，并已核对两侧实验与阶跃区间", key=f"ab_confirmed_{window_key}")
        draft = fingerprint({"pair":pair_key,"duration":common,"conditions_a":conditions_a.model_dump(),"conditions_b":conditions_b.model_dump(),"confirmed":confirmed})
        if state.comparison_key != draft:
            state.comparison = None
        if st.button("计算 A/B 对比", key="run_ab_comparison", type="primary"):
            state.comparison = None
            try:
                config = ComparisonConfig(common_duration_s=float(common), confirmed=confirmed)
                state.comparison = compare_experiments(a,b,config,conditions_a,conditions_b)
                state.comparison_key = draft
            except (ValueError, ValidationError) as exc:
                st.error(f"对比未执行：请确认共同窗口并核对输入。{exc}")
    result = state.comparison
    if result is None:
        st.info("对比尚未确认或输入已变化；不会沿用旧结果。")
        return
    st.info(result.normalization)
    for warning in result.warnings:
        st.warning(warning)
    st.caption("这是指标的描述性对比：差值不等于 PID 修改的因果效果，不计算综合评分，也不宣布某组绝对更好。")
    st.subheader("可比性检查")
    check_names = {"quantity":"物理量", "unit":"单位", "step_reference":"目标与归一化初值", "metric_config":"评价参数", "plant":"被控对象", "load":"负载", "sampling":"采样设置", "environment":"其他环境", "controller":"控制器参数", "native_window":"实际原始采样窗口"}
    check_status = {"match":"一致", "mismatch":"不同", "unknown":"未知"}
    st.dataframe(pd.DataFrame([{"项目":check_names[c.name],"状态":check_status[c.status],"依据":c.reason} for c in result.checks]), hide_index=True, width="stretch")
    st.subheader("实际计算区间")
    st.dataframe(pd.DataFrame([{
        "实验":letter,"t0 [s]":side.step.t0,"原始起始行":side.start_row,"原始截止行":side.end_row,
        "原始开始 [s]":side.start_s,"原始截止 [s]":side.end_s,
        "相对开始 [s]":side.relative_start_s,"相对截止 [s]":side.relative_end_s,
    } for side,letter in ((result.a,"A"),(result.b,"B"))]), hide_index=True, width="stretch")
    st.subheader("指标变化（B − A）")
    st.caption("百分比=100×(B−A)/abs(A)。正负表示数值变化，不是提升评分；分母为0或结果不可用时留空。")
    st.dataframe(pd.DataFrame([{
        "指标":d.name,"A":d.a.value,"A单位":d.a.unit,"A状态":STATUS_LABELS[d.a.status.value],
        "B":d.b.value,"B单位":d.b.unit,"B状态":STATUS_LABELS[d.b.status.value],
        "差值 B−A":d.delta,"变化百分比":d.percent,"说明":d.reason,
    } for d in result.differences]), hide_index=True, width="stretch")
    with st.expander("完整对比证据、评价配置与来源"):
        st.json(result.model_dump(mode="json"))
    _comparison_charts(result,a,b)
