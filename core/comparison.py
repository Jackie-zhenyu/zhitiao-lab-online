"""按各自确认的阶跃时刻对齐原生采样点，重算并描述 A/B 指标变化。"""

from copy import deepcopy
import hashlib
import json
import math

from core.comparison_models import (
    COMPARISON_VERSION, ComparisonConfig, ComparisonResult, ComparisonSide,
    CompatibilityCheck, ExperimentConditions, MetricDifference, SavedExperiment,
)
from core.import_models import ParserOptions, PreparedExperiment, QualityReport
from core.metric_models import PerformanceResult
from core.metrics import STEP_KEYS, TRACKING_KEYS, compute_step, compute_tracking
from core.models import ResultStatus
from core.parser import parse_csv
from core.performance import analyze_performance
from core.quality import check_quality, prepare_experiment, select_interval


def _fingerprint(payload: dict) -> str:
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False, allow_nan=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _validate_context(
    prepared: PreparedExperiment, quality: QualityReport, performance: PerformanceResult,
) -> PerformanceResult:
    """核对不可变来源及已确认结果，拒绝来源、数据、配置或计算值混用。"""
    if not isinstance(prepared, PreparedExperiment) or not isinstance(quality, QualityReport):
        raise ValueError("必须提供同一实验的数据准备对象和质量报告")
    result = PerformanceResult.model_validate(performance)
    if result.step is None or result.response is None:
        raise ValueError("请先明确确认一个单次阶跃并计算响应指标，再保存实验")
    if set(result.response.metrics) != set(STEP_KEYS) or any(
        metric.status in (ResultStatus.INVALID_DATA, ResultStatus.NOT_APPLICABLE)
        for metric in result.response.metrics.values()
    ):
        raise ValueError("当前响应含不适用或无效数据，不能保存为已确认单次阶跃")
    if result.step_start_row is None or result.step_end_row is None:
        raise ValueError("缺少已确认阶跃的原始起止行")
    if prepared.experiment.source != result.source or prepared.experiment.experiment_id != result.experiment_id:
        raise ValueError("指标来源与当前实验不匹配，不能沿用旧结果")
    if prepared.mapping != result.mapping or quality.config != result.quality_rules:
        raise ValueError("字段、单位或质量配置已变化，请重新确认和计算")
    if (
        result.config.min_interval_ratio != quality.config.min_interval_ratio
        or result.config.max_interval_ratio != quality.config.max_interval_ratio
        or (
            result.config.reference_interval_s is not None
            and result.config.reference_interval_s != quality.interval_stats.get("median_s")
        )
    ):
        raise ValueError("指标使用的连续性参数不匹配当前质量检查")
    parsed = prepared.parsed
    actual_sha = hashlib.sha256(parsed.raw_bytes).hexdigest()
    if actual_sha != parsed.sha256 or actual_sha != result.source.sha256:
        raise ValueError("原始字节与来源摘要不一致，不能使用该实验快照")
    # 根据记录的格式重新核对有界原始字节；不信任可能被外部修改的可变 DataFrame。
    raw_parsed = parse_csv(
        parsed.raw_bytes, parsed.filename,
        ParserOptions(
            encoding=parsed.encoding, delimiter=parsed.delimiter,
            max_bytes=max(1, len(parsed.raw_bytes)), max_rows=max(1, len(parsed.frame)),
        ),
    )
    if not raw_parsed.frame.equals(parsed.frame) or raw_parsed.source_lines != parsed.source_lines:
        raise ValueError("原始字符串表或行位置已与来源字节不同，不能保存旧分析")
    canonical = prepare_experiment(parsed, prepared.mapping, prepared.experiment.source.kind)
    if (
        not canonical.frame.equals(prepared.frame)
        or canonical.experiment != prepared.experiment
        or canonical.operations != prepared.operations
    ):
        raise ValueError("准备数据或转换记录已变化，请从原始字节重新分析")
    refreshed_quality = check_quality(canonical, quality.config)
    if refreshed_quality != quality:
        raise ValueError("质量报告已失效或不匹配当前原始数据")
    fresh = analyze_performance(
        canonical, refreshed_quality, result.start_row, result.end_row, result.config,
        source_label=result.source_label, step=result.step,
        step_start_row=result.step_start_row, step_end_row=result.step_end_row,
    )
    if fresh.model_dump(mode="json") != result.model_dump(mode="json"):
        raise ValueError("指标区间、配置或数值与当前数据重算结果不一致，请重新确认")
    return result


