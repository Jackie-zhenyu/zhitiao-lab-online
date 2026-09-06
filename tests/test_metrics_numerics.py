"""独立数学验收：解析常量、手算梯形面积及交点，不调用实现生成期望。"""

import math

import numpy as np
import pandas as pd
import pytest

from core.metric_models import MetricConfig, StepSelection
from core.metrics import compute_step, compute_tracking


TRACKING_KEYS = ("rmse_t", "iae", "max_abs_error", "tail_bias")
STEP_KEYS = ("rise_time", "overshoot_pct", "settling_time")


def frame_fixture(times, actual, target=1.0, *, index=None):
    """只造明确标注于本测试文件的数学夹具，不代表实测数据。"""
    return pd.DataFrame({"time": times, "target": target, "actual": actual}, index=index)


def step_fixture(**changes):
    values = dict(t0=0.0, r0=0.0, r1=1.0, y0=0.0, y0_source="manual", confirmed=True)
    values.update(changes)
    return StepSelection(**values)


def assert_value(computation, key, expected, *, absolute=1e-12):
    metric = computation.metrics[key]
    assert metric.status.value == "success", metric.reason
    assert math.isfinite(metric.value)
    assert metric.value == pytest.approx(expected, rel=0, abs=absolute)


def assert_unavailable(computation, keys, status):
    for key in keys:
        metric = computation.metrics[key]
        assert metric.status.value == status, (key, metric.status, metric.reason)
        assert metric.value is None
        assert metric.reason and metric.reason.strip()


@pytest.mark.parametrize(
    "r0,r1,y0",
    [(0.0, 1.0, 0.0), (5.0, 1.0, 5.0), (0.0, 10.0, 2.0)],
    ids=["positive_step", "negative_step", "baseline_differs_from_old_target"],
)
def test_first_order_response_matches_independent_closed_form(r0, r1, y0):
    # tau=1: t90-t10=ln(9), 2% 最终入带时间=-ln(.02)。
    # 对指数响应，线性交点误差为 O(h²)；两个交点之差使用 h² 严格上界。
    h = 0.001
    times = np.arange(-500, 6001, dtype=float) * h
    q = np.where(times < 0, 0.0, 1.0 - np.exp(-np.maximum(times, 0.0)))
    frame = frame_fixture(times, y0 + (r1 - y0) * q, np.where(times < 0, r0, r1))
    result = compute_step(frame, step_fixture(r0=r0, r1=r1, y0=y0))
    assert_value(result, "rise_time", math.log(9), absolute=h * h)
    assert_value(result, "settling_time", -math.log(0.02), absolute=h * h)
    assert_value(result, "overshoot_pct", 0.0)


def test_nonuniform_first_order_uses_timestamps_for_crossings():
    intervals = np.tile([0.001, 0.0015], 2500)
    times = np.r_[0.0, np.cumsum(intervals)]
    frame = frame_fixture(times, 1.0 - np.exp(-times))
    result = compute_step(frame, step_fixture())
    # 默认间隔规则接受 1:1.5 的非等间隔；期望来自解析式，不是采样索引。
    assert_value(result, "rise_time", math.log(9), absolute=0.0015**2)
    assert_value(result, "settling_time", -math.log(0.02), absolute=0.0015**2)


def test_linear_crossings_use_r1_minus_y0_not_target_jump_or_tail_value():
    # y0=2,r0=0,r1=10，响应阈值为2.8/9.2；q=.5t，两交点为.2/1.8。
    times = [-1.0, 0.0, 0.4, 1.0, 1.6, 2.0, 2.8, 3.2, 4.0]
    q = np.array([0.0, 0.0, 0.2, 0.5, 0.8, 1.0, 1.0, 1.0, 1.0])
    frame = frame_fixture(times, 2.0 + 8.0 * q, [0.0] + [10.0] * 8)
    result = compute_step(frame, step_fixture(r0=0.0, r1=10.0, y0=2.0))
    assert_value(result, "rise_time", 1.6)
    assert_value(result, "settling_time", 1.96)


