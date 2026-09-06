"""独立验证平台推荐、阶跃边界与时间加权基线证据。"""

import numpy as np
import pandas as pd
import pytest

from core.metric_models import MetricConfig
from core.models import ResultStatus
from core.steps import discover_steps, estimate_baseline


def frame(target, actual=None, time=None, start_index=0):
    target = np.asarray(target, dtype=float)
    return pd.DataFrame(
        {
            "time": np.arange(len(target), dtype=float) / 10 if time is None else time,
            "target": target,
            "actual": target.copy() if actual is None else actual,
        },
        index=pd.RangeIndex(start_index, start_index + len(target)),
    )


@pytest.mark.parametrize("r0,r1", [(0.0, 1.0), (0.0, -1.0), (5.0, 7.0), (5.0, 2.0)])
def test_constant_platforms_produce_signed_step_with_original_rows(r0, r1):
    data = frame([r0] * 5 + [r1] * 6, start_index=100)
    candidates = discover_steps(data)
    assert len(candidates) == 1
    candidate = candidates[0]
    assert (candidate.pre_start_row, candidate.step_row, candidate.end_row) == (101, 106, 111)
    assert (candidate.r0, candidate.r1) == (r0, r1)
    assert candidate.t0 == pytest.approx(0.5)
    assert candidate.previous_sample_s == pytest.approx(0.4)


def test_multiple_steps_have_independent_platform_and_observation_ranges():
    candidates = discover_steps(frame([0] * 4 + [1] * 4 + [-2] * 4))
    assert [(item.pre_start_row, item.step_row, item.end_row) for item in candidates] == [(1, 5, 8), (5, 9, 12)]
    assert [(item.r0, item.r1) for item in candidates] == [(0, 1), (1, -2)]


@pytest.mark.parametrize("target", [
    [3] * 12,
    list(np.arange(12) / 10),
    [0, 1] * 8,
    [0, 1, 1, 1],
    [0, 0, 0, 1],
    [0, 0, 0],
])
def test_static_ramp_noise_and_missing_platform_evidence_have_no_candidate(target):
    assert discover_steps(frame(target)) == []


def test_small_drift_cannot_form_reliable_platform_before_large_jump():
    target = list(np.arange(40) * 1e-7) + [1] * 10
    assert discover_steps(frame(target)) == []


def test_target_noise_within_platform_tolerance_is_not_a_separate_step():
    noise = np.array([0, 3e-7, -3e-7, 0])
    candidates = discover_steps(frame(np.concatenate((noise, 1 + noise))))
    assert len(candidates) == 1
    assert candidates[0].step_row == 5
    assert candidates[0].r0 == 0
    assert candidates[0].r1 == 1


def test_near_zero_step_and_jump_indistinguishable_from_platform_tolerance_are_not_recommended():
    assert discover_steps(frame([0] * 4 + [0.1] * 4), MetricConfig(amplitude_epsilon=0.2)) == []
    assert discover_steps(frame([0] * 4 + [1e-6] * 4)) == []


def test_configurable_zero_target_tolerance_detects_small_exact_step():
    candidates = discover_steps(frame([0] * 4 + [1e-6] * 4), MetricConfig(target_flat_tolerance=0.0))
    assert len(candidates) == 1


@pytest.mark.parametrize("damage", ["nan", "infinity", "duplicate_time", "reversal", "gap", "index_gap", "index_string", "index_negative", "missing_field"])
def test_invalid_data_cannot_produce_step_or_baseline(damage):
    data = frame([0] * 5 + [1] * 5)
    if damage == "nan":
        data.loc[1, "actual"] = np.nan
    elif damage == "infinity":
        data.loc[1, "target"] = np.inf
    elif damage == "duplicate_time":
        data.loc[2, "time"] = data.loc[1, "time"]
    elif damage == "reversal":
        data.loc[2, "time"] = -1
    elif damage == "gap":
        data.loc[5:, "time"] += 10
    elif damage == "index_gap":
        data.index = [0, 1, 2, 4, 5, 6, 7, 8, 9, 10]
    elif damage == "index_string":
        data.index = data.index.astype(str)
    elif damage == "index_negative":
        data.index = data.index - 1
    elif damage == "missing_field":
        data = data.drop(columns="actual")
    assert discover_steps(data) == []
    baseline = estimate_baseline(data, 0.5, 0.0)
    assert baseline.status == ResultStatus.INVALID_DATA
    assert baseline.value is None
    assert baseline.reason