def _snapshot_identity(name, prepared, quality, performance, conditions) -> str:
    return "snapshot-" + _fingerprint({
        "name": name, "source": performance.source.model_dump(mode="json"),
        "encoding": prepared.parsed.encoding, "delimiter": prepared.parsed.delimiter,
        "mapping": prepared.mapping.model_dump(mode="json"),
        "operations": prepared.operations,
        "quality": quality.config.model_dump(mode="json"),
        "performance": performance.model_dump(mode="json"),
        "conditions": conditions.model_dump(mode="json"), "version": COMPARISON_VERSION,
    })


def capture_experiment(
    prepared: PreparedExperiment, quality: QualityReport,
    performance: PerformanceResult, name: str,
    conditions: ExperimentConditions | None = None,
) -> SavedExperiment:
    """核验来源/质量/配置/结果后，保存全部对象的独立深副本。"""
    if not isinstance(name, str) or not name.strip():
        raise ValueError("请填写实验快照名称")
    title = name.strip()
    context = _validate_context(prepared, quality, performance)
    condition_values = ExperimentConditions.model_validate(conditions or ExperimentConditions())
    copied_prepared, copied_quality, copied_result, copied_conditions = deepcopy(
        (prepared, quality, context, condition_values)
    )
    snapshot_id = _snapshot_identity(title, copied_prepared, copied_quality, copied_result, copied_conditions)
    saved = SavedExperiment(snapshot_id, title, copied_prepared, copied_quality, copied_result, copied_conditions)
    _duration(saved)
    return saved


def _validate_saved(saved: SavedExperiment) -> None:
    if not isinstance(saved, SavedExperiment):
        raise ValueError("请选择已保存并确认单次阶跃的实验快照")
    _validate_context(saved.prepared, saved.quality, saved.performance)
    conditions = ExperimentConditions.model_validate(saved.conditions)
    expected = _snapshot_identity(saved.name, saved.prepared, saved.quality, saved.performance, conditions)
    if expected != saved.snapshot_id:
        raise ValueError("实验快照的来源、名称、配置、阶跃或条件已修改，请重新保存确认")


def _duration(saved: SavedExperiment) -> float:
    result = saved.performance
    if result.step is None or result.step_end_row is None or result.step_start_row is None:
        raise ValueError("快照缺少已确认阶跃或观察区间")
    frame = select_interval(saved.prepared, saved.quality, result.step_start_row, result.step_end_row)
    duration = float(frame["time"].iloc[-1]) - result.step.t0
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError("已确认阶跃没有有限的正观察时长")
    return duration


def available_duration(saved: SavedExperiment) -> float:
    """返回已确认观察截止时间减 t0，严格使用该快照的阶跃段。"""
    _validate_saved(saved)
    return _duration(saved)


def _make_side(saved: SavedExperiment, duration: float, conditions: ExperimentConditions) -> ComparisonSide:
    result = saved.performance
    frame = select_interval(saved.prepared, saved.quality, result.step_start_row, result.step_end_row)
    cutoff = result.step.t0 + duration
    # 请求恰为本侧全部可用时长时直接复用确认末点，避免 t0+(end-t0)
    # 的一次浮点舍入错误丢掉合法最后一行（例如 -.7 与 .1）。
    original_end = float(frame["time"].iloc[-1])
    if duration == original_end - result.step.t0:
        cutoff = original_end
    if not math.isfinite(cutoff):
        raise ValueError(f"实验 {saved.name} 的共同截止时间计算溢出")
    # 原生点截取：不为共同窗口插入时间点或修改原始数据。
    response_frame = frame.loc[frame["time"] <= cutoff].copy(deep=True)
    post = response_frame.loc[response_frame["time"] >= result.step.t0].copy(deep=True)
    if len(post) < 2:
        raise ValueError(f"实验 {saved.name} 在共同窗口内不足两个原始阶跃后采样点；请扩大允许范围内的观察时长")
    start_s, end_s = float(post["time"].iloc[0]), float(post["time"].iloc[-1])
    return ComparisonSide(
        snapshot_id=saved.snapshot_id, name=saved.name,
        source=result.source, source_label=result.source_label,
        quantity=result.mapping.quantity, unit=result.mapping.unit,
        step=result.step, config=result.config, conditions=conditions,
        step_start_row=result.step_start_row,
        start_row=int(post.index[0]) + 1, end_row=int(post.index[-1]) + 1,
        start_s=start_s, end_s=end_s,
        relative_start_s=start_s - result.step.t0, relative_end_s=end_s - result.step.t0,
        tracking=compute_tracking(post, result.config, result.mapping.unit),
        response=compute_step(response_frame, result.step, result.config, result.mapping.unit),
    )


