"""指标输入和追溯边界；全部数据为直接定义的数学测试夹具。"""

import math

import numpy as np
import pandas as pd
import pytest

from core.metric_models import MetricConfig, StepSelection
from core.metrics import STEP_KEYS, TRACKING_KEYS, compute_step, compute_tracking


def frame_fixture(times=(0, 1, 2, 3), actual=(0, 0.5, 1, 1), target=1, index=None):
    return pd.DataFrame({"time": times, "target": target, "actual": actual}, index=index)


def step_fixture(**changes):
    arguments = dict(t0=0.0, r0=0.0, r1=1.0, y0=0.0, y0_source="manual", confirmed=True)
    arguments.update(changes)
    return StepSelection(**arguments)


def unavailable(result, expected="invalid_data"):
    assert all(metric.status.value == expected for metric in result.metrics.values())
    assert all(metric.value is None and metric.reason for metric in result.metrics.values())
    result.model_dump_json()


@pytest.mark.parametrize("field", ["control", "p_term", "i_term", "d_term", "error"])
def test_all_mapped_columns_are_checked_before_computing(field):
    frame = frame_fixture()
    frame[field] = [0, 0, np.nan, 0]
    unavailable(compute_tracking(frame))
    unavailable(compute_step(frame, step_fixture()))


@pytest.mark.parametrize("index", [[0, 1, 3, 4], [0, 1, 1, 2], [0.0, 1.0, 2.0, 3.0], [-1, 0, 1, 2]])
def test_indices_must_remain_contiguous_nonnegative_original_integer_rows(index):
    frame = frame_fixture(index=index)
    unavailable(compute_tracking(frame))
    unavailable(compute_step(frame, step_fixture()))


def test_integer_index_dtype_cannot_wrap_and_hide_original_row_discontinuity():
    index = pd.Index(np.array([126, 127, -128, -127], dtype=np.int8))
    unavailable(compute_tracking(frame_fixture(index=index)))


def test_complex_signal_is_not_silently_cast_to_its_real_part():
    frame = frame_fixture()
    frame["actual"] = [0j, 0.5j, 1j, 1j]
    unavailable(compute_tracking(frame))
    unavailable(compute_step(frame, step_fixture()))


def test_external_quality_reference_prevents_renormalizing_a_gapped_subinterval():
    config = MetricConfig(reference_interval_s=0.1)
    unavailable(compute_tracking(frame_fixture(), config))
    unavailable(compute_step(frame_fixture(), step_fixture(), config))


def test_missing_required_field_is_invalid():
    frame = frame_fixture().drop(columns="actual")
    unavailable(compute_tracking(frame))
    unavailable(compute_step(frame, step_fixture()))


def test_empty_and_one_point_frames_do_not_manufacture_zero_results():
    for count in (0, 1):
        frame = frame_fixture().iloc[:count]
        unavailable(compute_tracking(frame), "insufficient_data")
        unavailable(compute_step(frame, step_fixture()), "insufficient_data")


def test_large_finite_values_are_checked_per_metric_when_squaring_overflows():
    frame = frame_fixture(actual=[-1e200] * 4, target=0)
    result = compute_tracking(frame)
    assert result.metrics["rmse_t"].status.value == "invalid_data"
    assert result.metrics["rmse_t"].value is None
    assert result.metrics["max_abs_error"].value == 1e200
    assert result.metrics["iae"].value == pytest.approx(3e200)
    assert result.metrics["tail_bias"].value == pytest.approx(1e200)
    result.model_dump_json()


def test_normalization_overflow_is_explicit_without_numeric_exception():
    frame = frame_fixture(actual=[1e308] * 4, target=1e-7)
    unavailable(compute_step(frame, step_fixture(r1=1e-7)))


def test_time_span_overflow_is_invalid_even_when_each_adjacent_delta_is_finite():
    frame = frame_fixture(times=[-1e308, 0, 1e308], actual=[0, 0.5, 1])
    unavailable(compute_tracking(frame))
    unavailable(compute_step(frame, step_fixture()))


def test_unrepresentably_small_tail_window_is_explicitly_unavailable():
    frame = frame_fixture(times=[1e9, 1e9 + 1], actual=[0, 1])
    result = compute_tracking(frame, MetricConfig(tail_fraction=1e-20))
    assert result.metrics["tail_bias"].status.value == "invalid_data"
    assert result.metrics["tail_bias"].value is None
    assert result.metrics["rmse_t"].status.value == "success"


@pytest.mark.parametrize("t0", [-1.0, 4.0])
def test_manual_t0_outside_observed_frame_is_not_extrapolated(t0):
    unavailable(compute_step(frame_fixture(), step_fixture(t0=t0)))


def test_a_single_observation_at_last_timestamp_is_insufficient():
    frame = frame_fixture(target=[0, 0, 0, 1])
    unavailable(compute_step(frame, step_fixture(t0=3.0)), "insufficient_data")


def test_pre_step_target_must_agree_with_declared_old_plateau():
    frame = frame_fixture(target=[0, 0.5, 1, 1])
    unavailable(compute_step(frame, step_fixture(t0=2.0)), "not_applicable")


