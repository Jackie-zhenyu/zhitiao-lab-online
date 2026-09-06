"""A/B 原生窗口、快照校验与差值语义；所有输入均为数学合成夹具。"""

from copy import deepcopy

import numpy as np
import pandas as pd
import pytest

from core.comparison import available_duration, capture_experiment, compare_experiments
from core.comparison_models import ComparisonConfig, ExperimentConditions
from core.import_models import MappingConfig
from core.metric_models import MetricConfig, StepSelection
from core.models import ResultStatus, SourceKind
from core.parser import parse_csv
from core.performance import analyze_performance
from core.quality import check_quality, prepare_experiment


def known_conditions(**changes):
    values = dict(plant="同一对象", load="同一负载", sampling="记录的采样设置", environment="同一环境", controller="Kp=1")
    values.update(changes)
    return ExperimentConditions(**values)


def context(*, name="A", relative=None, t0=0.5, r0=0.0, r1=1.0, y0=0.0, post_error=None, quantity="响应量", unit="1", metric_changes=None):
    relative = np.arange(-2, 11, dtype=float) / 4 if relative is None else np.asarray(relative, dtype=float)
    times = relative + t0
    target = np.where(relative < 0, r0, r1)
    if post_error is None:
        actual = np.where(relative < 0, y0, r1 + (y0 - r1) * np.exp(-np.maximum(relative, 0)))
    else:
        actual = np.where(relative < 0, y0, r1 - post_error)
    raw = pd.DataFrame({"time": times, "target": target, "actual": actual}).to_csv(index=False).encode("utf-8")
    parsed = parse_csv(raw, f"{name}.csv")
    mapping = MappingConfig(fields={"time": "time", "target": "target", "actual": "actual"}, time_unit="s", quantity=quantity, unit=unit, confirmed=True, units_consistent=True)
    prepared = prepare_experiment(parsed, mapping, SourceKind.SIMULATED)
    quality = check_quality(prepared)
    config_values = dict(reference_interval_s=quality.interval_stats["median_s"])
    config_values.update(metric_changes or {})
    config = MetricConfig(**config_values)
    step = StepSelection(t0=float(t0), r0=float(r0), r1=float(r1), y0=float(y0), y0_source="manual", confirmed=True)
    performance = analyze_performance(prepared, quality, 1, len(parsed.frame), config, source_label="解析函数合成数据，不是实测", step=step, step_start_row=1, step_end_row=len(parsed.frame))
    return prepared, quality, performance


def saved(*, conditions=None, **kwargs):
    prepared, quality, performance = context(**kwargs)
    return capture_experiment(prepared, quality, performance, kwargs.get("name", "A"), conditions or known_conditions())


def compare(a, b, duration=2.0, **kwargs):
    return compare_experiments(a, b, ComparisonConfig(common_duration_s=float(duration), confirmed=True), **kwargs)


def differences(result):
    return {metric.key: metric for metric in result.differences}


def test_self_comparison_recomputes_native_post_window_and_all_available_deltas_are_zero():
    experiment = saved()
    original = deepcopy(experiment.performance)
    result = compare(experiment, experiment, duration=1.5)
    assert len(result.differences) == 7
    assert all(metric.delta == 0.0 for metric in result.differences if metric.a.status == ResultStatus.SUCCESS)
    assert all(metric.percent == 0.0 for metric in result.differences if metric.a.status == ResultStatus.SUCCESS and metric.a.value != 0)
    assert differences(result)["overshoot_pct"].delta == 0.0
    assert differences(result)["overshoot_pct"].percent is None
    assert "A 为 0" in differences(result)["overshoot_pct"].reason
    assert (result.a.relative_start_s, result.a.relative_end_s) == (0.0, 1.5)
    assert result.a.start_row == 3 and result.a.end_row == 9
    assert result.a.step_start_row == 1
    assert result.a.tracking.details["interval_start_s"] == 0.5
    assert result.a.tracking.details["interval_end_s"] == 2.0
    assert result.a.response.details["observation_start_s"] == 0.5
    assert result.a.response.details["observation_end_s"] == 2.0
    assert result.a.tracking.metrics["iae"].value != original.tracking.metrics["iae"].value
    assert experiment.performance == original
    assert not result.descriptive_only
    assert result.can_overlay