def _unknown(value: str | None) -> bool:
    return value is None or value.strip().casefold() in {
        "unknown", "n/a", "na", "none", "未知", "不详", "未记录", "未填写", "未确认",
    }


def _compatibility(a: ComparisonSide, b: ComparisonSide):
    checks: list[CompatibilityCheck] = []
    warnings: list[str] = []

    def check(name, match, success_reason, failure_reason):
        checks.append(CompatibilityCheck(name=name, status="match" if match else "mismatch", reason=success_reason if match else failure_reason))
        if not match:
            warnings.append(failure_reason)

    check("quantity", a.quantity == b.quantity, "物理量名称一致。", "物理量名称不同；不计算指标差值或变化百分比，不作同轴叠图。")
    check("unit", a.unit == b.unit, "目标/实测单位一致。", "目标/实测单位不同；未隐式换算，不计算指标差值或变化百分比，不作同轴叠图。")
    reference_a = (a.step.r0, a.step.r1, a.step.y0)
    reference_b = (b.step.r0, b.step.r1, b.step.y0)
    check("step_reference", reference_a == reference_b, "r0、r1 与归一化初值 y0 一致。", "r0、r1 或归一化初值 y0 不一致；只显示各侧值，不计算差值或变化百分比。")
    config_a = a.config.model_dump(exclude={"reference_interval_s"})
    config_b = b.config.model_dump(exclude={"reference_interval_s"})
    check("metric_config", config_a == config_b, "用户评价参数一致；各自参考采样间隔作为数据属性保留。", "指标参数/评价口径不一致；不计算差值或变化百分比，不自动覆盖任一侧参数。")
    for key, label in (("plant", "被控对象"), ("load", "负载"), ("sampling", "采样设置"), ("environment", "其他环境"), ("controller", "控制器参数")):
        first, second = getattr(a.conditions, key), getattr(b.conditions, key)
        if _unknown(first) or _unknown(second):
            reason = f"{label}存在未记录/未知条件；本次只能作带条件限制的数值描述。"
            checks.append(CompatibilityCheck(name=key, status="unknown", reason=reason))
            warnings.append(reason)
        elif first != second:
            reason = f"{label}记录不同；不能把观察到的差异直接归因为 PID 修改。"
            if key == "controller":
                reason = "控制器参数记录不同，这是允许的比较条件；记录本身不构成 PID 因果归因。"
            checks.append(CompatibilityCheck(name=key, status="mismatch", reason=reason))
            warnings.append(reason)
        else:
            checks.append(CompatibilityCheck(name=key, status="match", reason=f"{label}记录相同；这不是独立控制条件的真实性验证。"))
    same_window = math.isclose(a.relative_start_s, b.relative_start_s, rel_tol=1e-12, abs_tol=1e-12) and math.isclose(a.relative_end_s, b.relative_end_s, rel_tol=1e-12, abs_tol=1e-12)
    check("native_window", same_window, "两侧原生采样点的实际相对首尾时间一致。", "两侧原生采样窗口实际首尾不完全相同；没有重采样或插值对齐，仅能作描述性对比。")
    meaningful = all(item.status == "match" for item in checks if item.name in {"quantity", "unit", "step_reference", "metric_config"})
    # 已知控制器参数可以不同；未记录控制器仍属于未知实验条件。
    descriptive = any(
        item.status != "match" and (item.name != "controller" or item.status == "unknown")
        for item in checks
    )
    return checks, warnings, meaningful, descriptive