def test_step_markers_are_absolute_and_all_observation_context_is_recorded():
    frame = frame_fixture(times=[7, 8, 9, 10])
    result = compute_step(frame, step_fixture(t0=7.0), unit="rad/s")
    assert result.markers == pytest.approx({
        "t0": 7, "t_lower": 7.2, "t_upper": 8.8, "t_settle": 8.96,
        "y_lower": 0.1, "y_upper": 0.9, "band_lower": 0.98, "band_upper": 1.02,
    })
    assert result.metrics["settling_time"].value == pytest.approx(1.96)
    assert result.details["observation_start_s"] == 7
    assert result.details["observation_end_s"] == 10
    assert result.details["observation_duration_s"] == 3
    assert result.details["settled_observation_s"] == pytest.approx(1.04)
    assert result.details["required_observation_s"] == 0.5
    assert result.details["normalization_amplitude"] == 1
    assert result.details["target_unit"] == "rad/s"
    assert "相对于目标值" in result.details["normalization"]


def test_unreached_high_threshold_keeps_only_observed_lower_crossing():
    result = compute_step(frame_fixture(actual=[0, 0.2, 0.3, 0.4]), step_fixture())
    assert result.metrics["rise_time"].status.value == "not_reached"
    assert result.markers["t_lower"] == pytest.approx(0.5)
    assert "t_upper" not in result.markers
    assert "t_settle" not in result.markers


def test_candidate_band_entry_can_be_recorded_without_claiming_sufficient_observation():
    result = compute_step(frame_fixture(times=[0, 0.25, 0.5], actual=[0, 0.5, 1]), step_fixture())
    assert result.metrics["settling_time"].status.value == "insufficient_data"
    assert result.markers["t_settle"] == pytest.approx(0.49)
    assert result.details["settled_observation_s"] == pytest.approx(0.01)


def test_starting_inside_configured_band_can_have_true_zero_settling_time():
    result = compute_step(frame_fixture(), step_fixture(), MetricConfig(absolute_tolerance=1.0))
    assert result.metrics["settling_time"].status.value == "success"
    assert result.metrics["settling_time"].value == 0
    assert result.markers["t_settle"] == 0


def test_tail_trace_includes_actual_boundaries_and_original_row_range():
    frame = frame_fixture(times=[1, 2, 3, 4], index=[30, 31, 32, 33])
    before = frame.copy(deep=True)
    result = compute_tracking(frame, unit="V")
    assert result.details["interval_start_s"] == 1
    assert result.details["interval_end_s"] == 4
    assert result.details["tail_start_s"] == pytest.approx(3.7)
    assert result.details["tail_end_s"] == 4
    assert result.details["tail_duration_s"] == pytest.approx(0.3)
    assert result.details["start_row"] == 31
    assert result.details["end_row"] == 34
    pd.testing.assert_frame_equal(frame, before)


def test_ui_display_names_are_chinese_while_dictionary_keys_are_stable():
    tracking = compute_tracking(frame_fixture())
    response = compute_step(frame_fixture(), step_fixture())
    assert tuple(tracking.metrics) == TRACKING_KEYS
    assert tuple(response.metrics) == STEP_KEYS
    assert tracking.metrics["tail_bias"].name == "末段平均偏差"
    assert response.metrics["settling_time"].name == "目标调节时间"


def test_nonempty_units_are_required_and_never_replaced_with_guessed_units():
    unavailable(compute_tracking(frame_fixture(), unit=" "))
    unavailable(compute_step(frame_fixture(), step_fixture(), unit=" "))


def test_unknown_configuration_is_reported_without_calculating():
    unavailable(compute_tracking(frame_fixture(), config={"misspelling": 1}))
    unavailable(compute_step(frame_fixture(), step_fixture(), config={"misspelling": 1}))


def test_every_successful_value_and_every_marker_is_finite():
    for result in (compute_tracking(frame_fixture()), compute_step(frame_fixture(), step_fixture())):
        assert all(math.isfinite(metric.value) for metric in result.metrics.values())
        assert all(math.isfinite(value) for value in result.markers.values())
        result.model_dump_json()


def test_crossing_difference_overflow_cannot_be_misreported_as_zero_rise_time():
    frame = frame_fixture(
        times=[0, 1, 2, 3, 4, 5],
        target=[0, 0, 1, 1, 1, 1],
        actual=[0, 0, -1e308, 1e308, 1, 1],
    )
    result = compute_step(frame, step_fixture(t0=2.0))
    rise = result.metrics["rise_time"]
    assert rise.status.value == "invalid_data"
    assert rise.value is None and rise.reason
    assert "t_lower" not in result.markers
    assert "t_upper" not in result.markers
    result.model_dump_json()


def test_band_entry_difference_overflow_cannot_be_misreported_as_zero_settling_time():
    frame = frame_fixture(actual=[-1.7e308, 1e308, 1, 1])
    result = compute_step(frame, step_fixture(), MetricConfig(absolute_tolerance=1e308))
    settling = result.metrics["settling_time"]
    assert settling.status.value == "invalid_data"
    assert settling.value is None and settling.reason
    assert "t_settle" not in result.markers
    assert result.details["settled_observation_s"] is None
    result.model_dump_json()
