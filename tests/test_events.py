"""确定性现象规则验收；全部夹具为解析函数合成或手工数学数据。"""

import numpy as np
import pandas as pd
import pytest
from pydantic import ValidationError

from core.event_models import EventConfig, OutputLimits
from core.events import detect_events
from core.import_models import MappingConfig, QualityConfig
from core.models import SourceKind
from core.parser import parse_csv
from core.quality import check_quality, prepare_experiment


def fixture(times, actual, target=0, control=None, quality_config=None):
    values = {"time": times, "target": target, "actual": actual}
    optional_units = {}
    if control is not None:
        values["control"] = control
        optional_units["control"] = "V"
    raw = pd.DataFrame(values).to_csv(index=False).encode("utf-8")
    parsed = parse_csv(raw, "synthetic-fixture.csv")
    mapping = MappingConfig(
        fields={key: key for key in values}, time_unit="s", quantity="位移", unit="m",
        optional_units=optional_units, confirmed=True, units_consistent=True,
    )
    prepared = prepare_experiment(parsed, mapping, SourceKind.SIMULATED)
    return prepared, check_quality(prepared, quality_config)


def output_config(**changes):
    return EventConfig(output_limits=OutputLimits(lower=-1.0, upper=1.0, confirmed=True), **changes)


def category(report, name):
    return [event for event in report.events if event.category == name]


def status(report, rule):
    return next(check.status for check in report.checks if check.rule == rule)


def test_sustained_sinusoid_has_original_interval_and_numeric_evidence():
    time = np.linspace(0, 6, 301)
    prepared, quality = fixture(time, 1 + 0.1 * np.sin(2 * np.pi * 3 * time), target=1)
    report = detect_events(prepared, quality)
    events = category(report, "oscillation")
    assert len(events) == 1
    event = events[0]
    assert event.start_s == prepared.frame.iloc[event.start_row - 1]["time"]
    assert event.end_s == prepared.frame.iloc[event.end_row - 1]["time"]
    assert event.end_s - event.start_s >= 2
    assert event.observations["peak_to_peak"] >= 0.19
    assert event.observations["crossings"] >= 4
    assert event.observations["matched_window_count"] > 1
    assert event.charts == ["response", "error"]
    assert "不能确定" in event.undetermined_causes
    assert status(report, "oscillation") == "checked"


def test_nonuniform_oscillation_fraction_uses_left_state_duration_not_sample_count():
    prepared, quality = fixture(
        [0, 0.1, 0.2, 0.3, 1.3, 2.0], [0.1, 0, -0.1, 0, 0.1, -0.1],
        quality_config=QualityConfig(min_interval_ratio=0.01, max_interval_ratio=20),
    )
    report = detect_events(prepared, quality, EventConfig(oscillation_min_crossings=2))
    event = category(report, "oscillation")[0]
    # 左端死区外的间隔为 .1、.1、.7 秒，总 .9/2=.45；样本数量则是4/6。
    assert event.observations["outside_time_fraction"] == pytest.approx(0.45)
    assert event.observations["outside_time_s"] == pytest.approx(0.9)
    assert event.observations["outside_time_fraction"] != pytest.approx(4 / 6)


def test_oscillation_fraction_threshold_uses_actual_time_weight():
    prepared, quality = fixture(
        [0, 0.1, 0.2, 0.3, 1.3, 2.0], [0.1, 0, -0.1, 0, 0.1, -0.1],
        quality_config=QualityConfig(min_interval_ratio=0.01, max_interval_ratio=20),
    )
    report = detect_events(prepared, quality, EventConfig(oscillation_min_crossings=2, oscillation_min_time_fraction=0.5))
    assert not category(report, "oscillation")


def test_monotonic_response_is_not_classified_as_oscillation():
    time = np.linspace(0, 6, 301)
    prepared, quality = fixture(time, 1 - np.exp(-time), target=1)
    assert not category(detect_events(prepared, quality), "oscillation")


def test_changing_target_is_not_an_applicable_stable_target_oscillation_window():
    time = np.linspace(0, 6, 301)
    prepared, quality = fixture(time, time + 0.1 * np.sin(20 * time), target=time)
    report = detect_events(prepared, quality)
    assert not category(report, "oscillation")
    assert status(report, "oscillation") == "not_applicable"


def test_deadband_discards_small_noise_crossings():
    time = np.linspace(0, 6, 301)
    prepared, quality = fixture(time, 0.005 * np.sin(20 * time))
    assert not category(detect_events(prepared, quality), "oscillation")


def test_control_near_limit_is_continuous_and_time_fraction_is_nonuniform():
    prepared, quality = fixture(
        [0, 0.2, 1.2, 1.4, 2.0], [0] * 5, control=[0, 1, 1, 0, 0],
        quality_config=QualityConfig(min_interval_ratio=0.05, max_interval_ratio=10),
    )
    event = category(detect_events(prepared, quality, output_config()), "control_limit")[0]
    assert (event.start_row, event.end_row) == (2, 3)
    assert event.start_s == 0.2 and event.end_s == 1.2
    assert event.observations["duration_s"] == pytest.approx(1)
    assert event.observations["valid_interval_near_time_fraction"] == pytest.approx(0.6)
    assert "积分饱和" not in event.phenomenon
    assert "无法据此确认积分饱和" in event.undetermined_causes


