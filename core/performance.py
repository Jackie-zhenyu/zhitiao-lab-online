"""从已确认来源/连续区间构建完整指标结果，不调用页面或模型。"""

from core.import_models import PreparedExperiment, QualityReport
from core.metric_models import MetricConfig, PerformanceResult, StepSelection
from core.metrics import compute_step, compute_tracking
from core.models import AnalysisInterval
from core.quality import select_interval


DEFINITIONS = {
    "rmse_t": "时间加权 RMSE = sqrt(∫(target-actual)²dt / (tb-ta))；对平方误差样本按实际时间梯形积分。",
    "iae": "IAE = ∫abs(target-actual)dt；对绝对误差样本按实际时间梯形积分。",
    "max_abs_error": "最大绝对误差 = max(abs(target-actual))，仅表示所选区间采样点中的最大值。",
    "tail_bias": "末段平均偏差 = 末段时间窗口内 ∫(target-actual)dt / 窗口时长；不是稳态误差，正值表示实测低于目标。",
    "rise_time": "相对于目标值归一化 q=(actual-y0)/(r1-y0)；首次达到下阈值到随后首次达到上阈值的时间差，交点线性插值。",
    "overshoot_pct": "相对于目标值归一化超调 = max(0,max(q)-1)×100%；不从实测末段估计归一化终值。",
    "settling_time": "目标误差带 abs(actual-r1)≤max(relative_band×abs(r1-y0), absolute_tolerance)；最终连续入带时刻减t0，且末尾后续观察时长达标。只在本次观测区间内满足，不保证未来。",
}


def analyze_performance(
    prepared: PreparedExperiment, quality: QualityReport,
    start_row: int, end_row: int, config: MetricConfig,
    *, source_label: str, step: StepSelection | None = None,
    step_start_row: int | None = None, step_end_row: int | None = None,
) -> PerformanceResult:
    """每次用当前区间和配置重新计算；选段质量校验不能由调用者跳过。"""
    frame = select_interval(prepared, quality, start_row, end_row)
    response = None
    if step is not None:
        if step_start_row is None or step_end_row is None:
            raise ValueError("阶跃计算必须明确原始起止行")
        if not start_row <= step_start_row < step_end_row <= end_row:
            raise ValueError("阶跃片段必须包含在当前连续分析区间内")
        step_frame = select_interval(prepared, quality, step_start_row, step_end_row)
        response = compute_step(step_frame, step, config, prepared.mapping.unit)
    return PerformanceResult(
        experiment_id=prepared.experiment.experiment_id,
        source=prepared.experiment.source, source_label=source_label,
        interval=AnalysisInterval(start_s=float(frame.time.iloc[0]), end_s=float(frame.time.iloc[-1])),
        start_row=start_row, end_row=end_row, mapping=prepared.mapping,
        quality_rules=quality.config, config=config, definitions=DEFINITIONS,
        step=step, step_start_row=step_start_row, step_end_row=step_end_row,
        tracking=compute_tracking(frame, config, prepared.mapping.unit), response=response,
    )
