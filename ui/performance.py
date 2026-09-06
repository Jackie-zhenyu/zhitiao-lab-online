"""指标配置、阶跃确认和结果解释；数值计算全部由 core 完成。"""

import pandas as pd
import streamlit as st
from ui.components import sidebar_controls
from pydantic import ValidationError

from core.metric_models import MetricConfig, NORMALIZATION, StepSelection
from core.models import ResultStatus
from core.performance import analyze_performance
from core.steps import discover_steps, estimate_baseline
from ui.session import LabSession, fingerprint


STATUS_LABELS = {
    "success": "计算成功", "not_applicable": "不适用",
    "not_reached": "观测内未达到", "insufficient_data": "数据不足",
    "invalid_data": "数据异常", "not_computable": "无法计算",
    "not_implemented": "尚未实现", "error": "计算错误",
}


@sidebar_controls
def _config_controls(state: LabSession) -> MetricConfig:
    with st.expander("指标参数 · 积分末段、响应阈值与基线"):
        left, middle, right = st.columns(3)
        tail = left.number_input("末段占总时间比例", min_value=0.01, max_value=1.0, value=0.1, step=0.05, key="metric_tail")
        lower = middle.number_input("上升下阈值 q", min_value=0.001, max_value=0.999, value=0.1, step=0.05, key="metric_rise_lower")
        upper = right.number_input("上升上阈值 q", min_value=0.001, max_value=0.999, value=0.9, step=0.05, key="metric_rise_upper")
        band = left.number_input("目标带相对幅值比例", min_value=0.0, max_value=0.99, value=0.02, step=0.01, format="%.4f", key="metric_band")
        absolute = middle.number_input(f"目标带绝对容差 [{state.prepared.mapping.unit}]", min_value=0.0, value=0.0, format="%.6g", key="metric_absolute")
        observation = right.number_input("最短后续观察时长 [s]", min_value=0.0, value=0.5, step=0.1, key="metric_observation")
        epsilon = left.number_input("近零幅值容差 [物理单位]", min_value=1e-15, value=1e-9, format="%.9g", key="metric_epsilon")
        flat = middle.number_input("目标平台容差 [物理单位]", min_value=0.0, value=1e-6, format="%.9g", key="metric_flat")
        baseline_range = right.number_input("基线实测峰峰差上限 [物理单位]", min_value=0.0, value=0.02, format="%.6g", key="metric_baseline_range")
        window = left.number_input("阶跃前基线窗口 [s]", min_value=0.001, value=0.5, step=0.1, key="metric_baseline_window")
        duration = middle.number_input("基线最短跨度 [s]", min_value=0.0, value=0.1, step=0.05, key="metric_baseline_duration")
        points = right.number_input("基线最少采样点", min_value=2, value=3, step=1, key="metric_baseline_points")
        st.caption("积分按实际时间梯形计算；末段按时间比例选取。响应带宽取相对带宽与绝对容差中的较大值。参数不会自动放宽；改变参数后须重新确认阶跃。")
    quality = state.quality
    return MetricConfig(
        tail_fraction=float(tail), rise_lower=float(lower), rise_upper=float(upper),
        relative_band=float(band), absolute_tolerance=float(absolute), min_observation_s=float(observation),
        amplitude_epsilon=float(epsilon), target_flat_tolerance=float(flat),
        baseline_range_tolerance=float(baseline_range), baseline_window_s=float(window),
        baseline_min_duration_s=float(duration), baseline_min_points=int(points),
        min_interval_ratio=quality.config.min_interval_ratio,
        max_interval_ratio=quality.config.max_interval_ratio,
        reference_interval_s=quality.interval_stats.get("median_s"),
    )


def _show_metrics(computation, result, *, response=False):
    metrics = computation.metrics
    columns = st.columns(len(metrics))
    rows = []
    for column, (key, metric) in zip(columns, metrics.items()):
        status = STATUS_LABELS.get(metric.status.value, metric.status.value)
        value = "—" if metric.value is None else f"{metric.value:.6g} {metric.unit}"
        column.metric(metric.name, value)
        column.caption(status)
        rows.append({"指标": metric.name, "数值": metric.value, "单位": metric.unit, "状态": status, "解释 / 原因": metric.reason})
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    for key, metric in metrics.items():
        with st.expander(f"指标解释 · {metric.name}"):
            st.write(result.definitions[key])
            st.write(f"状态：{STATUS_LABELS.get(metric.status.value, metric.status.value)}；{metric.reason or '依据本次所选数据计算。'}")
            if response:
                st.caption(NORMALIZATION)
            st.json({
                "result": metric.model_dump(mode="json"),
                "interval": result.interval.model_dump(),
                "original_rows": [result.start_row, result.end_row],
                "step_original_rows": [result.step_start_row, result.step_end_row] if response else None,
                "parameters": result.config.model_dump(),
                "step": result.step.model_dump() if response and result.step else None,
                "actual_computation_context": computation.details,
                "crossings_and_thresholds": computation.markers,
                "source": result.source.model_dump(mode="json"),
                "source_label": result.source_label,
                "algorithm_version": result.algorithm_version,
            })