def test_both_limit_sides_are_distinct_and_far_beyond_limit_is_not_near():
    prepared, quality = fixture(
        np.arange(9, dtype=float), [0] * 9,
        control=[-1, -1, 0, 1, 1, 0, 10, 10, 10],
    )
    events = category(detect_events(prepared, quality, output_config()), "control_limit")
    assert {(event.start_row, event.end_row) for event in events} == {(1, 2), (4, 5)}
    assert {event.observations["limit_side"] for event in events} == {"lower", "upper"}
    assert len({event.event_id for event in events}) == 2


def test_near_limit_band_endpoints_are_included_without_expanding_configured_band():
    prepared, quality = fixture([0, 1, 2], [0, 0, 0], control=[0.98, 0.98, 0.98])
    config = EventConfig(output_limits=OutputLimits(lower=0.0, upper=1.0, confirmed=True))
    events = category(detect_events(prepared, quality, config), "control_limit")
    assert len(events) == 1
    assert events[0].observations["duration_s"] == 2


def test_control_spikes_are_not_merged_across_nonmatching_samples():
    prepared, quality = fixture([0, 1, 2, 3, 4], [0] * 5, control=[1, 0, 1, 0, 1])
    assert not category(detect_events(prepared, quality, output_config()), "control_limit")


def test_missing_control_or_missing_confirmed_limits_is_not_applicable():
    prepared, quality = fixture([0, 1, 2], [0, 0, 0])
    report = detect_events(prepared, quality, output_config())
    assert status(report, "control_limit") == "not_applicable"
    assert not category(report, "control_limit")
    prepared, quality = fixture([0, 1, 2], [0, 0, 0], control=[1, 1, 1])
    report = detect_events(prepared, quality)
    assert status(report, "control_limit") == "not_applicable"
    assert not category(report, "control_limit")


def test_unconfirmed_output_limit_structure_is_rejected():
    with pytest.raises(ValidationError):
        OutputLimits(lower=0.0, upper=1.0, confirmed=False)


def test_unconfirmed_physical_rate_uses_only_statistical_candidate_label():
    prepared, quality = fixture(np.arange(9), [0, 0, 0, 0, 1, 0, 0, 0, 0])
    report = detect_events(prepared, quality, EventConfig(rate_limit=0.01, rate_limit_confirmed=False))
    events = category(report, "measurement_change")
    assert len(events) == 2
    assert all(event.rule == "measurement_statistical_jump" for event in events)
    assert all("统计突变候选" in event.phenomenon for event in events)
    assert all(event.thresholds["physical_limit_confirmed"] is False for event in events)
    assert all("传感器故障" not in event.phenomenon for event in events)


def test_confirmed_rate_threshold_is_physical_and_not_limited_by_statistical_jump_floor():
    prepared, quality = fixture([0, 0.0001], [0, 0.001])
    report = detect_events(prepared, quality, EventConfig(rate_limit=5.0, rate_limit_confirmed=True))
    event = category(report, "measurement_change")[0]
    assert event.rule == "measurement_physical_rate_limit"
    assert event.thresholds["physical_limit_confirmed"] is True
    assert event.observations["absolute_rate"] == pytest.approx(10)
    assert event.observations["delta_t_s"] == 0.0001
    assert event.observations["absolute_jump"] == 0.001
    assert (event.start_row, event.end_row) == (1, 2)


def test_confirmed_rate_uses_actual_nonuniform_delta_time():
    time = np.array([0, 0.25, 0.75, 1.125])
    prepared, quality = fixture(time, time * 6)
    report = detect_events(prepared, quality, EventConfig(rate_limit=5.0, rate_limit_confirmed=True))
    events = category(report, "measurement_change")
    assert len(events) == 3
    assert all(event.observations["absolute_rate"] == pytest.approx(6) for event in events)


def test_zero_mad_still_requires_exceeding_median_and_absolute_jump_floor():
    prepared, quality = fixture(np.arange(8), [0, 1, 2, 3, 4, 10, 11, 12])
    events = category(detect_events(prepared, quality), "measurement_change")
    assert len(events) == 1
    assert events[0].observations["median_absolute_rate"] == 1
    assert events[0].observations["rate_mad"] == 0
    assert events[0].thresholds["rate_threshold"] == 1
    prepared, quality = fixture(np.arange(8), [0, 0, 0, 0.01, 0, 0, 0, 0])
    assert not category(detect_events(prepared, quality), "measurement_change")


def test_data_record_issue_keeps_exact_csv_location_and_does_not_claim_root_cause():
    prepared, quality = fixture([0, 1, 2, 3], [0, None, 0, 0])
    record = category(detect_events(prepared, quality), "data_record")[0]
    assert record.start_row == record.end_row == 2
    assert record.observations["csv_line"] == 3
    assert record.observations["field"] == "actual"
    assert record.observations["quality_code"] == "missing_value"
    assert record.start_s == record.end_s == 1
    assert "不能据此确定设备故障" in record.undetermined_causes


