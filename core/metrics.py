"""第三阶段确定性指标；计算约定见 docs/METRICS.md。

仅使用调用方明确选择的全量连续原始行。线性求值只用于积分边界和阈值
交点；本模块不排序、清洗、平滑、跨断点插值，不依赖 Streamlit 或模型。
"""

import math

import numpy as np
import pandas as pd
from pydantic import ValidationError

from core.metric_models import (
    METRICS_VERSION,
    NORMALIZATION,
    MetricComputation,
    MetricConfig,
    StepSelection,
)
from core.models import MetricResult, ResultStatus


TRACKING_KEYS = ("rmse_t", "iae", "max_abs_error", "tail_bias")
STEP_KEYS = ("rise_time", "overshoot_pct", "settling_time")
METRIC_NAMES = {
    "rmse_t": "时间加权 RMSE",
    "iae": "IAE",
    "max_abs_error": "最大绝对误差",
    "tail_bias": "末段平均偏差",
    "rise_time": "目标归一化上升时间",
    "overshoot_pct": "目标归一化超调量",
    "settling_time": "目标调节时间",
}


def _outcome(
    key: str, unit: str | None, status: ResultStatus,
    reason: str, value: float | None = None,
) -> MetricResult:
    if value is not None and not math.isfinite(value):
        status, value, reason = ResultStatus.INVALID_DATA, None, "数值运算溢出或产生非有限结果。"
    return MetricResult(name=METRIC_NAMES[key], status=status, value=value, unit=unit, reason=reason)


def _failed(
    keys: tuple[str, ...], units: dict[str, str | None],
    status: ResultStatus, reason: str,
    details: dict | None = None,
) -> MetricComputation:
    return MetricComputation(
        metrics={key: _outcome(key, units[key], status, reason) for key in keys},
        details=details or {"algorithm_version": METRICS_VERSION},
    )


def _validate_frame(
    frame: pd.DataFrame, config: MetricConfig,
) -> tuple[ResultStatus | None, str | None, np.ndarray | None]:
    if not isinstance(frame, pd.DataFrame):
        return ResultStatus.INVALID_DATA, "输入必须为规范字段数据表。", None
    if len(frame) < 2:
        return ResultStatus.INSUFFICIENT_DATA, "连续分析区间至少需要两个数据点。", None
    if not {"time", "target", "actual"} <= set(frame.columns) or frame.columns.has_duplicates:
        return ResultStatus.INVALID_DATA, "缺少 time、target、actual 必需字段或存在重复列名。", None
    if not pd.api.types.is_integer_dtype(frame.index.dtype):
        return ResultStatus.INVALID_DATA, "必须保留原始行的连续整数索引。", None
    indices = frame.index.to_numpy(dtype=object)
    if np.any(indices < 0) or np.any(np.diff(indices) != 1):
        return ResultStatus.INVALID_DATA, "原始数据行不连续；禁止跨越质量断点计算。", None
    if any(pd.api.types.is_complex_dtype(dtype) for dtype in frame.dtypes):
        return ResultStatus.INVALID_DATA, "实值信号中不能包含复数；不会丢弃虚部。", None
    try:
        values = frame.to_numpy(dtype=np.float64)
    except (ValueError, TypeError, OverflowError):
        return ResultStatus.INVALID_DATA, "已映射字段中存在非数值。", None
    if not np.isfinite(values).all():
        return ResultStatus.INVALID_DATA, "已映射字段存在缺失值、无穷值或非有限值。", None
    time = frame["time"].to_numpy(dtype=np.float64)
    with np.errstate(over="ignore", invalid="ignore", divide="ignore"):
        differences = np.diff(time)
        duration = time[-1] - time[0]
    if not np.all(np.isfinite(differences) & (differences > 0)) or not np.isfinite(duration):
        return ResultStatus.INVALID_DATA, "时间必须严格递增且时间差必须有限。", None
    if duration <= 0:
        return ResultStatus.INVALID_DATA, "分析区间没有正的有效时间跨度。", None
    reference = config.reference_interval_s
    if reference is None:
        ordered = np.sort(differences)
        midpoint = len(ordered) // 2
        reference = float(ordered[midpoint])
        if len(ordered) % 2 == 0:
            low = float(ordered[midpoint - 1])
            reference = low + (reference - low) / 2
    with np.errstate(over="ignore", invalid="ignore", divide="ignore"):
        ratios = differences / reference
    if np.any((ratios < config.min_interval_ratio) | (ratios > config.max_interval_ratio)):
        return ResultStatus.INVALID_DATA, "采样间隔违反已确认的连续性检查规则；禁止跨越异常间隔。", None
    return None, None, time


