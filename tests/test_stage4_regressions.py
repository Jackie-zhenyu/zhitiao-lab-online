"""第四阶段独立验收：原生时间窗口、数学差值与现象证据的确认边界。"""

import math

import numpy as np
import pandas as pd
import pytest
from pydantic import ValidationError

from core.comparison import available_duration, capture_experiment, compare_experiments
from core.comparison_models import ComparisonConfig, ExperimentConditions
from core.event_models import EventConfig, OutputLimits
from core.events import detect_events
from core.import_models import MappingConfig, QualityConfig
from core.metric_models import MetricConfig, StepSelection
from core.models import SourceKind
from core.parser import parse_csv
from core.performance import analyze_performance
from core.quality import check_quality, prepare_experiment


def prepared_fixture(times, actual, target=0.0, *, control=None, unit="1", quantity="响应量", filename="stage4-math-fixture.csv", quality_config=None):
    values = {"time": times, "target": target, "actual": actual}
    if control is not None:
        values["control"] = control
    payload = pd.DataFrame(values).to_csv(index=False).encode("utf-8-sig")
    mapping = MappingConfig(
        fields={name: name for name in values}, time_unit="s", quantity=quantity, unit=unit,
        optional_units={"control": "1"} if control is not None else {},
        confirmed=True, units_consistent=True,
    )
    prepared = prepare_experiment(parse_csv(payload, filename), mapping, SourceKind.SIMULATED)
    return prepared, check_quality(prepared, quality_config)


def saved_fixture(relative_times, *, shift=0.0, errors=None, unit="1", quantity="响应量", name="A", config_changes=None, conditions=None):
    relative = np.asarray(relative_times, dtype=float)
    target = np.where(relative < 0, 0.0, 1.0)
    error = np.where(relative < 0, 0.0, 1.0) if errors is None else np.asarray(errors, dtype=float)
    prepared, quality = prepared_fixture(relative + shift, target - error, target, unit=unit, quantity=quantity, filename=f"{name}.csv")
    assert not quality.blocking
    config_values = {"reference_interval_s": quality.interval_stats["median_s"]}
    config_values.update(config_changes or {})
    metric_config = MetricConfig(**config_values)
    step = StepSelection(t0=shift, r0=0.0, r1=1.0, y0=0.0, y0_source="manual", confirmed=True)
    performance = analyze_performance(
        prepared, quality, 1, len(relative), metric_config,
        source_label="独立数学验收夹具，非实测", step=step,
        step_start_row=1, step_end_row=len(relative),
    )
    if conditions is None:
        conditions = ExperimentConditions(plant="同一对象", load="0", sampling="记录原始时间", environment="无扰动", controller="已记录PI参数")
    return capture_experiment(prepared, quality, performance, name, conditions)


def compare(a, b, duration):
    return compare_experiments(a, b, ComparisonConfig(common_duration_s=duration, confirmed=True))


def differences(result):
    return {item.key: item for item in result.differences}


def test_negative_step_time_alignment_and_nonuniform_post_only_integrals():
    # post t=[0,1,3], e=[1,2,2]：∫e²=10.5，∫|e|=5.5。
    a = saved_fixture([-1, 0, 1, 3], shift=-5.0, errors=[0, 1, 2, 2], name="negative-t0")
    b = saved_fixture([-1, 0, 1, 3], shift=5.0, errors=[0, 1, 2, 2], name="positive-t0")
    assert available_duration(a) == available_duration(b) == 3.0
    result = compare(a, b, 3.0)
    assert result.a.start_s == -5.0 and result.b.start_s == 5.0
    assert result.a.relative_start_s == result.b.relative_start_s == 0.0
    assert result.a.relative_end_s == result.b.relative_end_s == 3.0
    assert result.a.start_row == result.b.start_row == 2
    for side in (result.a, result.b):
        assert side.tracking.metrics["rmse_t"].value == pytest.approx(math.sqrt(3.5), rel=0, abs=1e-12)
        assert side.tracking.metrics["iae"].value == 5.5
    assert differences(result)["rmse_t"].delta == 0.0
    assert differences(result)["iae"].percent == 0.0