def test_reference_interval_prevents_locally_uniform_sparse_data_bypassing_rule():
    data = frame([0] * 5 + [1] * 5, time=np.arange(10, dtype=float))
    cfg = MetricConfig(reference_interval_s=0.1)
    assert discover_steps(data, cfg) == []
    assert estimate_baseline(data, 5.0, 0.0, cfg).status == ResultStatus.INVALID_DATA


@pytest.mark.parametrize("y0", [0.0, 2.5, -3.0])
def test_constant_nonzero_or_true_zero_baseline_is_measured_from_pre_samples(y0):
    data = frame([0] * 10 + [1] * 5, actual=[y0] * 10 + [100] * 5, start_index=50)
    baseline = estimate_baseline(data, 1.0, 0.0)
    assert baseline.status == ResultStatus.SUCCESS
    assert baseline.value == pytest.approx(y0)
    assert (baseline.start_row, baseline.end_row) == (56, 60)
    assert (baseline.start_s, baseline.end_s) == pytest.approx((0.5, 0.9))


def test_nonuniform_baseline_uses_independent_known_time_integral():
    data = frame([0, 0, 0, 0, 1, 1], actual=[2, 2.01, 2, 2, 3, 3], time=[0, 0.1, 0.3, 0.45, 0.7, 1.0])
    cfg = MetricConfig(baseline_window_s=1.0, min_interval_ratio=0.49)
    baseline = estimate_baseline(data, 0.7, 0.0, cfg)
    assert baseline.status == ResultStatus.SUCCESS
    # Independently: .1*(2+2.01)/2 + .2*(2.01+2)/2 + .15*(2+2)/2 = .9015.
    assert baseline.value == pytest.approx(0.9015 / 0.45, rel=1e-14)
    assert baseline.value != pytest.approx((2 + 2.01 + 2 + 2) / 4, rel=1e-6)
    assert baseline.end_s == 0.45


def test_baseline_does_not_extend_to_step_or_include_poststep_values():
    data = frame([0, 0, 0, 0, 1, 1], actual=[2, 2, 2, 2.01, 100, 100])
    baseline = estimate_baseline(data, 0.4, 0.0)
    assert baseline.status == ResultStatus.SUCCESS
    assert baseline.end_s == 0.3
    assert baseline.value == pytest.approx((0.2 + 0.2 + 0.2005) / 0.3)


def test_baseline_failure_retains_none_when_there_is_no_pre_step_data():
    baseline = estimate_baseline(frame([1] * 5), 0.0, 0.0)
    assert baseline.status == ResultStatus.INSUFFICIENT_DATA
    assert baseline.value is None
    assert baseline.start_s is None
    assert "不能默认用零" in baseline.reason


def test_baseline_minimum_count_and_duration_are_independent_requirements():
    data = frame([0] * 5 + [1] * 5)
    count_result = estimate_baseline(data, 0.5, 0.0, MetricConfig(baseline_min_points=6))
    assert count_result.status == ResultStatus.INSUFFICIENT_DATA
    assert "至少需要 6" in count_result.reason
    span_result = estimate_baseline(data, 0.5, 0.0, MetricConfig(baseline_min_duration_s=0.45))
    assert span_result.status == ResultStatus.INSUFFICIENT_DATA
    assert "时间跨度" in span_result.reason
    assert count_result.value is span_result.value is None


def test_drifting_actual_values_cannot_be_declared_a_baseline():
    data = frame([0] * 5 + [1] * 5, actual=[0, 0.03, 0.06, 0.09, 0.12, 1, 1, 1, 1, 1])
    baseline = estimate_baseline(data, 0.5, 0.0)
    assert baseline.status == ResultStatus.INSUFFICIENT_DATA
    assert baseline.value is None
    assert "峰峰差" in baseline.reason