def _integral(time: np.ndarray, values: np.ndarray) -> float:
    # 先对被积样本做变换，再使用实际 dt 的复合梯形；不假设等间隔。
    with np.errstate(over="ignore", invalid="ignore", under="ignore"):
        areas = (values[:-1] / 2 + values[1:] / 2) * np.diff(time)
        return float(np.sum(areas))


def _from_boundary(
    time: np.ndarray, values: np.ndarray, boundary: float,
) -> tuple[np.ndarray, np.ndarray]:
    """给定范围内起点的相邻有效样本线性边界值；不外推或改动输入。"""
    index = int(np.searchsorted(time, boundary, side="left"))
    if index < len(time) and time[index] == boundary:
        return time[index:], values[index:]
    weight = (boundary - time[index - 1]) / (time[index] - time[index - 1])
    with np.errstate(over="ignore", invalid="ignore"):
        interpolated = (1 - weight) * values[index - 1] + weight * values[index]
    return (
        np.concatenate(([boundary], time[index:])),
        np.concatenate(([interpolated], values[index:])),
    )


def _base_details(frame: pd.DataFrame, time: np.ndarray) -> dict:
    return {
        "algorithm_version": METRICS_VERSION,
        "interval_start_s": float(time[0]),
        "interval_end_s": float(time[-1]),
        "duration_s": float(time[-1] - time[0]),
        "start_row": float(frame.index[0] + 1),
        "end_row": float(frame.index[-1] + 1),
        "sample_count": float(len(frame)),
    }


def compute_tracking(
    frame: pd.DataFrame, config: MetricConfig | None = None, unit: str = "1",
) -> MetricComputation:
    """时间加权 RMSE、IAE、最大绝对误差与按时间定义的末段平均偏差。"""
    valid_unit = unit.strip() if isinstance(unit, str) and unit.strip() else None
    units = {key: valid_unit for key in TRACKING_KEYS}
    units["iae"] = f"{valid_unit}·s" if valid_unit else None
    try:
        cfg = MetricConfig.model_validate(config if config is not None else MetricConfig())
    except (ValidationError, ValueError, TypeError):
        return _failed(TRACKING_KEYS, units, ResultStatus.INVALID_DATA, "指标配置无效。")
    if valid_unit is None:
        return _failed(TRACKING_KEYS, units, ResultStatus.INVALID_DATA, "必须明确填写目标值与实测值共同单位。")
    status, reason, time = _validate_frame(frame, cfg)
    if status is not None:
        return _failed(TRACKING_KEYS, units, status, reason)
    details = _base_details(frame, time)
    details["integration_method"] = "实际时间戳复合梯形；先构造 e² 或 |e| 的被积样本。"
    details["tail_definition"] = "按末段时间跨度加权平均偏差；不称为稳态误差。"
    details["tail_fraction"] = cfg.tail_fraction
    target = frame["target"].to_numpy(dtype=np.float64)
    actual = frame["actual"].to_numpy(dtype=np.float64)
    with np.errstate(over="ignore", invalid="ignore"):
        error = target - actual
    if not np.isfinite(error).all():
        return _failed(TRACKING_KEYS, units, ResultStatus.INVALID_DATA, "目标与实测相减溢出，误差不是有限值。", details)
    duration = float(time[-1] - time[0])
    with np.errstate(over="ignore", invalid="ignore", divide="ignore"):
        squared = error * error
        rmse = float(np.sqrt(_integral(time, squared) / duration))
        iae = _integral(time, np.abs(error))
    maximum = float(np.max(np.abs(error)))
    metrics = {
        "rmse_t": _outcome("rmse_t", valid_unit, ResultStatus.SUCCESS, "按实际时间对误差平方样本做梯形积分后归一化。", rmse),
        "iae": _outcome("iae", units["iae"], ResultStatus.SUCCESS, "按实际时间对绝对误差样本做梯形积分。", iae),
        "max_abs_error": _outcome("max_abs_error", valid_unit, ResultStatus.SUCCESS, "全量选段内采样点的最大绝对误差；不推断样本之间峰值。", maximum),
    }
    tail_start = float(time[-1] - cfg.tail_fraction * duration)
    tail_duration = float(time[-1] - tail_start)
    details.update(tail_start_s=tail_start, tail_end_s=float(time[-1]), tail_duration_s=tail_duration)
    if tail_duration <= 0:
        metrics["tail_bias"] = _outcome("tail_bias", valid_unit, ResultStatus.INVALID_DATA, "末段窗口小于当前时间表示精度，无法形成正时间跨度。")
    else:
        tail_time, tail_error = _from_boundary(time, error, tail_start)
        with np.errstate(over="ignore", invalid="ignore", divide="ignore"):
            bias = _integral(tail_time, tail_error) / tail_duration
        metrics["tail_bias"] = _outcome("tail_bias", valid_unit, ResultStatus.SUCCESS, "末段按时间加权的平均 target-actual；正值表示实测低于目标，未判断稳定性。", bias)
    return MetricComputation(metrics=metrics, details=details)