def test_common_request_keeps_distinct_native_endpoints_and_does_not_insert_points():
    a = saved_fixture([-0.5, 0.0, 0.7, 1.4, 2.1, 2.8, 3.5], name="native-a")
    b = saved_fixture([-0.5, 0.0, 0.6, 1.2, 1.8, 2.4, 3.0, 3.6], name="native-b")
    assert a.performance.config.reference_interval_s != b.performance.config.reference_interval_s
    result = compare(a, b, 2.5)
    assert result.a.relative_end_s == 2.1 and result.b.relative_end_s == 2.4
    assert result.a.end_row == 5 and result.b.end_row == 6
    assert result.a.tracking.metrics["iae"].value == 2.1
    assert result.b.tracking.metrics["iae"].value == 2.4
    assert differences(result)["iae"].delta == pytest.approx(0.3, abs=1e-14)
    assert next(item for item in result.checks if item.name == "metric_config").status == "match"
    assert next(item for item in result.checks if item.name == "native_window").status == "mismatch"
    assert result.descriptive_only
    assert any("未插入" in warning for warning in result.warnings)


@pytest.mark.parametrize("changes", [{"unit": "rad"}, {"quantity": "角位移"}, {"config_changes": {"tail_fraction": 0.2}}])
def test_incompatible_physical_or_evaluation_meaning_never_has_delta_or_percent(changes):
    a = saved_fixture([-1, 0, 1, 2, 3], name="compatible-base")
    b = saved_fixture([-1, 0, 1, 2, 3], name="incompatible", **changes)
    result = compare(a, b, 3.0)
    assert all(item.delta is None and item.percent is None and item.reason for item in result.differences)
    if "unit" in changes or "quantity" in changes:
        assert not result.can_overlay
    assert result.warnings and result.descriptive_only


def test_signed_tail_percentage_uses_absolute_a_denominator_without_ranking():
    a = saved_fixture([-1, 0, 1, 2, 3], errors=[0, -2, -2, -2, -2], name="negative-bias-a")
    b = saved_fixture([-1, 0, 1, 2, 3], errors=[0, -1, -1, -1, -1], name="negative-bias-b")
    item = differences(compare(a, b, 3.0))["tail_bias"]
    assert item.a.value == -2.0 and item.b.value == -1.0
    assert item.delta == 1.0 and item.percent == 50.0
    assert "不是提升" in item.reason


def test_self_comparison_keeps_true_zero_delta_and_empty_zero_denominator_percent():
    saved = saved_fixture([-1, 0, 1, 2, 3], errors=[0, 0, 0, 0, 0], name="self")
    result = compare(saved, saved, 3.0)
    items = differences(result)
    for key in ("rmse_t", "iae", "max_abs_error", "tail_bias", "overshoot_pct"):
        assert items[key].delta == 0.0
        assert items[key].percent is None
        assert "0" in items[key].reason
    assert items["rise_time"].delta is None and items["rise_time"].percent is None


def test_unknown_or_changed_conditions_only_allow_warned_description_not_pid_causality():
    a = saved_fixture([-1, 0, 1, 2, 3], name="conditions-a")
    b = saved_fixture(
        [-1, 0, 1, 2, 3], name="conditions-b",
        conditions=ExperimentConditions(plant="不同对象", load=None, sampling="记录原始时间", environment="无扰动", controller="不同PI参数"),
    )
    result = compare(a, b, 3.0)
    assert result.descriptive_only
    assert any(item.name == "plant" and item.status == "mismatch" for item in result.checks)
    assert any(item.name == "load" and item.status == "unknown" for item in result.checks)
    assert any("不能" in warning and "PID" in warning for warning in result.warnings)
    # 条件警告没有伪造数值，也未一律抹去有共同物理口径的描述性差值。
    assert differences(result)["rmse_t"].delta == 0.0


def test_comparison_cannot_extend_confirmed_observation_or_accept_too_few_native_points():
    saved = saved_fixture([-1, 0, 1, 2, 3], name="limited")
    with pytest.raises(ValueError, match="不能超过"):
        compare(saved, saved, 3.01)
    with pytest.raises(ValueError, match="不足两个"):
        compare(saved, saved, 0.5)


