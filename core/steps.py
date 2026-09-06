"""保守推荐目标阶跃，并在当前连续数据内估计阶跃前基线。

推荐不是用户确认。此模块不修改原始帧，不排序、补点、平滑或借用其他阶跃。
"""

import numpy as np
import pandas as pd

from core.metric_models import BaselineEstimate, MetricConfig, StepCandidate
from core.models import ResultStatus


def _median(values: np.ndarray) -> float:
    """稳定的中位数，避免两个很大的有限值相加溢出。"""
    ordered = np.sort(values)
    middle = len(ordered) // 2
    high = float(ordered[middle])
    if len(ordered) % 2:
        return high
    low = float(ordered[middle - 1])
    return low + (high - low) / 2 if low * high >= 0 else low / 2 + high / 2


def _validate_frame(frame: pd.DataFrame, config: MetricConfig) -> str | None:
    if not isinstance(frame, pd.DataFrame) or not {"time", "target", "actual"} <= set(frame.columns):
        return "缺少规范字段 time、target 或 actual，不能估计阶跃与基线。"
    if not frame.columns.is_unique:
        return "规范字段名重复，不能确定目标或实测信号。"
    try:
        valid_index = frame.index.dtype.kind in "iu" and all(int(index) >= 0 for index in frame.index)
    except (TypeError, ValueError, OverflowError):
        valid_index = False
    if not valid_index:
        return "必须保留非负整数原始行索引，不能重建或猜测行位置。"
    rows = frame.index.to_numpy()
    if any(int(right) != int(left) + 1 for left, right in zip(rows[:-1], rows[1:])):
        return "原始数据行不连续，不能跨缺失区间识别阶跃或估计基线。"
    try:
        values = frame.to_numpy(dtype=float)
    except (TypeError, ValueError, OverflowError):
        return "当前帧含非数值，须先完成质量检查。"
    if not np.isfinite(values).all():
        return "当前帧含缺失或非有限值，不能跨越问题数据估计。"
    if len(frame) < 2:
        return None
    time = frame["time"].to_numpy(dtype=float)
    with np.errstate(over="ignore", invalid="ignore"):
        delta = np.diff(time)
        span = float(time[-1] - time[0])
    if not np.isfinite(delta).all() or not (delta > 0).all() or not np.isfinite(span):
        return "时间必须严格递增且时间差有限；不能跨时间倒退、重置或溢出。"
    reference = config.reference_interval_s or _median(delta)
    with np.errstate(over="ignore", invalid="ignore"):
        ratios = delta / reference
    if not np.isfinite(ratios).all() or np.any(
        (ratios < config.min_interval_ratio) | (ratios > config.max_interval_ratio)
    ):
        return "采样间隔超出已配置的连续性规则，不能跨该断点估计。"
    return None


def discover_steps(
    frame: pd.DataFrame, config: MetricConfig | None = None,
) -> list[StepCandidate]:
    """推荐前后至少各两点、目标平台可靠的离散阶跃。

    平台边界的相邻跳变须超过两倍平台容差和幅值下限；这样平台内允许
    的抖动不会自行产生阶跃。若跳变量无法与平台容差区分，保守地不推荐。
    平台整体还须满足各点相对中位数的偏差不超过平台容差，防止小斜坡
    因相邻变化很小被误作常值平台。
    """
    cfg = MetricConfig.model_validate(config or MetricConfig())
    if _validate_frame(frame, cfg) is not None or len(frame) < 4:
        return []
    target = frame["target"].to_numpy(dtype=float)
    time = frame["time"].to_numpy(dtype=float)
    rows = frame.index.to_numpy()
    with np.errstate(over="ignore", invalid="ignore"):
        jumps = np.abs(np.diff(target))
    if not np.isfinite(jumps).all():
        return []
    threshold = max(cfg.amplitude_epsilon, 2 * cfg.target_flat_tolerance)
    changes = np.flatnonzero(jumps > threshold) + 1
    boundaries = [0, *(int(index) for index in changes), len(frame)]
    platforms: list[tuple[int, int, float, bool]] = []
    for start, stop in zip(boundaries[:-1], boundaries[1:]):
        values = target[start:stop]
        level = _median(values)
        with np.errstate(over="ignore", invalid="ignore"):
            deviation = np.abs(values - level)
        reliable = len(values) >= 2 and bool(np.all(deviation <= cfg.target_flat_tolerance))
        platforms.append((start, stop, level, reliable))
    candidates: list[StepCandidate] = []
    for previous, current in zip(platforms[:-1], platforms[1:]):
        pre_start, pre_stop, r0, pre_reliable = previous
        start, stop, r1, reliable = current
        amplitude = abs(r1 - r0)
        if not pre_reliable or not reliable or not np.isfinite(amplitude) or amplitude <= cfg.amplitude_epsilon:
            continue
        candidates.append(StepCandidate(
            pre_start_row=int(rows[pre_start]) + 1,
            step_row=int(rows[start]) + 1,
            end_row=int(rows[stop - 1]) + 1,
            t0=float(time[start]), r0=float(r0), r1=float(r1),
            previous_sample_s=float(time[pre_stop - 1]),
        ))
    return candidates


def _failure(status: ResultStatus, reason: str, **context) -> BaselineEstimate:
    return BaselineEstimate(status=status, value=None, reason=reason, **context)


def _finite_number(value: object) -> bool:
    if isinstance(value, (bool, np.bool_)) or not isinstance(value, (int, float, np.integer, np.floating)):
        return False
    try:
        return bool(np.isfinite(float(value)))
    except (OverflowError, ValueError, TypeError):
        return False


