"""明确转换、逐行质量定位和连续区间选择；不依赖 UI 或模型。

原始字节和原始字符串表从不修改。NaN 仅作为错误标记；不排序、删除、
填补、平滑或插值。详细规则见 docs/QUALITY.md。
"""

import hashlib
import json

import numpy as np
import pandas as pd

from core.import_models import (
    IMPORT_VERSION,
    MappingConfig,
    ParsedCSV,
    PreparedExperiment,
    QualityConfig,
    QualityIssue,
    QualityReport,
    ValidInterval,
)
from core.models import DataSource, Experiment, SourceKind


MISSING_TOKENS = frozenset({"", "na", "n/a", "nan", "null", "none", "<na>"})


def _is_missing(value: object) -> bool:
    return value is None or str(value).strip().lower() in MISSING_TOKENS


def _to_float(value: object) -> float:
    if _is_missing(value):
        return float("nan")
    try:
        return float(str(value).strip())
    except (ValueError, OverflowError):
        return float("nan")


def prepare_experiment(
    parsed: ParsedCSV,
    mapping: MappingConfig,
    source_kind: SourceKind = SourceKind.UNKNOWN,
) -> PreparedExperiment:
    """校验用户已确认映射，建立独立规范字段表及可追溯实验元数据。"""
    mapping = MappingConfig.model_validate(mapping)
    source_kind = SourceKind(source_kind)
    missing = set(mapping.fields.values()) - set(parsed.frame.columns)
    if missing:
        raise ValueError(f"映射引用了不存在的原始列：{', '.join(sorted(missing))}")
    if len(parsed.source_lines) != len(parsed.frame):
        raise ValueError("原始行定位数量与数据行数不一致")
    if not len(parsed.frame):
        raise ValueError("CSV 没有数据记录")
    actual_sha = hashlib.sha256(parsed.raw_bytes).hexdigest()
    if actual_sha != parsed.sha256:
        raise ValueError("原始字节与数据摘要不一致")

    frame = pd.DataFrame(
        {
            field: np.fromiter(
                (_to_float(value) for value in parsed.frame[column]),
                dtype=np.float64,
                count=len(parsed.frame),
            )
            for field, column in mapping.fields.items()
        }
    )
    operations = list(parsed.operations)
    operations.append(
        "用户确认字段映射："
        + json.dumps(mapping.fields, ensure_ascii=False, sort_keys=True)
    )
    operations.append(
        "规范字段转为 float64；去除数值两端空白；空值标记及非数值以 NaN 标记，"
        "无穷值保留以供质量检查；原始字符串和字节保持不变。"
    )
    if mapping.time_unit == "ms":
        frame["time"] = frame["time"] / 1000.0
        operations.append("时间单位 ms → s，规范时间数值除以 1000；不改变时间基准。")
    else:
        operations.append("时间单位 s → s；不改变数值或时间基准。")
    operations.append(
        f"用户声明物理量：{mapping.quantity}；目标值与实测值单位一致：{mapping.unit}。"
    )
    if mapping.optional_units:
        operations.append(
            "用户声明可选字段单位："
            + json.dumps(mapping.optional_units, ensure_ascii=False, sort_keys=True)
        )
    with np.errstate(over="ignore", invalid="ignore"):
        frame["error"] = frame["target"] - frame["actual"]
    operations.append(f"误差 error = target - actual，单位 {mapping.unit}；溢出阻断使用。")
    operations.append("不排序、删除、填补、平滑或插值；所有原始数据行保持原顺序。")
    operations.append(f"数据准备与质量检查算法版本：{IMPORT_VERSION}。")

    identity = {
        "sha256": actual_sha,
        "mapping": mapping.model_dump(mode="json"),
        "encoding": parsed.encoding,
        "delimiter": parsed.delimiter,
        "source_kind": source_kind.value,
        "algorithm_version": IMPORT_VERSION,
    }
    experiment_id = "exp-" + hashlib.sha256(
        json.dumps(identity, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    raw_fields = {column: None for column in parsed.frame.columns}
    for field, column in mapping.fields.items():
        raw_fields[column] = (
            mapping.time_unit if field == "time" else
            mapping.unit if field in ("target", "actual") else
            mapping.optional_units[field]
        )
    experiment = Experiment(
        experiment_id=experiment_id,
        name=parsed.filename,
        source=DataSource(
            source_id=f"sha256:{actual_sha}",
            experiment_id=experiment_id,
            kind=source_kind,
            filename=parsed.filename,
            sha256=actual_sha,
        ),
        raw_fields=raw_fields,
    )
    return PreparedExperiment(parsed, experiment, mapping, frame, operations)


def _interval_statistics(positive: np.ndarray) -> dict[str, float | int | None]:
    """仅相邻原始行之间有限且正的 dt；缩放避免统计过程中平方溢出。"""
    if not len(positive):
        return dict(min_s=None, median_s=None, max_s=None, mean_s=None, std_s=None, count=0)
    maximum = float(positive.max())
    scaled = positive / maximum
    ordered = np.sort(positive)
    middle = len(ordered) // 2
    median = float(ordered[middle])
    if not len(ordered) % 2:
        low = float(ordered[middle - 1])
        median = low + (median - low) / 2
    return {
        "min_s": float(positive.min()),
        "median_s": median,
        "max_s": maximum,
        "mean_s": float(np.mean(scaled) * maximum),
        "std_s": float(np.std(scaled, ddof=0) * maximum),
        "count": int(len(positive)),
    }


def check_quality(
    prepared: PreparedExperiment, config: QualityConfig | None = None,
) -> QualityReport:
    """检查已映射全部字段，报告问题位置，并构建未清洗的连续有效区间。"""
    config = QualityConfig.model_validate(config or QualityConfig())
    frame = prepared.frame
    row_count = len(frame)
    issues: list[QualityIssue] = []

    def add(code: str, index: int, field: str, message: str) -> None:
        issues.append(QualityIssue(
            code=code, row=index + 1,
            csv_line=prepared.parsed.source_lines[index],
            field=field, message=message, blocking=True,
        ))

    for field, column in prepared.mapping.fields.items():
        values = frame[field].to_numpy(dtype=float)
        raw_values = prepared.parsed.frame[column].to_numpy()
        for index in np.flatnonzero(~np.isfinite(values)):
            if _is_missing(raw_values[index]):
                add("missing_value", int(index), field, f"原始列 {column} 为缺失值；未填补。")
            elif np.isinf(values[index]):
                add("infinite_value", int(index), field, f"原始列 {column} 为无穷值或数值超出 float64 范围。")
            else:
                add("non_numeric", int(index), field, f"原始列 {column} 不能转换为数值；原值保留。")

    finite_inputs = np.isfinite(frame[["target", "actual"]].to_numpy()).all(axis=1)
    for index in np.flatnonzero(finite_inputs & ~np.isfinite(frame["error"].to_numpy())):
        add("error_overflow", int(index), "error", "目标值与实测值有限，但误差相减溢出；不能绘制或分析该行。")

    time = frame["time"].to_numpy(dtype=float)
    finite_time = np.isfinite(time)
    with np.errstate(over="ignore", invalid="ignore"):
        differences = np.diff(time)
    adjacent_finite = finite_time[:-1] & finite_time[1:]
    positive_mask = adjacent_finite & np.isfinite(differences) & (differences > 0)
    statistics = _interval_statistics(differences[positive_mask])
    median = statistics["median_s"]

    # 全局重复用于定位；不将重置后所有重复行判为无效，以保留可显式选择的片段。
    seen: dict[float, int] = {}
    for index in np.flatnonzero(finite_time):
        timestamp = float(time[index])
        if timestamp in seen:
            first_row = seen[timestamp] + 1
            add("duplicate_time", int(index), "time", f"时间戳与数据行 {first_row} 重复；整段不可直接使用。")
        else:
            seen[timestamp] = int(index)

    safe_edge = positive_mask.copy()
    for edge in np.flatnonzero(adjacent_finite):
        index = int(edge) + 1
        delta = differences[edge]
        if not np.isfinite(delta):
            add("interval_overflow", index, "time", f"与数据行 {index} 的时间差计算溢出；不能跨越该边界。")
        elif delta < 0:
            add("time_reversal", index, "time", f"相对于数据行 {index} 时间倒退 {abs(delta):.9g} s。")
            if median is not None and 0 <= time[index] <= median * 0.1 and -delta >= median:
                add("time_reset", index, "time", "疑似时间戳重置：倒退后时间接近零且回退不少于中位采样间隔；这是启发式检查，不等于设备故障。")
        elif delta > 0 and median is not None:
            with np.errstate(over="ignore", invalid="ignore"):
                ratio = float(delta / median)
            if ratio < config.min_interval_ratio or ratio > config.max_interval_ratio:
                safe_edge[edge] = False
                add("interval_anomaly", index, "time", (
                    f"与数据行 {index} 的间隔 {delta:.9g} s 超出正间隔中位数的 "
                    f"[{config.min_interval_ratio:g}, {config.max_interval_ratio:g}] 倍；"
                    "这是连续性检查规则，不等于设备故障。"
                ))

    duration = None
    if row_count and finite_time.all() and np.all(np.isfinite(differences)) and np.all(differences >= 0):
        with np.errstate(over="ignore", invalid="ignore"):
            span = float(time[-1] - time[0])
        if np.isfinite(span):
            duration = span
        elif row_count:
            add("duration_overflow", row_count - 1, "time", "首尾时间差溢出，整段时长不可用。")

    finite_rows = np.isfinite(frame.to_numpy(dtype=float)).all(axis=1)
    intervals: list[ValidInterval] = []
    start: int | None = None

    def finish(end: int) -> None:
        if start is not None and end > start:
            intervals.append(ValidInterval(
                start_row=start + 1, end_row=end + 1,
                start_s=float(time[start]), end_s=float(time[end]),
            ))

    for index in range(row_count):
        if not finite_rows[index]:
            finish(index - 1)
            start = None
        elif start is None:
            start = index
        elif not safe_edge[index - 1]:
            finish(index - 1)
            start = index
    finish(row_count - 1)
    if row_count == 1:
        add("insufficient_data", 0, "time", "仅有一个数据点；连续曲线至少需要两个点。")
    return QualityReport(
        row_count, duration, statistics, issues, intervals, config,
        experiment_id=prepared.experiment.experiment_id,
    )


def select_interval(
    prepared: PreparedExperiment, report: QualityReport, start_row: int, end_row: int,
) -> pd.DataFrame:
    """仅复制用户明确选择的单一有效连续原始区间，不跨问题位置。"""
    if type(start_row) is not int or type(end_row) is not int or end_row <= start_row:
        raise ValueError("区间必须使用数据行整数，且至少包含两个点")
    if (
        report.experiment_id != prepared.experiment.experiment_id
        or report.row_count != len(prepared.frame)
    ):
        raise ValueError("质量报告不匹配当前数据")
    if not any(
        interval.start_row <= start_row < end_row <= interval.end_row
        for interval in report.valid_intervals
    ):
        raise ValueError("所选行必须位于同一个连续有效区间，不能跨越缺失值或时间异常")
    selected = prepared.frame.iloc[start_row - 1:end_row].copy(deep=True)
    # 即使调用方传入旧报告，也不让选择跨越非有限值或失效的时间边界。
    if len(selected) != end_row - start_row + 1 or not np.isfinite(selected.to_numpy()).all():
        raise ValueError("所选区间的数据已变化，请重新执行质量检查")
    with np.errstate(over="ignore", invalid="ignore"):
        delta = np.diff(selected["time"].to_numpy())
    if not np.all(np.isfinite(delta) & (delta > 0)):
        raise ValueError("所选区间时间必须严格递增，请重新执行质量检查")
    median = report.interval_stats.get("median_s")
    if median is None or not np.isfinite(median) or median <= 0:
        raise ValueError("质量报告缺少有效采样间隔基准，请重新执行质量检查")
    with np.errstate(over="ignore", invalid="ignore"):
        ratios = delta / median
    if np.any(
        (ratios < report.config.min_interval_ratio)
        | (ratios > report.config.max_interval_ratio)
    ):
        raise ValueError("所选区间采样间隔已超出质量报告的阈值，请重新执行质量检查")
    return selected