def test_output_limits_are_not_inferred_from_data_or_unconfirmed_defaults():
    prepared, quality = prepared_fixture(np.arange(6) * 0.2, np.zeros(6), control=np.full(6, 2.5))
    report = detect_events(prepared, quality)
    assert not any(item.category == "control_limit" for item in report.events)
    assert next(item for item in report.checks if item.rule == "control_limit").status == "not_applicable"
    with pytest.raises(ValidationError):
        OutputLimits(lower=-2.5, upper=2.5)
    with pytest.raises(ValidationError):
        OutputLimits(lower=-2.5, upper=2.5, confirmed=False)
    confirmed = detect_events(prepared, quality, EventConfig(output_limits=OutputLimits(lower=-2.5, upper=2.5, confirmed=True)))
    events = [item for item in confirmed.events if item.category == "control_limit"]
    assert len(events) == 1 and events[0].rule == "control_near_upper_limit"
    assert events[0].observations["duration_s"] == 1.0
    assert "无法" in events[0].undetermined_causes and "积分饱和" in events[0].undetermined_causes


def test_control_far_beyond_limit_is_not_relabelled_as_near_limit():
    prepared, quality = prepared_fixture(np.arange(6) * 0.2, np.zeros(6), control=np.full(6, 3.0))
    config = EventConfig(output_limits=OutputLimits(lower=-2.5, upper=2.5, confirmed=True))
    report = detect_events(prepared, quality, config)
    assert not any(item.category == "control_limit" for item in report.events)


def test_rate_limit_value_without_confirmation_remains_statistical_candidate():
    prepared, quality = prepared_fixture(np.arange(7, dtype=float), [0, 0, 0, 1, 0, 0, 0])
    report = detect_events(prepared, quality, EventConfig(rate_limit=100.0, rate_limit_confirmed=False))
    candidates = [item for item in report.events if item.category == "measurement_change"]
    assert len(candidates) == 2
    assert all(item.rule == "measurement_statistical_jump" for item in candidates)
    assert all("统计突变候选" in item.phenomenon for item in candidates)
    assert all(item.thresholds["physical_limit_confirmed"] is False for item in candidates)
    assert all("无法" in item.undetermined_causes and "传感器故障" in item.undetermined_causes for item in candidates)
    confirmed = detect_events(prepared, quality, EventConfig(rate_limit=100.0, rate_limit_confirmed=True))
    assert not any(item.category == "measurement_change" for item in confirmed.events)


def test_oscillation_outside_fraction_uses_time_not_number_of_samples():
    config = QualityConfig(min_interval_ratio=0.0001, max_interval_ratio=10000.0)
    # 五个死区外点只占前.05秒，即2.5%的窗口时间；不能按5/7样本命中。
    times = [0.0, 0.01, 0.02, 0.03, 0.04, 0.05, 2.0]
    prepared, quality = prepared_fixture(times, [0.1, -0.1, 0.1, -0.1, 0.1, 0.0, 0.0], quality_config=config)
    assert not quality.blocking
    report = detect_events(prepared, quality)
    assert not any(item.category == "oscillation" for item in report.events)


def test_oscillation_detects_long_outside_time_despite_many_deadband_samples():
    config = QualityConfig(min_interval_ratio=0.0001, max_interval_ratio=10000.0)
    # 5个死区外样本只占5/45点，却按左端保持占1.61/2=80.5%的时间。
    times = [0.0, 0.4, 0.8, 1.2, 1.6, *np.linspace(1.61, 1.99, 39), 2.0]
    actual = [0.1, -0.1, 0.1, -0.1, 0.1] + [0.0] * 40
    prepared, quality = prepared_fixture(times, actual, quality_config=config)
    assert not quality.blocking
    events = [item for item in detect_events(prepared, quality).events if item.category == "oscillation"]
    assert len(events) == 1
    assert events[0].observations["outside_time_fraction"] == pytest.approx(0.805, rel=0, abs=1e-12)
    assert events[0].observations["crossings"] == 4.0
    assert "不能确定" in events[0].undetermined_causes


def test_events_do_not_bridge_missing_time_and_preserve_unknown_time_locations():
    prepared, quality = prepared_fixture([0.0, 0.2, None, 0.6, 0.8, 1.0], [0.0] * 6, control=[2.5] * 6)
    config = EventConfig(output_limits=OutputLimits(lower=-2.5, upper=2.5, confirmed=True))
    report = detect_events(prepared, quality, config)
    assert not any(item.category == "control_limit" for item in report.events)
    records = [item for item in report.events if item.category == "data_record"]
    assert records
    assert all(item.start_s is None and item.end_s is None for item in records)
    missing = next(item for item in records if item.rule == "quality_missing_value")
    assert missing.start_row == missing.end_row == 3
    assert missing.observations["csv_line"] == 4.0
