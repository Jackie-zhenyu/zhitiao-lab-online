"""项目与会话之间的白名单桥接；先完整准备，再替换当前会话。

项目包的格式、摘要和数值复核由 projects.archive 负责。这里仅将已核验
的记录恢复成现有页面的控件草稿，不恢复模型身份、聊天证据或发送授权。
"""

from copy import deepcopy
import re

from core.comparison import available_duration
from core.event_models import EVENTS_VERSION, EventConfig
from core.events import detect_events
from core.import_models import IMPORT_VERSION, OPTIONAL_FIELDS, REQUIRED_FIELDS, ParserOptions
from core.models import ResultStatus
from core.steps import estimate_baseline
from projects.archive import ProjectData, ProjectError, ProjectRun
from ui.session import LabSession, fingerprint


_MIB = 1024 * 1024
_SAFE_INTEGER = (1 << 53) - 1


def _recorded_parser_options(parsed) -> ParserOptions:
    """旧快照没有独立解析配置字段，从既有解析操作记录读取限制。"""
    for operation in parsed.operations:
        match = re.fullmatch(r"导入限制：(\d+) bytes / (\d+) 数据记录", operation)
        if match:
            return ParserOptions(encoding=parsed.encoding, delimiter=parsed.delimiter,
                                 max_bytes=int(match[1]), max_rows=int(match[2]))
    raise ProjectError("实验缺少可追溯的原导入限制，不能恢复为工作实验。")


def capture_session(session_state, name: str, notes: str = "", *, include_report: bool = True) -> ProjectData:
    """只读取已有解析对象和确认结果；不遍历保存任意会话键。"""
    state = session_state.get("lab_session")
    current = None
    if isinstance(state, LabSession) and state.parsed is not None:
        parsed = state.parsed
        recorded = _recorded_parser_options(parsed)
        options = ParserOptions(
            encoding=session_state.get("csv_encoding", recorded.encoding),
            delimiter=session_state.get("csv_delimiter", recorded.delimiter),
            max_bytes=(session_state["max_mib"] * _MIB if "max_mib" in session_state else recorded.max_bytes),
            max_rows=session_state.get("max_rows", recorded.max_rows),
        )
        confirmed = bool(state.confirmed_key and state.prepared is not None and state.quality is not None)
        current = ProjectRun(parsed=parsed, metadata=state.source_metadata, parser_options=options,
                             prepared=state.prepared if confirmed else None,
                             quality=state.quality if confirmed else None,
                             performance=state.performance if confirmed else None,
                             evidence=state.evidence if confirmed else None)
    history = None
    if include_report:
        bundle = session_state.get("report_exports")
        history = bundle.snapshot if bundle is not None else session_state.get("project_historical_report")
    return deepcopy(ProjectData(
        name=name, notes=notes, current=current,
        saved_experiments=state.saved_experiments if isinstance(state, LabSession) else {},
        saved_event_configs=state.saved_event_configs if isinstance(state, LabSession) else {},
        comparison=state.comparison if isinstance(state, LabSession) else None,
        historical_report=history,
    ))


def _within(value, lower, upper=None) -> None:
    if value < lower or (upper is not None and value > upper):
        raise ProjectError("项目参数超出当前页面控件范围；未改动原会话或自动调整阈值。")