def test_custom_rise_thresholds_have_exact_linear_intersections():
    frame = frame_fixture([0.0, 1.0, 2.0, 3.0], [0.0, 0.5, 1.0, 1.0])
    result = compute_step(frame, step_fixture(), MetricConfig(rise_lower=0.2, rise_upper=0.8))
    assert_value(result, "rise_time", 1.2)


def test_nonzero_step_time_is_subtracted_from_settling_marker():
    times = np.array([-1.0, 0.0, 1.0, 2.0, 3.0]) + 7.0
    frame = frame_fixture(times, [0.0, 0.0, 0.5, 1.0, 1.0], [0.0, 1.0, 1.0, 1.0, 1.0])
    result = compute_step(frame, step_fixture(t0=7.0))
    assert_value(result, "rise_time", 1.6)
    assert_value(result, "settling_time", 1.96)


def test_manual_step_between_samples_interpolates_only_valid_boundary():
    frame = frame_fixture([0.0, 1.0, 2.0, 3.0, 4.0], [0.0, 0.2, 0.8, 1.0, 1.0], [0.0, 1.0, 1.0, 1.0, 1.0])
    before = frame.copy(deep=True)
    result = compute_step(frame, step_fixture(t0=0.25))
    # t0处q=.05；t10=.5，t90=2.5，最终入带t=2.9。
    assert_value(result, "rise_time", 2.0)
    assert_value(result, "settling_time", 2.65)
    pd.testing.assert_frame_equal(frame, before)


@pytest.mark.parametrize("y0,r1", [(0.0, 1.0), (5.0, 1.0)])
def test_sampled_overshoot_and_last_band_entry_are_direction_independent(y0, r1):
    q = np.array([0.0, 0.6, 1.2, 1.0, 1.0])
    frame = frame_fixture([0.0, 1.0, 2.0, 3.0, 4.0], y0 + (r1 - y0) * q, r1)
    result = compute_step(frame, step_fixture(r0=y0, r1=r1, y0=y0))
    assert_value(result, "overshoot_pct", 20.0)
    # 最后带外q=1.2到q=1之间，q=1.02的交点为2.9。
    assert_value(result, "settling_time", 2.9)


def test_absolute_band_uses_maximum_and_not_sum_of_tolerances():
    frame = frame_fixture([0.0, 1.0, 2.0, 3.0, 4.0], [0.0, 0.5, 0.8, 1.0, 1.0])
    config = MetricConfig(relative_band=0.02, absolute_tolerance=0.1)
    result = compute_step(frame, step_fixture(), config)
    # 带宽max(.02,.1)=.1，实测达到.9于t=2.5；若误用相加将得到2.4。
    assert_value(result, "settling_time", 2.5)


def test_settling_requires_final_stay_not_an_earlier_half_second_window():
    frame = frame_fixture(np.arange(7, dtype=float), [0.0, 1.0, 1.0, 1.1, 1.0, 1.0, 1.0])
    result = compute_step(frame, step_fixture())
    assert_value(result, "settling_time", 3.8)


def test_final_sample_outside_band_prevents_settling():
    frame = frame_fixture(np.arange(7, dtype=float), [0.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.1])
    result = compute_step(frame, step_fixture())
    assert_unavailable(result, ["settling_time"], "not_reached")
    assert_value(result, "overshoot_pct", 10.0)


def test_exact_observation_duration_and_band_boundary_are_included():
    # .125、.875及.5均可由二进制精确表示，避免夹具自己引入舍入歧义。
    frame = frame_fixture([0.0, 1.0, 1.5], [0.0, 0.875, 1.0])
    result = compute_step(frame, step_fixture(), MetricConfig(relative_band=0.125))
    assert_value(result, "settling_time", 1.0)