def _crossing_time(
    left_time: float, right_time: float,
    left_value: float, right_value: float, threshold: float,
) -> float:
    """有限且被相邻采样夹住的交点；中间差值溢出不能被 1/inf 掩盖成零。"""
    with np.errstate(over="ignore", invalid="ignore", divide="ignore"):
        numerator = np.float64(threshold) - np.float64(left_value)
        denominator = np.float64(right_value) - np.float64(left_value)
        duration = np.float64(right_time) - np.float64(left_time)
    if not np.isfinite([numerator, denominator, duration]).all() or denominator == 0:
        return float("nan")
    with np.errstate(over="ignore", invalid="ignore", divide="ignore"):
        weight = numerator / denominator
        crossing = np.float64(left_time) + weight * duration
    if not np.isfinite(weight) or not 0 <= weight <= 1 or not np.isfinite(crossing):
        return float("nan")
    return float(crossing)


def _first_crossing(time: np.ndarray, values: np.ndarray, threshold: float) -> float | None:
    reached = np.flatnonzero(values >= threshold)
    if not len(reached):
        return None
    index = int(reached[0])
    if index == 0 or values[index] == threshold:
        return float(time[index])
    return _crossing_time(
        time[index - 1], time[index], values[index - 1], values[index], threshold,
    )