def _parser_widgets(options: ParserOptions) -> dict:
    if options.max_bytes % _MIB:
        raise ProjectError("当前页面只支持整数 MiB 导入上限，项目配置未被自动改变。")
    _within(options.max_bytes // _MIB, 1, 200)
    _within(options.max_rows, 1, 2000000)
    return {"csv_encoding": options.encoding, "csv_delimiter": options.delimiter,
            "max_mib": options.max_bytes // _MIB, "max_rows": options.max_rows}


def _mapping_widgets(state: LabSession, target: dict) -> None:
    mapping, config = state.prepared.mapping, state.quality.config
    _within(config.min_interval_ratio, .01, .99)
    _within(config.max_interval_ratio, 1.01, 100.)
    unset = "— 未映射 —"
    while unset in state.parsed.frame.columns:
        unset += "—"
    for field in REQUIRED_FIELDS + OPTIONAL_FIELDS:
        target[f"map_{state.revision}_{field}"] = mapping.fields.get(field, unset)
    for field, unit in mapping.optional_units.items():
        target[f"unit_{state.revision}_{field}"] = unit
    target.update({f"time_unit_{state.revision}": mapping.time_unit,
                   f"quantity_{state.revision}": mapping.quantity,
                   f"physical_unit_{state.revision}": mapping.unit,
                   f"units_confirmed_{state.revision}": True,
                   "interval_lower": config.min_interval_ratio,
                   "interval_upper": config.max_interval_ratio})
    state.confirmed_key = fingerprint({
        "source": state.parse_key, "fields": mapping.fields, "time_unit": mapping.time_unit,
        "quantity": mapping.quantity, "unit": mapping.unit, "optional_units": mapping.optional_units,
        "units_consistent": True, "min_interval_ratio": config.min_interval_ratio,
        "max_interval_ratio": config.max_interval_ratio, "version": IMPORT_VERSION,
    })


def _metric_widgets(config, target: dict) -> None:
    for value, lo, hi in (
        (config.tail_fraction, .01, 1.), (config.rise_lower, .001, .999),
        (config.rise_upper, .001, .999), (config.relative_band, 0., .99),
        (config.amplitude_epsilon, 1e-15, None), (config.baseline_window_s, .001, None),
        (config.baseline_min_points, 2, _SAFE_INTEGER),
    ):
        _within(value, lo, hi)
    names = {"tail": "tail_fraction", "rise_lower": "rise_lower", "rise_upper": "rise_upper",
             "band": "relative_band", "absolute": "absolute_tolerance",
             "observation": "min_observation_s", "epsilon": "amplitude_epsilon",
             "flat": "target_flat_tolerance", "baseline_range": "baseline_range_tolerance",
             "baseline_window": "baseline_window_s", "baseline_duration": "baseline_min_duration_s",
             "baseline_points": "baseline_min_points"}
    target.update({"metric_" + widget: getattr(config, field) for widget, field in names.items()})


def _step_widgets(state: LabSession, target: dict) -> None:
    result = state.performance
    step, config = result.step, result.config
    if step is None:
        return
    rows = [result.start_row, result.end_row]
    first, last = result.step_start_row, result.step_end_row
    if first is None or last is None or not rows[0] <= first < last <= rows[1]:
        raise ProjectError("确认阶跃未包含在当前连续分析区间内。")
    frame = state.prepared.frame.loc[rows[0] - 1:rows[1] - 1]
    _within(step.t0, float(frame.time.iloc[0]), float(frame.time.iloc[-1]))
    selection_id = fingerprint({"confirmed": state.confirmed_key, "rows": rows,
                                "config": config.model_dump()})[:16]
    baseline = estimate_baseline(frame.loc[first - 1:last - 1], step.t0, step.r0, config)
    if step.y0_source == "baseline":
        expected = (step.y0, step.baseline_start_s, step.baseline_end_s,
                    step.baseline_start_row, step.baseline_end_row, step.baseline_method)
        actual = (baseline.value, baseline.start_s, baseline.end_s,
                  baseline.start_row, baseline.end_row, baseline.reason)
        if baseline.status != ResultStatus.SUCCESS or expected != actual:
            raise ProjectError("阶跃初值与原有效基线不一致；没有猜测或替换初值。")
    mode = "使用有效基线估计" if step.y0_source == "baseline" else "手动提供 y0"
    manual = "" if step.y0_source == "baseline" else repr(step.y0)
    target.update({"step_mode": "手动指定",
                   f"step_start_manual_{selection_id}": first,
                   f"step_end_manual_{selection_id}": last,
                   f"step_t0_{selection_id}": step.t0,
                   f"step_r0_{selection_id}": step.r0,
                   f"step_r1_{selection_id}": step.r1,
                   f"step_y0_mode_{selection_id}": mode,
                   f"step_ack_{selection_id}": True})
    if manual:
        target[f"step_y0_manual_{selection_id}"] = manual
    state.step_selection = deepcopy(step)
    state.step_key = fingerprint({
        "selection": selection_id, "mode": "手动指定", "rows": [first, last],
        "t0": step.t0, "r0": step.r0, "r1": step.r1,
        "y0_mode": mode, "manual_text": manual,
        "baseline": baseline.model_dump(mode="json"), "acknowledged": True,
    })


def _analysis_widgets(state: LabSession, target: dict) -> None:
    result = state.performance
    if result is None:
        # 阻断数据且没有确认区间时保持未选择，不能暗中越过原问题。
        if state.quality.blocking:
            target[f"valid_interval_{state.confirmed_key}"] = None
        return
    if result.source_label != state.source_metadata.get("source_label", "用户声明来源，未经真实性验证"):
        raise ProjectError("项目来源标签与原分析记录不一致，不能在恢复时替换来源声明。")
    selected = next((index for index, interval in enumerate(state.quality.valid_intervals)
                     if interval.start_row <= result.start_row < result.end_row <= interval.end_row), None)
    if selected is None:
        raise ProjectError("保存的分析范围不是当前数据的连续有效区间。")
    config = result.config
    if (config.min_interval_ratio != state.quality.config.min_interval_ratio
            or config.max_interval_ratio != state.quality.config.max_interval_ratio
            or config.reference_interval_s != state.quality.interval_stats.get("median_s")):
        raise ProjectError("保存的指标连续性配置不能由当前页面原样恢复。")
    target.update({f"valid_interval_{state.confirmed_key}": selected,
                   f"start_{state.confirmed_key}_{selected}": result.start_row,
                   f"end_{state.confirmed_key}_{selected}": result.end_row})
    state.metric_interval = (result.start_row, result.end_row)
    _metric_widgets(config, target)
    _step_widgets(state, target)


def _event_widgets(state: LabSession, target: dict) -> None:
    if state.evidence is None:
        return
    config = state.evidence.config
    for value, lo, hi in (
        (config.oscillation_window_s, .01, None),
        (config.oscillation_min_crossings, 2, _SAFE_INTEGER),
        (config.oscillation_peak_to_peak, 1e-12, None),
        (config.near_target_band, 1e-12, None),
        (config.control_near_fraction, .001, .499),
        (config.control_min_duration_s, .001, None),
        (config.statistical_rate_factor, .01, None), (config.statistical_min_jump, 1e-12, None),
    ):
        _within(value, lo, hi)
    if config.output_limits is not None and "control" not in state.prepared.frame:
        raise ProjectError("保存规则包含输出限幅但没有控制输出字段，无法原样恢复确认。")
    if config.rate_limit_confirmed and config.rate_limit is None:
        raise ProjectError("保存规则缺少已经确认的物理变化率上限。")
    prefix = f"event_cfg_{state.revision}_{state.confirmed_key[:16]}_"
    target["event_controls_context"] = f"{state.revision}:{state.confirmed_key}"
    names = {"window": "oscillation_window_s", "crossings": "oscillation_min_crossings",
             "peak": "oscillation_peak_to_peak", "deadband": "oscillation_deadband",
             "near": "near_target_band", "flat": "target_flat_tolerance",
             "fraction": "oscillation_min_time_fraction", "control_band": "control_near_fraction",
             "control_duration": "control_min_duration_s", "factor": "statistical_rate_factor",
             "jump": "statistical_min_jump"}
    target.update({prefix + widget: getattr(config, field) for widget, field in names.items()})
    target.update({prefix + "limits_confirmed": config.output_limits is not None,
                   prefix + "rate": "" if config.rate_limit is None else repr(config.rate_limit),
                   prefix + "rate_confirmed": config.rate_limit_confirmed})
    if config.output_limits is not None:
        target[prefix + "lower"] = repr(config.output_limits.lower)
        target[prefix + "upper"] = repr(config.output_limits.upper)
    state.evidence_key = fingerprint({"experiment": state.prepared.experiment.experiment_id,
                                     "quality": state.quality.config.model_dump(),
                                     "rules": config.model_dump(), "version": EVENTS_VERSION})


def _comparison_widgets(state: LabSession, target: dict) -> None:
    result = state.comparison
    if result is None:
        return
    a_id, b_id = result.a.snapshot_id, result.b.snapshot_id
    if a_id not in state.saved_experiments or b_id not in state.saved_experiments:
        raise ProjectError("对比引用的实验快照不存在。")
    common = result.config.common_duration_s
    maximum = min(available_duration(state.saved_experiments[a_id]),
                  available_duration(state.saved_experiments[b_id]))
    _within(common, min(1e-9, maximum), maximum)
    target.update({"ab_selected_a": a_id, "ab_selected_b": b_id})
    for side, values in (("A", result.a), ("B", result.b)):
        for key, value in values.conditions.model_dump().items():
            target[f"ab_condition_{side}_{values.snapshot_id}_{key}"] = value or ""
    pair_key = fingerprint({"a": a_id, "b": b_id})[:16]
    target[f"ab_duration_{pair_key}"] = common
    window_key = fingerprint({"pair": pair_key, "duration": common})[:16]
    target[f"ab_confirmed_{window_key}"] = True
    state.comparison_key = fingerprint({"pair": pair_key, "duration": common,
                                        "conditions_a": result.a.conditions.model_dump(),
                                        "conditions_b": result.b.conditions.model_dump(),
                                        "confirmed": True})


def prepare_session(project: ProjectData) -> dict:
    """从已经核验的项目构造独立状态；任何失败均发生在替换旧会话之前。"""
    if not isinstance(project, ProjectData):
        raise ProjectError("没有有效的项目恢复对象。")
    project = deepcopy(project)
    if len(project.saved_experiments) > 8:
        raise ProjectError("项目实验快照超过当前会话的 8 份上限。")
    state = LabSession(saved_experiments=project.saved_experiments,
                       saved_event_configs=project.saved_event_configs, comparison=project.comparison)
    target = {"lab_session": state, "workspace_page": "实验分析",
              "project_name": project.name, "project_notes": project.notes,
              "report_project": project.name, "input_mode": "上传 CSV", "lab_input_mode": "上传 CSV"}
    if project.historical_report is not None:
        project.historical_report.data()  # 校验冻结摘要，不恢复为活动报告或 AI 证据。
        target["project_historical_report"] = project.historical_report
    if project.current is not None:
        run = project.current
        target.update(_parser_widgets(run.parser_options))
        state.set_source(run.parsed.raw_bytes, run.parsed.filename, run.metadata)
        state.parsed = run.parsed
        state.parse_key = fingerprint({"source": state.source_key, "options": run.parser_options.model_dump()})
        target["source_kind"] = state.source_metadata.get("source_kind", "unknown")
        if target["source_kind"] not in ("unknown", "measured", "simulated"):
            raise ProjectError("项目数据来源类型无法在当前页面显示。")
        if (run.prepared is None) != (run.quality is None):
            raise ProjectError("项目字段确认和质量结果不完整。")
        state.prepared, state.quality = run.prepared, run.quality
        state.performance, state.evidence = run.performance, run.evidence
        if state.prepared is not None:
            if target["source_kind"] != state.prepared.experiment.source.kind.value:
                raise ProjectError("项目来源选择与原分析记录不一致。")
            _mapping_widgets(state, target)
            _analysis_widgets(state, target)
            _event_widgets(state, target)
        elif run.performance is not None or run.evidence is not None:
            raise ProjectError("未确认的原始文件不能附带活动分析结果。")
    _comparison_widgets(state, target)
    return target


def project_for_snapshot(project: ProjectData, snapshot_id: str) -> ProjectData:
    """把用户选定的历史快照变成工作实验；保留其原初值、规则和来源声明。"""
    project = deepcopy(project)
    saved = project.saved_experiments.get(snapshot_id)
    if saved is None:
        raise ProjectError("所选快照不属于当前项目。")
    mapping = saved.prepared.mapping
    metadata = {"source_kind": saved.prepared.experiment.source.kind.value,
                "source_label": saved.performance.source_label, "quantity": mapping.quantity,
                "unit": mapping.unit, "time_unit": mapping.time_unit,
                "optional_units": mapping.optional_units, "conditions": saved.conditions.model_dump()}
    config = project.saved_event_configs.get(snapshot_id, EventConfig())
    if config.output_limits is not None:
        metadata["output_limits"] = {"lower": config.output_limits.lower, "upper": config.output_limits.upper}
    project.current = ProjectRun(
        parsed=saved.prepared.parsed, metadata=metadata,
        parser_options=_recorded_parser_options(saved.prepared.parsed),
        prepared=saved.prepared, quality=saved.quality, performance=saved.performance,
        evidence=detect_events(saved.prepared, saved.quality, config),
    )
    return project


def apply_pending_project(session_state) -> None:
    """必须在任何控件实例化之前调用；成功才一次性清除并写入新状态。"""
    pending = session_state.get("project_pending_state")
    if pending is None:
        return
    if not isinstance(pending, dict):
        raise ProjectError("待恢复会话状态无效，原会话未被清除。")
    replacement = deepcopy(pending)
    replacement.pop("project_pending_state", None)
    session_state.clear()
    session_state.update(replacement)