def test_alignment_uses_each_negative_step_t0_instead_of_file_start():
    first = saved(name="A", t0=-5.0, r0=2.0, r1=-1.0, y0=2.0)
    second = saved(name="B", t0=5.0, r0=2.0, r1=-1.0, y0=2.0)
    result = compare(first, second)
    assert (result.a.start_s, result.b.start_s) == (-5.0, 5.0)
    assert (result.a.relative_start_s, result.b.relative_start_s) == (0.0, 0.0)
    assert (result.a.relative_end_s, result.b.relative_end_s) == (2.0, 2.0)
    assert all(metric.delta == pytest.approx(0.0, abs=1e-14) for metric in result.differences if metric.a.status == ResultStatus.SUCCESS)


def test_native_endpoints_are_not_interpolated_and_time_integral_uses_actual_span():
    # Native dt is .6 or .7; both full records extend past requested H=2.5.
    first = saved(name="A", t0=0.0, relative=[-1.2, -.6, 0, .6, 1.2, 1.8, 2.4, 3.0], post_error=1.0)
    second = saved(name="B", t0=0.0, relative=[-1.4, -.7, 0, .7, 1.4, 2.1, 2.8], post_error=1.0)
    result = compare(first, second, duration=2.5)
    assert result.a.relative_end_s == 2.4
    assert result.b.relative_end_s == 2.1
    assert result.a.tracking.metrics["iae"].value == pytest.approx(2.4)
    assert result.b.tracking.metrics["iae"].value == pytest.approx(2.1)
    assert differences(result)["iae"].delta == pytest.approx(-0.3)
    assert differences(result)["iae"].percent == pytest.approx(-12.5)
    assert result.descriptive_only
    assert any(check.name == "native_window" and check.status == "mismatch" for check in result.checks)
    assert any("未插入对齐点" in warning for warning in result.warnings)
    assert any(check.name == "metric_config" and check.status == "match" for check in result.checks)


@pytest.mark.parametrize("change", [{"unit": "rad"}, {"quantity": "角度"}, {"r1": 2.0}, {"y0": 0.01}, {"metric_changes": {"tail_fraction": 0.2}}])
def test_incompatible_units_quantity_reference_or_parameters_withhold_differences(change):
    result = compare(saved(name="A"), saved(name="B", **change))
    assert all(metric.delta is None and metric.percent is None for metric in result.differences)
    assert result.descriptive_only
    assert result.can_overlay is not ("unit" in change or "quantity" in change)
    assert all(metric.a.value is not None for metric in result.differences[:4])


@pytest.mark.parametrize("value", [None, "未知", "unknown", "不详", "n/a"])
def test_unknown_conditions_even_when_equal_remain_unknown_and_descriptive(value):
    conditions = known_conditions(load=value)
    result = compare(saved(name="A", conditions=conditions), saved(name="B", conditions=conditions))
    assert result.descriptive_only
    assert next(check for check in result.checks if check.name == "load").status == "unknown"
    assert differences(result)["iae"].delta == 0.0
    assert any("PID" in warning for warning in result.warnings)


def test_different_conditions_warn_while_controller_differences_are_permitted():
    first = saved(name="A", conditions=known_conditions())
    second = saved(name="B", conditions=known_conditions(controller="Kp=2"))
    result = compare(first, second)
    assert differences(result)["iae"].delta == 0.0
    assert next(check for check in result.checks if check.name == "controller").status == "mismatch"
    assert not result.descriptive_only
    changed_load = compare(first, second, conditions_b=known_conditions(load="不同负载", controller="Kp=2"))
    assert changed_load.descriptive_only
    assert differences(changed_load)["iae"].delta == 0.0
    assert changed_load.comparison_id != result.comparison_id
    assert second.conditions.load == "同一负载"