def compute_step(
    frame: pd.DataFrame, step: StepSelection,
    config: MetricConfig | None = None, unit: str = "1",
) -> MetricComputation:
    """用户确认的单次阶跃：相对于目标值归一化的上升、超调及调节时间。"""
    units = {"rise_time": "s", "overshoot_pct": "%", "settling_time": "s"}
    try:
        cfg = MetricConfig.model_validate(config if config is not None else MetricConfig())
        selection = StepSelection.model_validate(step)
    except (ValidationError, ValueError, TypeError):
        return _failed(STEP_KEYS, units, ResultStatus.INVALID_DATA, "阶跃确认或指标配置无效。")
    if not isinstance(unit, str) or not unit.strip():
        return _failed(STEP_KEYS, units, ResultStatus.INVALID_DATA, "必须明确填写目标与实测的共同单位。")
    status, reason, time = _validate_frame(frame, cfg)
    if status is not None:
        return _failed(STEP_KEYS, units, status, reason)
    details = _base_details(frame, time)
    details.update(
        normalization=NORMALIZATION, target_unit=unit.strip(),
        t0=selection.t0, r0=selection.r0, r1=selection.r1, y0=selection.y0,
        y0_source=selection.y0_source,
        baseline_start_s=selection.baseline_start_s,
        baseline_end_s=selection.baseline_end_s,
        baseline_start_row=selection.baseline_start_row,
        baseline_end_row=selection.baseline_end_row,
        baseline_method=selection.baseline_method,
        required_observation_s=cfg.min_observation_s,
        settled_observation_s=None,
        observation_start_s=None,
        observation_end_s=None,
        observation_duration_s=None,
        crossing_method="仅在相邻有效样本之间线性计算交点；不保证样本之间没有未观测波动。",
    )
    if selection.t0 < time[0] or selection.t0 > time[-1]:
        return _failed(STEP_KEYS, units, ResultStatus.INVALID_DATA, "阶跃 t0 必须位于当前连续选段内；不外推。", details)
    target = frame["target"].to_numpy(dtype=np.float64)
    actual = frame["actual"].to_numpy(dtype=np.float64)
    pre = time < selection.t0
    with np.errstate(over="ignore", invalid="ignore"):
        pre_deviation = np.abs(target[pre] - selection.r0)
        post_deviation = np.abs(target[~pre] - selection.r1)
        target_jump = float(np.float64(selection.r1) - np.float64(selection.r0))
        denominator = float(np.float64(selection.r1) - np.float64(selection.y0))
    if not (
        np.isfinite(pre_deviation).all() and np.isfinite(post_deviation).all()
        and math.isfinite(target_jump) and math.isfinite(denominator)
    ):
        return _failed(STEP_KEYS, units, ResultStatus.INVALID_DATA, "目标平台差值或归一化幅值运算溢出。", details)
    if np.any(pre_deviation > cfg.target_flat_tolerance) or np.any(post_deviation > cfg.target_flat_tolerance):
        return _failed(STEP_KEYS, units, ResultStatus.NOT_APPLICABLE, "选段不是符合所声明 r0/r1 容差的单次阶跃平台；不能混合多阶跃或持续变化目标。", details)
    if abs(target_jump) <= cfg.amplitude_epsilon or abs(denominator) <= cfg.amplitude_epsilon:
        return _failed(STEP_KEYS, units, ResultStatus.NOT_APPLICABLE, "目标阶跃或目标相对初值的幅值接近零，不适用响应归一化。", details)
    observed_time, observed_actual = _from_boundary(time, actual, selection.t0)
    observation_duration = float(observed_time[-1] - selection.t0)
    details.update(
        observation_start_s=selection.t0,
        observation_end_s=float(observed_time[-1]),
        observation_duration_s=observation_duration,
        normalization_amplitude=abs(denominator),
        normalization_signed_amplitude=denominator,
        target_step_amplitude=abs(target_jump),
    )
    if len(observed_time) < 2 or observation_duration <= 0:
        return _failed(STEP_KEYS, units, ResultStatus.INSUFFICIENT_DATA, "阶跃后没有两个观测点或正的观察时间跨度。", details)
    with np.errstate(over="ignore", invalid="ignore", divide="ignore"):
        normalized = (observed_actual - selection.y0) / denominator
    if not np.isfinite(normalized).all():
        return _failed(STEP_KEYS, units, ResultStatus.INVALID_DATA, "响应归一化运算溢出或产生非有限值。", details)
    amplitude = abs(denominator)
    band_width = max(cfg.relative_band * amplitude, cfg.absolute_tolerance)
    y_lower = selection.y0 + cfg.rise_lower * denominator
    y_upper = selection.y0 + cfg.rise_upper * denominator
    markers = {"t0": selection.t0, "y_lower": y_lower, "y_upper": y_upper}
    details.update(
        rise_lower=cfg.rise_lower, rise_upper=cfg.rise_upper,
        band_width=band_width, relative_band=cfg.relative_band,
        absolute_tolerance=cfg.absolute_tolerance,
        maximum_normalized_response=float(np.max(normalized)),
    )
    metrics: dict[str, MetricResult] = {}

    if normalized[0] > cfg.rise_lower:
        metrics["rise_time"] = _outcome("rise_time", "s", ResultStatus.INSUFFICIENT_DATA, "观察起点已超过低阈值，缺少第一次低阈值交点证据。")
    else:
        t_lower = _first_crossing(observed_time, normalized, cfg.rise_lower)
        t_upper = _first_crossing(observed_time, normalized, cfg.rise_upper)
        if t_lower is not None and math.isfinite(t_lower):
            markers["t_lower"] = t_lower
        if t_upper is not None and math.isfinite(t_upper):
            markers["t_upper"] = t_upper
        if t_lower is None or t_upper is None:
            metrics["rise_time"] = _outcome("rise_time", "s", ResultStatus.NOT_REACHED, "本次观察内未依次达到上升时间的低、高阈值。")
        elif not math.isfinite(t_lower) or not math.isfinite(t_upper):
            metrics["rise_time"] = _outcome("rise_time", "s", ResultStatus.INVALID_DATA, "阈值交点计算产生非有限值。")
        else:
            metrics["rise_time"] = _outcome("rise_time", "s", ResultStatus.SUCCESS, "相对于目标值归一化，使用首次低阈值和随后首次高阈值的线性交点。", t_upper - t_lower)
    overshoot = max(0.0, float(np.max(normalized)) - 1.0) * 100.0
    metrics["overshoot_pct"] = _outcome("overshoot_pct", "%", ResultStatus.SUCCESS, "本次观测样本与 t0 边界上的目标归一化超调量；不推断未采到的峰值。", overshoot)

    with np.errstate(over="ignore", invalid="ignore"):
        band_lower = selection.r1 - band_width
        band_upper = selection.r1 + band_width
        distance = np.abs(observed_actual - selection.r1)
    if not math.isfinite(band_lower) or not math.isfinite(band_upper) or not np.isfinite(distance).all():
        metrics["settling_time"] = _outcome("settling_time", "s", ResultStatus.INVALID_DATA, "目标误差带或目标偏差计算溢出。")
    else:
        markers.update(band_lower=band_lower, band_upper=band_upper)
        # 直接比较显示的有限上下端点，让恰好位于 .98 等边界的采样计入带内；
        # 避免先相减放大浮点表示误差，不额外扩大用户配置的容差。
        outside = np.flatnonzero((observed_actual < band_lower) | (observed_actual > band_upper))
        if len(outside) and outside[-1] == len(observed_time) - 1:
            metrics["settling_time"] = _outcome("settling_time", "s", ResultStatus.NOT_REACHED, "记录末尾仍在目标误差带外；本次观测内未达到最终连续留带。")
        else:
            t_settle = selection.t0
            if len(outside):
                last = int(outside[-1])
                boundary = band_upper if observed_actual[last] > selection.r1 else band_lower
                t_settle = _crossing_time(
                    observed_time[last], observed_time[last + 1],
                    observed_actual[last], observed_actual[last + 1], boundary,
                )
            if not math.isfinite(t_settle):
                metrics["settling_time"] = _outcome("settling_time", "s", ResultStatus.INVALID_DATA, "最终入带交点计算产生非有限值。")
            else:
                markers["t_settle"] = t_settle
                remaining = float(observed_time[-1] - t_settle)
                details["settled_observation_s"] = remaining
                if remaining < cfg.min_observation_s:
                    metrics["settling_time"] = _outcome("settling_time", "s", ResultStatus.INSUFFICIENT_DATA, "最终进入目标误差带后的观察时长不足；不将短记录判断为已调节。")
                else:
                    metrics["settling_time"] = _outcome("settling_time", "s", ResultStatus.SUCCESS, "仅在本次观测区间内满足目标误差带与最小后续观察时长；不保证未来不再离带。", t_settle - selection.t0)
    return MetricComputation(metrics=metrics, markers=markers, details=details)
