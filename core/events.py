"""第四阶段的确定性现象证据；只在质量有效片段内检查，不诊断根因。"""

import hashlib
import json
import math

import numpy as np

from core.event_models import (
    EVENTS_VERSION, EventConfig, EvidenceEvent, EvidenceReport, RuleCheck,
)
from core.import_models import PreparedExperiment, QualityReport
from core.quality import select_interval


_RULES = ("oscillation", "control_limit", "measurement_change", "data_record")
_TIME_PROBLEMS = {
    "missing_value", "non_numeric", "infinite_value", "duplicate_time",
    "time_reversal", "time_reset", "interval_overflow", "duration_overflow",
}
_SAMPLED_LIMITATION = "只使用本次记录的原始采样点；不能排除未采到的变化，也不证明系统正常。"


def _finite(value: float) -> float:
    value = float(value)
    if not math.isfinite(value):
        raise ValueError("规则计算产生非有限值或数值溢出")
    return value


def _median(values: np.ndarray) -> float:
    ordered = np.sort(values)
    mid = len(ordered) // 2
    high = float(ordered[mid])
    if len(ordered) % 2:
        return high
    # 调用处均为非负变化率/MAD，或用缩放后的有限目标值。
    return _finite(float(ordered[mid - 1]) / 2 + high / 2)


def _window_observations(time, target, actual, config: EventConfig) -> dict:
    duration = _finite(time[-1] - time[0])
    if duration <= 0:
        raise ValueError("观察窗口没有正时间跨度")
    with np.errstate(over="ignore", invalid="ignore", divide="ignore"):
        error = target - actual
        differences = np.diff(time)
        weights = differences / duration
        mean = _finite(np.sum((error[:-1] / 2 + error[1:] / 2) * weights))
        peak_to_peak = _finite(actual.max() - actual.min())
        outside = np.abs(error) > config.oscillation_deadband
        outside_fraction = _finite(np.sum(weights[outside[:-1]]))
        target_median = _median(target)
        target_deviation = _finite(np.max(np.abs(target - target_median)))
    if not np.isfinite(error).all():
        raise ValueError("误差计算产生非有限值")
    signs = np.sign(error[outside])
    crossings = int(np.count_nonzero(signs[1:] != signs[:-1]))
    return {
        "duration_s": duration,
        "peak_to_peak": peak_to_peak,
        "crossings": float(crossings),
        "mean_error": mean,
        "outside_time_fraction": outside_fraction,
        "outside_time_s": _finite(outside_fraction * duration),
        "target_median": target_median,
        "target_max_deviation": target_deviation,
    }


def _runs(flags: np.ndarray):
    """生成连续真值的闭索引范围；不合并任何假值或断点。"""
    padded = np.r_[False, flags, False].astype(np.int8)
    changes = np.diff(padded)
    yield from zip(np.flatnonzero(changes == 1), np.flatnonzero(changes == -1) - 1)