@sidebar_controls
def _step_controls(state: LabSession, frame: pd.DataFrame, config: MetricConfig):
    st.subheader("06 / 单次阶跃确认")
    st.info(NORMALIZATION)
    candidates = discover_steps(frame, config)
    mode = st.radio("阶跃指定方式", ["候选阶跃", "手动指定"], horizontal=True, key="step_mode")
    frame_start, frame_end = int(frame.index[0]) + 1, int(frame.index[-1]) + 1
    selection_id = fingerprint({"confirmed": state.confirmed_key, "rows": [frame_start, frame_end], "config": config.model_dump()})[:16]
    if mode == "候选阶跃":
        if not candidates:
            state.invalidate_step()
            st.warning("未可靠识别单次阶跃平台。可缩小区间、核对参数，或切换“手动指定”；持续变化目标不适用阶跃指标。")
            return None, None
        st.caption("以下仅为候选。t0 取新目标平台首个采样点，真实触发可能位于它与上一点之间；下一阶跃前截断观察区间。")
        st.dataframe(pd.DataFrame([{"候选": i + 1, "t0 [s]": c.t0, "r0": c.r0, "r1": c.r1, "前平台起始行": c.pre_start_row, "阶跃行": c.step_row, "最晚观察行": c.end_row} for i, c in enumerate(candidates)]), hide_index=True, width="stretch")
        index = st.selectbox("选择候选阶跃（仍需确认）", range(len(candidates)), format_func=lambda i: f"候选 {i + 1} · t0={candidates[i].t0:.6g} s · {candidates[i].r0:.6g} → {candidates[i].r1:.6g}", key=f"step_candidate_{selection_id}")
        candidate = candidates[index]
        step_start, t0, r0, r1 = candidate.pre_start_row, candidate.t0, candidate.r0, candidate.r1
        step_end = int(st.number_input("阶跃观察截止数据行", min_value=candidate.step_row, max_value=candidate.end_row, value=candidate.end_row, step=1, key=f"step_end_{selection_id}_{index}"))
        st.caption(f"候选触发括区：({candidate.previous_sample_s:.6g}, {t0:.6g}] s；确认的 t0={t0:.6g} s，r0={r0:.6g}，r1={r1:.6g}。")
    else:
        left, right = st.columns(2)
        step_start = int(left.number_input("阶跃段起始数据行（含基线）", min_value=frame_start, max_value=frame_end - 1, value=frame_start, step=1, key=f"step_start_manual_{selection_id}"))
        step_end = int(right.number_input("阶跃观察截止数据行", min_value=frame_start + 1, max_value=frame_end, value=frame_end, step=1, key=f"step_end_manual_{selection_id}"))
        left, middle, right = st.columns(3)
        t0 = left.number_input("手动阶跃时刻 t0 [s]", min_value=float(frame.time.iloc[0]), max_value=float(frame.time.iloc[-1]), value=float(frame.time.iloc[0]), format="%.9g", key=f"step_t0_{selection_id}")
        r0 = middle.number_input("阶跃前目标 r0 [物理单位]", value=float(frame.target.iloc[0]), format="%.9g", key=f"step_r0_{selection_id}")
        r1 = right.number_input("阶跃后目标 r1 [物理单位]", value=float(frame.target.iloc[-1]), format="%.9g", key=f"step_r1_{selection_id}")
        st.caption("手动数值必须由你核对；记录前已发生的阶跃不外推。阶跃前后目标仍须通过平台检查，多次阶跃须分别选段。")
    if step_start >= step_end:
        state.invalidate_step()
        st.error("阶跃片段至少需要两行，且起始行必须早于截止行。")
        return None, None
    step_frame = frame.loc[step_start - 1:step_end - 1]
    baseline = estimate_baseline(step_frame, float(t0), float(r0), config)
    st.write(f"基线检查：{baseline.reason}")
    y0_mode = st.radio("响应初值 y0 来源", ["使用有效基线估计", "手动提供 y0"], horizontal=True, key=f"step_y0_mode_{selection_id}")
    y0 = None
    manual_text = ""
    if y0_mode == "使用有效基线估计":
        if baseline.status == ResultStatus.SUCCESS:
            y0 = baseline.value
            st.caption(f"y0={y0:.9g} {state.prepared.mapping.unit}；基线数据行 {baseline.start_row}–{baseline.end_row}，{baseline.start_s:.6g}–{baseline.end_s:.6g} s，按时间加权估计。")
        else:
            st.warning("没有可用的阶跃前基线。请选择“手动提供 y0”并明确填写，或者调整片段。")
    else:
        manual_text = st.text_input("明确填写响应初值 y0 [物理单位]", placeholder="不得留空；不自动假定为 0", key=f"step_y0_manual_{selection_id}")
        if manual_text.strip():
            try:
                y0 = float(manual_text)
            except ValueError:
                pass
    acknowledged = st.checkbox("我已核对本片段仅包含一次阶跃、t0、r0/r1 及 y0 的来源", key=f"step_ack_{selection_id}")
    draft_key = fingerprint({
        "selection": selection_id, "mode": mode, "rows": [step_start, step_end],
        "t0": float(t0), "r0": float(r0), "r1": float(r1),
        "y0_mode": y0_mode, "manual_text": manual_text,
        "baseline": baseline.model_dump(mode="json"), "acknowledged": acknowledged,
    })
    if state.step_key and state.step_key != draft_key:
        state.invalidate_step()
        st.warning("阶跃片段、参数或初值已改变，旧响应指标已撤销，请重新确认。")
    if st.button("确认单次阶跃并计算响应指标", key="confirm_step", type="primary"):
        state.invalidate_step()
        if not acknowledged or y0 is None:
            st.error("请先核对并勾选确认，且提供有效基线或明确填写 y0。")
        else:
            try:
                state.step_selection = StepSelection(
                    t0=float(t0), r0=float(r0), r1=float(r1), y0=y0,
                    y0_source="baseline" if y0_mode == "使用有效基线估计" else "manual",
                    baseline_start_s=baseline.start_s if y0_mode == "使用有效基线估计" else None,
                    baseline_end_s=baseline.end_s if y0_mode == "使用有效基线估计" else None,
                    baseline_start_row=baseline.start_row if y0_mode == "使用有效基线估计" else None,
                    baseline_end_row=baseline.end_row if y0_mode == "使用有效基线估计" else None,
                    baseline_method=baseline.reason if y0_mode == "使用有效基线估计" else None,
                    confirmed=True,
                )
                state.step_key = draft_key
            except ValidationError:
                st.error("t0、目标及 y0 必须是有限数值，不能使用 NaN 或无穷。")
    if state.step_selection is None:
        st.caption("阶跃尚未确认：仅显示一般跟踪指标，暂不产生响应指标。")
    return step_start, step_end