@pytest.mark.parametrize("plateau,expected", [(0.98, 1.0), (1.02, 49.0 / 51.0)])
def test_default_two_percent_band_includes_both_computed_boundaries(plateau, expected):
    # 两边界分别为1-.02与1+.02；不能因先相减求绝对值的舍入而排除界线。
    frame = frame_fixture([0.0, 1.0, 2.0, 3.0], [0.0, plateau, plateau, plateau])
    result = compute_step(frame, step_fixture())
    assert_value(result, "settling_time", expected)


def test_short_record_reports_each_metric_independently():
    frame = frame_fixture([0.0, 0.25, 0.5], [0.0, 0.5, 1.0])
    result = compute_step(frame, step_fixture())
    assert_value(result, "rise_time", 0.4)
    assert_value(result, "overshoot_pct", 0.0)
    assert_unavailable(result, ["settling_time"], "insufficient_data")


def test_observation_start_above_low_threshold_does_not_invent_first_crossing():
    frame = frame_fixture([0.0, 1.0, 2.0, 3.0], [0.2, 0.95, 1.0, 1.0])
    result = compute_step(frame, step_fixture())
    assert_unavailable(result, ["rise_time"], "insufficient_data")
    assert_value(result, "overshoot_pct", 0.0)


@pytest.mark.parametrize("actual", [[0.0] * 5, [0.0, 0.5, 0.7, 0.8, 0.8]])
def test_unreached_response_is_not_renormalized_to_record_tail(actual):
    result = compute_step(frame_fixture(np.arange(5, dtype=float), actual), step_fixture())
    assert_unavailable(result, ["rise_time", "settling_time"], "not_reached")
    assert_value(result, "overshoot_pct", 0.0)


@pytest.mark.parametrize(
    "r0,r1,y0", [(1.0, 1.0, 0.0), (0.0, 1.0, 1.0), (0.0, 5e-10, 0.0)]
)
def test_static_or_near_zero_step_has_no_response_metrics(r0, r1, y0):
    frame = frame_fixture([0.0, 1.0, 2.0], [y0, y0, y0], r1)
    result = compute_step(frame, step_fixture(r0=r0, r1=r1, y0=y0))
    assert_unavailable(result, STEP_KEYS, "not_applicable")


def test_varying_post_step_target_is_not_treated_as_single_step():
    frame = frame_fixture([0.0, 1.0, 2.0, 3.0], [0.0, 0.5, 1.0, 1.0], [1.0, 1.0, 2.0, 1.0])
    result = compute_step(frame, step_fixture())
    assert_unavailable(result, STEP_KEYS, "not_applicable")


def test_nonuniform_integrals_match_hand_calculated_trapezoids():
    # t=[0,1,3],e=[0,2,2]：∫e²=(0+4)/2*1+(4+4)/2*2=10；∫|e|=5。
    frame = frame_fixture([0.0, 1.0, 3.0], [0.0, -2.0, -2.0], 0.0)
    result = compute_tracking(frame, unit="rad/s")
    assert_value(result, "rmse_t", math.sqrt(10.0 / 3.0))
    assert_value(result, "iae", 5.0)
    assert_value(result, "max_abs_error", 2.0)
    assert_value(result, "tail_bias", 2.0)
    assert result.metrics["rmse_t"].unit == "rad/s"
    assert "s" in result.metrics["iae"].unit


def test_absolute_error_and_error_square_are_integrated_as_sampled_values():
    # e从-1到1。用户约定先求|e|和e²再梯形积分；不补零交点或平方解析积分。
    frame = frame_fixture([0.0, 2.0], [1.0, -1.0], 0.0)
    result = compute_tracking(frame)
    assert_value(result, "iae", 2.0)
    assert_value(result, "rmse_t", 1.0)
    assert_value(result, "max_abs_error", 1.0)


@pytest.mark.parametrize("fraction,expected", [(0.1, 2.85), (2.0 / 3.0, 2.0), (1.0, 1.5)])
def test_tail_bias_uses_time_window_and_interpolated_boundary(fraction, expected):
    # e(t)=t，末段均值=(窗口起点+3)/2，独立于不均匀的采样密度。
    frame = frame_fixture([0.0, 1.0, 3.0], [0.0, -1.0, -3.0], 0.0)
    result = compute_tracking(frame, MetricConfig(tail_fraction=fraction))
    assert_value(result, "tail_bias", expected)