def _difference(key, first, second, meaningful) -> MetricDifference:
    base = dict(key=key, name=first.name, a=first, b=second)
    if not meaningful or first.unit != second.unit:
        return MetricDifference(**base, reason="物理量、单位、阶跃参考或评价口径不一致；差值和变化百分比均不适用。")
    if first.status != ResultStatus.SUCCESS or second.status != ResultStatus.SUCCESS:
        return MetricDifference(**base, reason=f"A 状态为 {first.status.value}，B 状态为 {second.status.value}；任一值不可用时不计算差值/百分比。A：{first.reason} B：{second.reason}")
    delta = second.value - first.value
    if not math.isfinite(delta):
        return MetricDifference(**base, reason="B−A 数值溢出；差值和变化百分比均为空。")
    if first.value == 0:
        return MetricDifference(**base, delta=delta, reason="差值为 B−A；A 为 0，变化百分比没有有效分母，保持为空。")
    percent = (delta / abs(first.value)) * 100.0
    if not math.isfinite(percent):
        return MetricDifference(**base, delta=delta, reason="差值为 B−A；变化百分比运算溢出，保持为空。")
    return MetricDifference(**base, delta=delta, percent=percent, reason="差值=B−A；变化百分比=100×(B−A)/abs(A)。这是有符号数值变化，不是提升评分或绝对优胜结论。")


def compare_experiments(
    a: SavedExperiment, b: SavedExperiment, config: ComparisonConfig,
    conditions_a: ExperimentConditions | None = None,
    conditions_b: ExperimentConditions | None = None,
) -> ComparisonResult:
    """显式共同观察时长内截取原生点并重新计算，不复用截段前指标。"""
    cfg = ComparisonConfig.model_validate(config)
    _validate_saved(a)
    if b is not a:
        _validate_saved(b)
    durations = (_duration(a), _duration(b))
    if cfg.common_duration_s > min(durations):
        raise ValueError(f"共同请求观察时长不能超过任一已确认阶跃段；可用上限 {min(durations):.9g} s")
    first_conditions = ExperimentConditions.model_validate(conditions_a if conditions_a is not None else a.conditions)
    second_conditions = ExperimentConditions.model_validate(conditions_b if conditions_b is not None else b.conditions)
    first = _make_side(a, cfg.common_duration_s, first_conditions)
    second = _make_side(b, cfg.common_duration_s, second_conditions)
    checks, warnings, meaningful, descriptive = _compatibility(first, second)
    for label, side in (("A", first), ("B", second)):
        gap = cfg.common_duration_s - side.relative_end_s
        if not math.isclose(gap, 0.0, rel_tol=0.0, abs_tol=1e-12):
            warnings.append(f"{label} 原生采样末点距共同请求截止时间仍差 {gap:.9g} s；未插入对齐点，实际末点为 t−t0={side.relative_end_s:.9g} s。")
            descriptive = True
        if side.relative_start_s > 1e-12:
            warnings.append(f"{label} 一般跟踪指标从首个阶跃后原生点 t−t0={side.relative_start_s:.9g} s 开始；没有人为补入 t0 计算点。")
            descriptive = True
    warnings.append("所有差异只描述本次已记录数据；不能据此直接归因于 PID 修改或宣布某次实验绝对更优。")
    metrics_a = {**first.tracking.metrics, **first.response.metrics}
    metrics_b = {**second.tracking.metrics, **second.response.metrics}
    differences = [_difference(key, metrics_a[key], metrics_b[key], meaningful) for key in (*TRACKING_KEYS, *STEP_KEYS)]
    comparison_id = "comparison-" + _fingerprint({
        "a": a.snapshot_id, "b": b.snapshot_id, "config": cfg.model_dump(mode="json"),
        "conditions_a": first_conditions.model_dump(mode="json"),
        "conditions_b": second_conditions.model_dump(mode="json"), "version": COMPARISON_VERSION,
    })
    return ComparisonResult(
        comparison_id=comparison_id, a=first, b=second, config=cfg,
        checks=checks, differences=differences, warnings=warnings,
        descriptive_only=descriptive, can_overlay=first.quantity == second.quantity and first.unit == second.unit,
    )