def detect_events(
    prepared: PreparedExperiment,
    quality: QualityReport,
    config: EventConfig | None = None,
) -> EvidenceReport:
    """返回规则命中证据和适用状态；配置校验使用 Pydantic，不改动实验数据。"""
    cfg = EventConfig.model_validate(config if config is not None else EventConfig())
    experiment = prepared.experiment
    events: list[EvidenceEvent] = []
    checks: list[RuleCheck] = []

    def finish():
        return EvidenceReport(
            experiment_id=experiment.experiment_id, source=experiment.source,
            config=cfg, checks=checks, events=events,
        )

    if quality.experiment_id != experiment.experiment_id or quality.row_count != len(prepared.frame):
        checks.extend(RuleCheck(rule=rule, status="invalid_data", reason="质量报告不匹配当前实验；未复用旧事件或旧定位。") for rule in _RULES)
        return finish()
    identity_context = json.dumps({
        "source": experiment.source.model_dump(mode="json"),
        "experiment_id": experiment.experiment_id,
        "config": cfg.model_dump(mode="json"),
        "quality_config": quality.config.model_dump(mode="json"),
        "algorithm_version": EVENTS_VERSION,
    }, ensure_ascii=False, sort_keys=True)

    def event(category, rule, first_row, last_row, start_s, end_s,
              thresholds, observations, charts, phenomenon, causes, limitations,
              discriminator=""):
        identity = f"{identity_context}|{rule}|{first_row}|{last_row}|{discriminator}"
        events.append(EvidenceEvent(
            event_id="evt-" + hashlib.sha256(identity.encode("utf-8")).hexdigest(),
            experiment_id=experiment.experiment_id, category=category,
            start_s=start_s, end_s=end_s,
            start_row=int(first_row), end_row=int(last_row), rule=rule,
            thresholds=thresholds, observations=observations, charts=charts,
            phenomenon=phenomenon, undetermined_causes=causes,
            limitations=[_SAMPLED_LIMITATION, *limitations],
        ))

    counts = {rule: {"checked": 0, "short": 0, "invalid": 0, "ineligible": 0}
              for rule in _RULES[:3]}
    control_enabled = "control" in prepared.mapping.fields and cfg.output_limits is not None
    physical_rate = cfg.rate_limit is not None and cfg.rate_limit_confirmed

    for valid in quality.valid_intervals:
        try:
            frame = select_interval(prepared, quality, valid.start_row, valid.end_row)
            time = frame["time"].to_numpy(dtype=float)
            target = frame["target"].to_numpy(dtype=float)
            actual = frame["actual"].to_numpy(dtype=float)
            duration = _finite(time[-1] - time[0])
            if duration <= 0:
                raise ValueError("片段时间跨度无效")
        except (ValueError, TypeError, OverflowError):
            for rule in counts:
                counts[rule]["invalid"] += 1
            continue
        first_row = valid.start_row

        # 半窗时长步进；相邻候选总扫描长度为 O(n)，不使用逐点全历史滑窗。
        if duration < cfg.oscillation_window_s:
            counts["oscillation"]["short"] += 1
        else:
            windows = []
            start = 0
            try:
                while start < len(time) - 1:
                    requested_end = _finite(time[start] + cfg.oscillation_window_s)
                    end = int(np.searchsorted(time, requested_end, side="left"))
                    if end >= len(time):
                        break
                    if end <= start:
                        raise ValueError("窗口长度小于时间戳表示精度")
                    observed = _window_observations(time[start:end + 1], target[start:end + 1], actual[start:end + 1], cfg)
                    if observed["target_max_deviation"] > cfg.target_flat_tolerance:
                        counts["oscillation"]["ineligible"] += 1
                    else:
                        counts["oscillation"]["checked"] += 1
                        if (
                            abs(observed["mean_error"]) <= cfg.near_target_band
                            and observed["peak_to_peak"] >= cfg.oscillation_peak_to_peak
                            and observed["crossings"] >= cfg.oscillation_min_crossings
                            and observed["outside_time_fraction"] >= cfg.oscillation_min_time_fraction
                        ):
                            if windows and start <= windows[-1][1]:
                                windows[-1] = (windows[-1][0], end, windows[-1][2] + 1)
                            else:
                                windows.append((start, end, 1))
                    next_time = _finite(time[start] + cfg.oscillation_window_s / 2)
                    start = max(start + 1, int(np.searchsorted(time, next_time, side="left")))
                for start, end, matched_count in windows:
                    observed = _window_observations(time[start:end + 1], target[start:end + 1], actual[start:end + 1], cfg)
                    observed["matched_window_count"] = float(matched_count)
                    event(
                        "oscillation", "stable_target_oscillation", first_row + start, first_row + end,
                        float(time[start]), float(time[end]),
                        {"requested_window_s": cfg.oscillation_window_s, "window_step_s": cfg.oscillation_window_s / 2,
                         "min_crossings": float(cfg.oscillation_min_crossings), "min_peak_to_peak": cfg.oscillation_peak_to_peak,
                         "deadband": cfg.oscillation_deadband, "near_target_band": cfg.near_target_band,
                         "target_flat_tolerance": cfg.target_flat_tolerance,
                         "min_outside_time_fraction": cfg.oscillation_min_time_fraction,
                         "quantity_unit": prepared.mapping.unit, "threshold_scope": "逐候选窗口检查；重叠命中窗口合并"},
                        observed, ["response", "error"], "稳定目标附近出现持续波动现象。",
                        "仅凭该波动不能确定 PID 设置、机械共振、测量噪声或不稳定性等根因。",
                        ["窗口只使用原始点，终点是首个达到请求窗长的采样点；实际跨度可能多一个采样间隔。",
                         "死区外时间占比使用左端样本状态保持；平均误差用实际时间复合梯形均值。",
                         "合并区间观测量重新计算；阈值成立范围是各命中窗口，不声称已完成频谱或稳定性诊断。"],
                    )
            except (ValueError, TypeError, OverflowError):
                counts["oscillation"]["invalid"] += 1

        if control_enabled:
            if duration < cfg.control_min_duration_s:
                counts["control_limit"]["short"] += 1
            else:
                try:
                    limits = cfg.output_limits
                    width = _finite((limits.upper - limits.lower) * cfg.control_near_fraction)
                    control = frame["control"].to_numpy(dtype=float)
                    for side, limit in (("upper", limits.upper), ("lower", limits.lower)):
                        near_lower, near_upper = _finite(limit - width), _finite(limit + width)
                        near = (control >= near_lower) & (control <= near_upper)
                        fraction = _finite(np.sum((np.diff(time) / duration)[near[:-1]]))
                        for start, end in _runs(near):
                            span = _finite(time[end] - time[start])
                            if span < cfg.control_min_duration_s:
                                continue
                            label = "上限" if side == "upper" else "下限"
                            event(
                                "control_limit", f"control_near_{side}_limit", first_row + start, first_row + end,
                                float(time[start]), float(time[end]),
                                {"lower_limit": limits.lower, "upper_limit": limits.upper,
                                 "limits_confirmed": True, "near_fraction": cfg.control_near_fraction,
                                 "absolute_near_band": width, "min_duration_s": cfg.control_min_duration_s,
                                 "control_unit": prepared.mapping.optional_units["control"]},
                                {"duration_s": span, "control_min": float(control[start:end + 1].min()),
                                 "control_max": float(control[start:end + 1].max()), "limit_side": side,
                                 "valid_interval_near_time_fraction": fraction, "valid_interval_duration_s": duration},
                                ["control"], f"控制输出持续接近用户确认的{label}。",
                                "无法据此确认积分饱和、控制器内部状态或抗饱和算法是否有效。",
                                ["接近要求与限值的绝对距离在配置带内；远超限值不属于本条近限规则。",
                                 "持续时长为连续命中样本首尾时间差，不越过未命中样本。"],
                            )
                    counts["control_limit"]["checked"] += 1
                except (ValueError, TypeError, OverflowError, KeyError):
                    counts["control_limit"]["invalid"] += 1

        if not physical_rate and len(time) < 4:
            counts["measurement_change"]["short"] += 1
        else:
            try:
                dt = np.diff(time)
                with np.errstate(over="ignore", invalid="ignore", divide="ignore"):
                    changes = np.diff(actual)
                    rates = np.abs(changes) / dt
                if not np.isfinite(changes).all() or not np.isfinite(rates).all():
                    raise ValueError("测量变化率运算溢出")
                if physical_rate:
                    threshold, median_rate, mad = cfg.rate_limit, None, None
                    hits = rates > threshold
                else:
                    median_rate = _median(rates)
                    mad = _median(np.abs(rates - median_rate))
                    threshold = _finite(median_rate + cfg.statistical_rate_factor * 1.4826 * mad)
                    hits = (rates > threshold) & (np.abs(changes) >= cfg.statistical_min_jump)
                for index in np.flatnonzero(hits):
                    rule = "measurement_physical_rate_limit" if physical_rate else "measurement_statistical_jump"
                    event(
                        "measurement_change", rule, first_row + index, first_row + index + 1,
                        float(time[index]), float(time[index + 1]),
                        {"rate_threshold": threshold, "physical_limit_confirmed": physical_rate,
                         "rate_unit": f"{prepared.mapping.unit}/s", "quantity_unit": prepared.mapping.unit,
                         "statistical_min_jump": None if physical_rate else cfg.statistical_min_jump,
                         "statistical_factor": None if physical_rate else cfg.statistical_rate_factor},
                        {"delta_t_s": float(dt[index]), "delta_actual": float(changes[index]),
                         "absolute_jump": float(abs(changes[index])), "absolute_rate": float(rates[index]),
                         "median_absolute_rate": median_rate, "rate_mad": mad},
                        ["response"], "测量变化率超过用户确认的物理变化率上限。" if physical_rate else "测量出现统计突变候选。",
                        "无法仅凭相邻采样变化确认传感器故障；真实快速动态、记录问题等原因尚不能确定。",
                        ["变化率只描述这一对相邻有效样本；不跨断点补值。",
                         "物理上限由用户声明确认，未独立验证设备规格。" if physical_rate else
                         "未确认物理变化率上限；统计阈值来自当前连续片段，不代表设备物理约束。"],
                    )
                counts["measurement_change"]["checked"] += 1
            except (ValueError, TypeError, OverflowError):
                counts["measurement_change"]["invalid"] += 1

    for rule in _RULES[:3]:
        count = counts[rule]
        if rule == "control_limit" and not control_enabled:
            reason = "未映射 control。" if "control" not in prepared.mapping.fields else "未提供用户明确确认的控制输出上下限；未推测限幅。"
            checks.append(RuleCheck(rule=rule, status="not_applicable", reason=reason))
        elif count["invalid"]:
            checks.append(RuleCheck(rule=rule, status="invalid_data", reason=f"有 {count['invalid']} 个片段存在失效定位或数值问题；其他已检查片段的证据仍单独保留。"))
        elif count["checked"]:
            checks.append(RuleCheck(rule=rule, status="checked", reason=f"已完成 {count['checked']} 个窗口或有效片段检查；未发现规则事件不等于系统正常。"))
        elif count["ineligible"]:
            checks.append(RuleCheck(rule=rule, status="not_applicable", reason="观察窗口目标未满足稳定平台容差；未将变化目标解释为稳定目标附近振荡。"))
        else:
            requirement = "完整振荡观察窗口" if rule == "oscillation" else "控制输出最小持续时长" if rule == "control_limit" else "统计变化率至少三个相邻差分样本，物理上限检查至少一对相邻样本"
            checks.append(RuleCheck(rule=rule, status="insufficient_data", reason=f"没有满足 {requirement} 的连续有效片段；未跨断点拼接。"))

    unreliable_time = any(issue.field == "time" and issue.code in _TIME_PROBLEMS for issue in quality.issues)
    for issue in quality.issues:
        timestamp = None
        if not unreliable_time and 1 <= issue.row <= len(prepared.frame):
            value = prepared.frame.iloc[issue.row - 1]["time"]
            if np.isfinite(value):
                timestamp = float(value)
        time_reason = "时间轴含缺失、重复、倒退、重置或溢出，无法可靠唯一定位；仅使用原始数据行聚焦。" if unreliable_time else "原始记录的有限时间戳；该问题未被修复。"
        event(
            "data_record", f"quality_{issue.code}", issue.row, issue.row, timestamp, timestamp,
            {"quality_min_interval_ratio": quality.config.min_interval_ratio,
             "quality_max_interval_ratio": quality.config.max_interval_ratio},
            {"quality_code": issue.code, "field": issue.field, "csv_line": float(issue.csv_line),
             "data_row": float(issue.row), "original_message": issue.message, "time_location_reason": time_reason},
            ["raw_data"], f"数据记录异常：{issue.message}",
            "该证据描述文件中的记录问题，不能据此确定设备故障或具体记录链路根因。",
            [time_reason, "保留质量规则原始说明与行位置；没有排序、删除、填补或修复原数据。"],
            discriminator=issue.field,
        )
    checks.append(RuleCheck(rule="data_record", status="checked", reason=f"已转录 {len(quality.issues)} 条质量问题并保留原始行位置；没有记录问题不证明系统完全正常。"))
    return finish()