@pytest.mark.parametrize("time_shift", [0.0, 1e9])
@pytest.mark.parametrize("scale", [1.0, -3.0])
def test_time_translation_and_signal_scaling_keep_physical_units(time_shift, scale):
    frame = frame_fixture(np.array([0.0, 1.0, 3.0]) + time_shift, [0.0, -2.0 * scale, -2.0 * scale], 0.0)
    result = compute_tracking(frame)
    assert_value(result, "rmse_t", abs(scale) * math.sqrt(10.0 / 3.0))
    assert_value(result, "iae", abs(scale) * 5.0)
    assert_value(result, "max_abs_error", abs(scale) * 2.0)
    assert_value(result, "tail_bias", scale * 2.0)


def test_static_tracking_error_has_meaningful_metrics_and_signed_bias():
    frame = frame_fixture([0.0, 0.4, 1.0, 1.8, 3.0], [4.0] * 5, 1.0)
    result = compute_tracking(frame)
    assert_value(result, "rmse_t", 3.0)
    assert_value(result, "iae", 9.0)
    assert_value(result, "max_abs_error", 3.0)
    assert_value(result, "tail_bias", -3.0)


@pytest.mark.parametrize(
    "times,actual,index",
    [
        ([0.0, 1.0, 1.0], [0.0, 0.5, 1.0], None),
        ([0.0, 2.0, 1.0], [0.0, 0.5, 1.0], None),
        ([0.0, 1.0, 2.0], [0.0, float("nan"), 1.0], None),
        ([0.0, 1.0, 2.0], [0.0, float("inf"), 1.0], None),
        ([0.0, 1.0, 2.0], [0.0, 0.5, 1.0], [0, 1, 3]),
        ([0.0, 1.0, 10.0], [0.0, 0.5, 1.0], None),
    ],
    ids=["duplicate_time", "reversal", "missing", "infinite", "original_row_gap", "sampling_gap"],
)
def test_invalid_data_is_not_bridged_or_silently_cleaned(times, actual, index):
    frame = frame_fixture(times, actual, index=index)
    assert_unavailable(compute_tracking(frame), TRACKING_KEYS, "invalid_data")
    assert_unavailable(compute_step(frame, step_fixture()), STEP_KEYS, "invalid_data")


def test_explicit_interval_rules_can_accept_uneven_but_continuous_samples():
    frame = frame_fixture([0.0, 1.0, 10.0], [0.0, -2.0, -2.0], 0.0)
    config = MetricConfig(min_interval_ratio=0.1, max_interval_ratio=3.0)
    result = compute_tracking(frame, config)
    # ∫e²=2+36=38，∫|e|=1+18=19。
    assert_value(result, "rmse_t", math.sqrt(3.8))
    assert_value(result, "iae", 19.0)


def test_one_point_does_not_produce_zero_or_pretend_observation_span():
    frame = frame_fixture([0.0], [0.0])
    assert_unavailable(compute_tracking(frame), TRACKING_KEYS, "insufficient_data")
    assert_unavailable(compute_step(frame, step_fixture()), STEP_KEYS, "insufficient_data")


def test_finite_inputs_with_overflowing_error_return_invalid_data():
    frame = frame_fixture([0.0, 1.0], [-1e308, -1e308], 1e308)
    assert_unavailable(compute_tracking(frame), TRACKING_KEYS, "invalid_data")


def test_numerical_boundary_interpolation_does_not_modify_input_frame():
    frame = frame_fixture([0.0, 1.0, 2.0, 3.0], [0.0, 0.5, 1.0, 1.0])
    before = frame.copy(deep=True)
    compute_tracking(frame)
    compute_step(frame, step_fixture())
    pd.testing.assert_frame_equal(frame, before)