def render_performance(state: LabSession, frame: pd.DataFrame):
    st.subheader("05 / 控制性能指标")
    st.caption("一般跟踪指标使用上方完整选段；单次阶跃使用下方确认的独立片段。所有指标由程序计算，不调用模型。")
    try:
        config = _config_controls(state)
    except ValidationError as exc:
        state.invalidate_step()
        st.error("指标参数无效：" + "；".join(e["msg"] for e in exc.errors(include_url=False)))
        return None
    start, end = int(frame.index[0]) + 1, int(frame.index[-1]) + 1
    source_label = state.source_metadata.get("source_label", "用户声明来源，未经真实性验证")
    try:
        result = analyze_performance(state.prepared, state.quality, start, end, config, source_label=source_label)
        _show_metrics(result.tracking, result)
        step_start, step_end = _step_controls(state, frame, config)
        if state.step_selection is not None:
            result = analyze_performance(
                state.prepared, state.quality, start, end, config, source_label=source_label,
                step=state.step_selection, step_start_row=step_start, step_end_row=step_end,
            )
            st.info(f"响应实际使用：原始数据行 {step_start}–{step_end}；t0={result.step.t0:.6g} s。时间型响应指标相对于 t0。")
            st.caption("目标调节时间只说明在本次观测区间内满足；不能保证未来不再离开目标带。")
            _show_metrics(result.response, result, response=True)
        state.performance = result
        with st.expander("完整指标结果 · 来源、区间、参数和算法版本"):
            st.json(result.model_dump(mode="json"))
        return result
    except ValueError as exc:
        state.invalidate_step()
        st.error(f"指标计算拒绝当前输入：{exc}")
        return None