def estimate_baseline(
    frame: pd.DataFrame, t0: float, r0: float,
    config: MetricConfig | None = None,
) -> BaselineEstimate:
    """在 [t0-window, t0) 的现有样本中求实际首尾时间间的梯形均值。

    当前帧可从候选的 pre_start_row 开始，明确排除更早的阶跃。窗口含
    其他目标平台时返回失败，不偷偷删掉这些记录以得到一份成功基线。
    """
    cfg = MetricConfig.model_validate(config or MetricConfig())
    problem = _validate_frame(frame, cfg)
    if problem is not None:
        return _failure(ResultStatus.INVALID_DATA, problem)
    if len(frame) < 2:
        return _failure(ResultStatus.INSUFFICIENT_DATA, "当前连续选段不足两个样本，不能估计基线。")
    if not _finite_number(t0) or not _finite_number(r0):
        return _failure(ResultStatus.INVALID_DATA, "阶跃时刻 t0 和前目标 r0 必须是明确的有限数值。")
    time = frame["time"].to_numpy(dtype=float)
    if t0 < time[0] or t0 > time[-1]:
        return _failure(ResultStatus.INVALID_DATA, "阶跃时刻不在当前连续选段内，不能向记录外推基线。")
    # 仅保留闭左、开右窗口里的已有样本，不在窗口边界或 t0 人造采样点。
    with np.errstate(over="ignore", invalid="ignore"):
        window_start = float(t0 - cfg.baseline_window_s)
    if not np.isfinite(window_start):
        return _failure(ResultStatus.INVALID_DATA, "基线窗口起点计算溢出，不能估计。")
    # 闭左边界容许一 ULP 的相减舍入误差（如 0.8 - 0.5），不扩大物理窗口。
    selected = frame.loc[(time >= np.nextafter(window_start, -np.inf)) & (time < t0)]
    if not len(selected):
        return _failure(ResultStatus.INSUFFICIENT_DATA, "基线窗口内没有阶跃前样本；需要明确提供 y0，不能默认用零。")
    baseline_time = selected["time"].to_numpy(dtype=float)
    context = dict(
        start_s=float(baseline_time[0]), end_s=float(baseline_time[-1]),
        start_row=int(selected.index[0]) + 1, end_row=int(selected.index[-1]) + 1,
    )
    if len(selected) < cfg.baseline_min_points:
        return _failure(
            ResultStatus.INSUFFICIENT_DATA,
            f"基线窗口只有 {len(selected)} 个阶跃前样本，至少需要 {cfg.baseline_min_points} 个；未借用其他区间。",
            **context,
        )
    span = float(baseline_time[-1] - baseline_time[0])
    if span <= 0 or (
        span < cfg.baseline_min_duration_s
        and not np.isclose(span, cfg.baseline_min_duration_s, rtol=1e-12, atol=0.0)
    ):
        return _failure(
            ResultStatus.INSUFFICIENT_DATA,
            f"基线实际样本时间跨度 {span:.9g} s 小于所需 {cfg.baseline_min_duration_s:.9g} s；不外推到阶跃时刻。",
            **context,
        )
    with np.errstate(over="ignore", invalid="ignore"):
        target_deviation = np.abs(selected["target"].to_numpy(dtype=float) - r0)
    if not np.isfinite(target_deviation).all():
        return _failure(ResultStatus.INVALID_DATA, "基线目标与 r0 的差值溢出。", **context)
    if np.any(target_deviation > cfg.target_flat_tolerance):
        return _failure(
            ResultStatus.INSUFFICIENT_DATA,
            "基线窗口含其他目标平台或目标变化，不是已声明的 r0 常值平台；不能跨先前阶跃借基线。",
            **context,
        )
    actual = selected["actual"].to_numpy(dtype=float)
    with np.errstate(over="ignore", invalid="ignore"):
        value_range = float(actual.max() - actual.min())
    if not np.isfinite(value_range):
        return _failure(ResultStatus.INVALID_DATA, "基线实测峰峰差计算溢出。", **context)
    if (
        value_range > cfg.baseline_range_tolerance
        and not np.isclose(value_range, cfg.baseline_range_tolerance, rtol=1e-12, atol=0.0)
    ):
        return _failure(
            ResultStatus.INSUFFICIENT_DATA,
            f"基线实测峰峰差 {value_range:.9g} 超过容差 {cfg.baseline_range_tolerance:.9g}，缺少平稳基线证据；需要用户明确提供 y0。",
            **context,
        )
    # 对值和 dt 分别缩放，仍为实际时间复合梯形均值，避免有限量乘积溢出。
    scale = float(np.max(np.abs(actual)))
    if scale == 0:
        estimate = 0.0  # 所有已验证的基线实测样本确实为 0。
    else:
        scaled = actual / scale
        weights = np.diff(baseline_time) / span
        with np.errstate(over="ignore", invalid="ignore"):
            estimate = float(np.sum((scaled[:-1] + scaled[1:]) / 2 * weights) * scale)
    if not np.isfinite(estimate):
        return _failure(ResultStatus.INVALID_DATA, "时间加权基线均值计算溢出。", **context)
    return BaselineEstimate(
        status=ResultStatus.SUCCESS, value=estimate,
        reason=(
            f"使用 {len(selected)} 个已有阶跃前样本，在实际首尾时间间求复合梯形时间加权均值；"
            "目标符合 r0 平台，实测峰峰差符合配置；不延伸到 t0、不填补或默认零。"
        ),
        **context,
    )