@pytest.mark.parametrize("controller", [None, "unknown", "未知"])
def test_unknown_controller_requires_descriptive_comparison_even_when_other_conditions_match(controller):
    first = saved(name="A", conditions=known_conditions(controller=controller))
    second = saved(name="B", conditions=known_conditions())
    result = compare(first, second)
    assert result.descriptive_only
    assert next(check for check in result.checks if check.name == "controller").status == "unknown"
    assert differences(result)["iae"].delta == 0.0
    assert any("控制器参数" in warning and "未知" in warning for warning in result.warnings)


def test_signed_tail_bias_percentage_uses_absolute_a_denominator():
    result = compare(saved(name="A", post_error=-0.5), saved(name="B", post_error=0.5))
    bias = differences(result)["tail_bias"]
    assert (bias.a.value, bias.b.value) == pytest.approx((-0.5, 0.5))
    assert bias.delta == pytest.approx(1.0)
    assert bias.percent == pytest.approx(200.0)
    assert "有符号" in bias.reason


def test_unavailable_responses_are_saved_but_never_replaced_with_zero_differences():
    experiment = saved(post_error=1.0)
    result = compare(experiment, experiment)
    rise = differences(result)["rise_time"]
    assert rise.a.status == ResultStatus.NOT_REACHED
    assert rise.delta is rise.percent is None
    assert rise.reason


def test_percentage_overflow_keeps_finite_delta_and_explicit_empty_percent():
    first = saved(name="A", r0=1.0, r1=0.0, y0=1.0, post_error=1e-300)
    second = saved(name="B", r0=1.0, r1=0.0, y0=1.0, post_error=1e150)
    result = compare(first, second)
    bias = differences(result)["tail_bias"]
    assert bias.delta == pytest.approx(1e150)
    assert bias.percent is None
    assert "溢出" in bias.reason


def test_delta_overflow_cannot_escape_as_infinite_value():
    options = dict(r0=1e308, r1=0.0, y0=1e308)
    first = saved(name="A", post_error=1e307, **options)
    second = saved(name="B", post_error=-1.797e308, **options)
    result = compare(first, second, duration=0.5)
    bias = differences(result)["tail_bias"]
    assert bias.delta is bias.percent is None
    assert "溢出" in bias.reason
    result.model_dump_json()


def test_duration_is_confirmed_step_end_minus_t0_and_cannot_be_exceeded():
    experiment = saved()
    assert available_duration(experiment) == 2.5
    with pytest.raises(ValueError, match="不能超过"):
        compare(experiment, experiment, duration=2.6)
    with pytest.raises(ValueError):
        compare_experiments(experiment, experiment, {"common_duration_s": 1.0, "confirmed": False})
    with pytest.raises(ValueError, match="两个原始阶跃后"):
        compare(experiment, experiment, duration=0.1)


def test_capture_deepcopies_every_mutable_component_and_preserves_source_label():
    prepared, quality, performance = context()
    conditions = known_conditions()
    snapshot = capture_experiment(prepared, quality, performance, "A", conditions)
    original = snapshot.performance.model_dump(mode="json")
    prepared.frame.loc[0, "actual"] = 99
    prepared.parsed.frame.loc[0, "actual"] = "99"
    prepared.operations.append("external change")
    quality.interval_stats["median_s"] = 99
    performance.config.tail_fraction = 0.3
    conditions.controller = "external change"
    assert snapshot.performance.model_dump(mode="json") == original
    assert snapshot.prepared.frame.loc[0, "actual"] == 0
    assert snapshot.prepared.parsed.frame.loc[0, "actual"] == "0.0"
    assert snapshot.conditions.controller == "Kp=1"
    assert snapshot.performance.source_label == "解析函数合成数据，不是实测"
    assert compare(snapshot, snapshot).a.source_label == snapshot.performance.source_label