@pytest.mark.parametrize("times", [[0, None, 2, 3], [0, 1, 0, 1], [0, 1, 1, 2]])
def test_missing_duplicate_or_reset_clock_uses_rows_instead_of_inventing_timestamps(times):
    prepared, quality = fixture(times, [0, 0, 0, 0])
    events = category(detect_events(prepared, quality), "data_record")
    assert events
    assert all(event.start_s is None and event.end_s is None for event in events)
    assert all("仅使用原始数据行" in event.observations["time_location_reason"] for event in events)


def test_dynamic_windows_and_runs_never_cross_a_missing_signal_row():
    time = np.linspace(0, 6, 301)
    actual = 0.1 * np.sin(20 * time)
    actual[150] = np.nan
    prepared, quality = fixture(time, actual, control=np.ones(len(time)))
    report = detect_events(prepared, quality, output_config())
    dynamic = [event for event in report.events if event.category != "data_record"]
    assert dynamic
    assert all(not (event.start_row <= 151 <= event.end_row) for event in dynamic)
    assert len(category(report, "control_limit")) == 2


def test_time_reset_cannot_merge_two_control_runs_with_identical_timestamps():
    prepared, quality = fixture([0, 1, 2, 0, 1, 2], [0] * 6, control=[1] * 6)
    events = category(detect_events(prepared, quality, output_config()), "control_limit")
    assert {(event.start_row, event.end_row) for event in events} == {(1, 3), (4, 6)}


def test_event_ids_are_deterministic_and_change_with_content_or_rule_config():
    prepared, quality = fixture([0, 1, 2, 3], [0] * 4, control=[1] * 4)
    config = output_config()
    first = detect_events(prepared, quality, config)
    repeated = detect_events(prepared, quality, config)
    assert [event.event_id for event in first.events] == [event.event_id for event in repeated.events]
    changed_rule = detect_events(prepared, quality, output_config(control_min_duration_s=1.0))
    assert {event.event_id for event in first.events}.isdisjoint(event.event_id for event in changed_rule.events)
    changed_prepared, changed_quality = fixture([0, 1, 2, 3], [0, 0, 0.01, 0], control=[1] * 4)
    changed_content = detect_events(changed_prepared, changed_quality, config)
    assert {event.event_id for event in first.events}.isdisjoint(event.event_id for event in changed_content.events)


def test_stale_quality_report_cannot_generate_events_for_same_row_count_new_file():
    prepared, quality = fixture([0, 1, 2, 3], [0] * 4, control=[1] * 4)
    changed, _ = fixture([0, 1, 2, 3], [1] * 4, control=[1] * 4)
    report = detect_events(changed, quality, output_config())
    assert not report.events
    assert all(check.status == "invalid_data" for check in report.checks)


def test_detection_does_not_change_raw_bytes_raw_strings_or_analysis_frame():
    prepared, quality = fixture(np.arange(9), [0, 0, 0, 0, 1, 0, 0, 0, 0], control=[1] * 9)
    original = prepared.frame.copy(deep=True)
    raw_frame = prepared.parsed.frame.copy(deep=True)
    raw_bytes = prepared.parsed.raw_bytes
    report = detect_events(prepared, quality, output_config())
    pd.testing.assert_frame_equal(prepared.frame, original)
    pd.testing.assert_frame_equal(prepared.parsed.frame, raw_frame)
    assert prepared.parsed.raw_bytes == raw_bytes
    assert report.events
    report.model_dump_json()


def test_short_data_has_explicit_checks_instead_of_normality_claims():
    prepared, quality = fixture([0, 0.1], [0, 0])
    report = detect_events(prepared, quality)
    assert status(report, "oscillation") == "insufficient_data"
    assert status(report, "measurement_change") == "insufficient_data"
    assert status(report, "control_limit") == "not_applicable"
    assert not report.events


def test_no_event_is_not_presented_as_proof_of_normal_operation():
    prepared, quality = fixture(np.arange(8), [0] * 8)
    report = detect_events(prepared, quality)
    assert not report.events
    assert any("不等于系统正常" in check.reason for check in report.checks)


def test_rate_and_peak_to_peak_overflow_are_explicit_invalid_checks():
    prepared, quality = fixture([0, 1, 2, 3], [-1e308, 1e308, 1, 1])
    report = detect_events(prepared, quality)
    assert status(report, "oscillation") == "invalid_data"
    assert status(report, "measurement_change") == "invalid_data"
    assert not category(report, "measurement_change")
    report.model_dump_json()


def test_output_limit_span_overflow_does_not_create_nonfinite_event():
    prepared, quality = fixture([0, 1, 2, 3], [0] * 4, control=[1] * 4)
    config = EventConfig(output_limits=OutputLimits(lower=-1e308, upper=1e308, confirmed=True))
    report = detect_events(prepared, quality, config)
    assert status(report, "control_limit") == "invalid_data"
    assert not category(report, "control_limit")
    report.model_dump_json()