def test_previous_target_step_cannot_supply_baseline_samples():
    data = frame([0, 0, 0, 1, 1, 2, 2, 2], actual=[0] * 8)
    baseline = estimate_baseline(data, 0.5, 1.0)
    assert baseline.status == ResultStatus.INSUFFICIENT_DATA
    assert "先前阶跃" in baseline.reason
    # Explicitly bounded current plateau has only 2 pre samples, so default also refuses it.
    bounded = estimate_baseline(data.iloc[3:], 0.5, 1.0)
    assert bounded.status == ResultStatus.INSUFFICIENT_DATA
    assert "只有 2" in bounded.reason


def test_current_platform_can_be_explicitly_bounded_without_old_step():
    data = frame([0, 0, 0, 1, 1, 1, 1, 2, 2], actual=[0] * 9)
    bounded = estimate_baseline(data.iloc[3:], 0.7, 1.0)
    assert bounded.status == ResultStatus.SUCCESS
    assert bounded.start_row == 4
    assert bounded.end_row == 7


@pytest.mark.parametrize("t0,r0", [(-0.1, 0), (2.0, 0), (float("nan"), 0), (0.5, float("inf")), (True, 0), ("0.5", 0)])
def test_invalid_manual_time_or_reference_has_explicit_failure(t0, r0):
    result = estimate_baseline(frame([0] * 5 + [1] * 5), t0, r0)
    assert result.status == ResultStatus.INVALID_DATA
    assert result.value is None


def test_empty_and_single_row_are_insufficient_data():
    for data in [frame([]), frame([0])]:
        assert discover_steps(data) == []
        result = estimate_baseline(data, 0.0, 0.0)
        assert result.status == ResultStatus.INSUFFICIENT_DATA
        assert result.value is None


def test_step_recommendation_and_baseline_never_modify_input():
    data = frame([0] * 10 + [1] * 10, actual=[0] * 20)
    original = data.copy(deep=True)
    discover_steps(data)
    estimate_baseline(data, 1.0, 0.0)
    pd.testing.assert_frame_equal(data, original)


def test_very_large_constant_finite_baseline_avoids_intermediate_overflow():
    data = frame([0] * 5 + [1] * 5, actual=[1e308] * 10)
    result = estimate_baseline(data, 0.5, 0.0)
    assert result.status == ResultStatus.SUCCESS
    assert result.value == pytest.approx(1e308)


def test_decimal_window_boundary_keeps_the_closed_left_sample():
    data = frame([0] * 8 + [1] * 2, actual=[0] * 10)
    result = estimate_baseline(data, 0.8, 0.0, MetricConfig(baseline_min_points=5))
    assert result.status == ResultStatus.SUCCESS
    assert result.start_s == 0.3
    assert result.start_row == 4


def test_decimal_duration_and_range_boundaries_tolerate_rounding_only():
    data = frame([0, 0, 0, 1, 1], actual=[2, 2, 2.02, 3, 3], time=[0.6, 0.65, 0.7, 0.75, 0.8])
    result = estimate_baseline(data, 0.75, 0.0)
    assert result.status == ResultStatus.SUCCESS
    assert result.start_s == 0.6
    assert result.end_s == 0.7
    data.loc[2, "actual"] = 2.020001
    assert estimate_baseline(data, 0.75, 0.0).status == ResultStatus.INSUFFICIENT_DATA


def test_nullable_index_and_extreme_integer_inputs_fail_without_exception():
    data = frame([0] * 5 + [1] * 5)
    assert estimate_baseline(data, 10**400, 0.0).status == ResultStatus.INVALID_DATA
    data.index = pd.Index([0, 1, 2, pd.NA, 4, 5, 6, 7, 8, 9], dtype="Int64")
    assert discover_steps(data) == []
    assert estimate_baseline(data, 0.5, 0.0).status == ResultStatus.INVALID_DATA