@pytest.mark.parametrize("change", ["raw_bytes", "raw_frame", "prepared_frame", "quality", "config", "range", "numeric_result", "source"])
def test_capture_rejects_source_or_configuration_changes_and_old_results(change):
    prepared, quality, performance = context()
    if change == "raw_bytes":
        prepared.parsed.raw_bytes += b"\n"
    elif change == "raw_frame":
        prepared.parsed.frame.loc[0, "actual"] = "99"
    elif change == "prepared_frame":
        prepared.frame.loc[0, "actual"] = 99
    elif change == "quality":
        quality.interval_stats["median_s"] *= 2
    elif change == "config":
        performance.config.tail_fraction = 0.2
    elif change == "range":
        performance.end_row -= 1
    elif change == "numeric_result":
        performance.tracking.metrics["iae"].value = 123.0
    elif change == "source":
        performance.source.filename = "different.csv"
    with pytest.raises(ValueError):
        capture_experiment(prepared, quality, performance, "A")


def test_capture_requires_confirmed_applicable_single_step():
    prepared, quality, performance = context()
    tracking_only = analyze_performance(prepared, quality, 1, len(prepared.frame), performance.config, source_label="解析函数合成数据")
    with pytest.raises(ValueError, match="确认一个单次阶跃"):
        capture_experiment(prepared, quality, tracking_only, "A")
    invalid_step = performance.step.model_copy(update={"r1": 3.0})
    invalid = analyze_performance(prepared, quality, 1, len(prepared.frame), performance.config, source_label="解析函数合成数据", step=invalid_step, step_start_row=1, step_end_row=len(prepared.frame))
    with pytest.raises(ValueError, match="不适用"):
        capture_experiment(prepared, quality, invalid, "A")


def test_snapshot_identity_tracks_name_conditions_and_later_tampering_is_rejected():
    prepared, quality, performance = context()
    first = capture_experiment(prepared, quality, performance, "A", known_conditions())
    same = capture_experiment(prepared, quality, performance, "A", known_conditions())
    renamed = capture_experiment(prepared, quality, performance, "B", known_conditions())
    changed_conditions = capture_experiment(prepared, quality, performance, "A", known_conditions(controller="Kp=2"))
    assert first.snapshot_id == same.snapshot_id
    assert len({first.snapshot_id, renamed.snapshot_id, changed_conditions.snapshot_id}) == 3
    first.conditions.controller = "mutated"
    with pytest.raises(ValueError, match="快照.*已修改"):
        compare(first, same)


def test_a_snapshot_cannot_be_substituted_with_a_mutated_prepared_frame():
    experiment = saved()
    experiment.prepared.frame.loc[3, "actual"] = 0.2
    with pytest.raises(ValueError, match="准备数据"):
        available_duration(experiment)


def test_full_available_duration_does_not_drop_last_sample_due_to_cancellation():
    raw = b"time,target,actual\n-0.9,0,0\n-0.8,0,0\n-0.7,1,0\n-0.6,1,0\n-0.5,1,0\n-0.4,1,0\n-0.3,1,0\n-0.2,1,0\n-0.1,1,0\n0,1,0\n0.1,1,0\n"
    prepared = prepare_experiment(
        parse_csv(raw, "decimal.csv"),
        MappingConfig(fields={"time": "time", "target": "target", "actual": "actual"}, time_unit="s", quantity="响应量", unit="1", confirmed=True, units_consistent=True),
    )
    quality = check_quality(prepared)
    config = MetricConfig(reference_interval_s=quality.interval_stats["median_s"])
    performance = analyze_performance(
        prepared, quality, 1, 11, config, source_label="解析函数合成数据",
        step=StepSelection(t0=-0.7, r0=0.0, r1=1.0, y0=0.0, y0_source="manual", confirmed=True),
        step_start_row=1, step_end_row=11,
    )
    experiment = capture_experiment(prepared, quality, performance, "decimal")
    result = compare(experiment, experiment, duration=available_duration(experiment))
    assert result.a.end_row == result.b.end_row == 11
    assert result.a.end_s == result.b.end_s == 0.1
    assert differences(result)["iae"].delta == 0.0
