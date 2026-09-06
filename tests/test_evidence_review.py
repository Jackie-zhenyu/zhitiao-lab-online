"""独立核对证据视图；数据为明确的数学夹具，未修改生产实现。"""

import re

import numpy as np
import pandas as pd
import pytest

from core.event_models import EventConfig, OutputLimits
from core.events import detect_events
from core.import_models import MappingConfig, QualityConfig
from core.metric_models import MetricConfig
from core.parser import parse_csv
from core.performance import analyze_performance
from core.quality import check_quality, prepare_experiment
from ui import evidence
from ui.session import LabSession


def make_state(times, actual, control=None, quality_config=None):
    values = {"time": times, "target": np.zeros(len(times)), "actual": actual}
    units = {}
    if control is not None:
        values["control"] = control
        units["control"] = "V"
    parsed = parse_csv(pd.DataFrame(values).to_csv(index=False).encode("utf-8"), "review-math-fixture.csv")
    mapping = MappingConfig(
        fields={key: key for key in values}, time_unit="s", quantity="位置", unit="m",
        optional_units=units, confirmed=True, units_consistent=True,
    )
    prepared = prepare_experiment(parsed, mapping)
    quality = check_quality(prepared, quality_config)
    return LabSession(raw_bytes=parsed.raw_bytes, parsed=parsed, prepared=prepared, quality=quality)


def capture_view(monkeypatch):
    shown = {"charts": [], "tables": [], "captions": []}
    monkeypatch.setattr(evidence.st, "write", lambda *args, **kwargs: None)
    monkeypatch.setattr(evidence.st, "caption", lambda text, **kwargs: shown["captions"].append(text))
    monkeypatch.setattr(evidence.st, "plotly_chart", lambda chart, **kwargs: shown["charts"].append(chart))
    monkeypatch.setattr(evidence.st, "dataframe", lambda frame, **kwargs: shown["tables"].append(frame))
    return shown


def test_default_and_user_values_are_visible_with_consistent_units_and_sources():
    config = EventConfig(oscillation_min_time_fraction=0.5, rate_limit=2.0)
    table = evidence._threshold_table(config, "m").set_index("参数")
    assert table.loc["死区外时间占比", "当前值"] == "0.5"
    assert table.loc["死区外时间占比", "默认值"] == "0.25"
    assert table.loc["死区外时间占比", "来源"] == "用户设置 / 确认"
    assert table.loc["物理变化率上限 / m/s", "当前值"] == "2.0"
    assert table.loc["物理上限已确认", "当前值"] == "False"
    assert table.loc["波动窗 / s", "来源"] == "默认 / 与默认一致"


def test_oscillation_summary_shows_time_fraction_as_percent_without_sample_recounting():
    state = make_state(
        [0, 0.1, 0.2, 0.3, 1.3, 2], [0.1, 0, -0.1, 0, 0.1, -0.1],
        quality_config=QualityConfig(min_interval_ratio=0.01, max_interval_ratio=20),
    )
    report = detect_events(state.prepared, state.quality, EventConfig(oscillation_min_crossings=2))
    event = next(event for event in report.events if event.category == "oscillation")
    summary = evidence._observed_summary(event)
    assert "45.00%" in summary
    assert "66.67%" not in summary
    assert "阈值按各窗口判断" in summary


def test_control_focus_plots_control_in_control_units_and_preserves_metrics(monkeypatch):
    state = make_state([0, 1, 2, 3], [5, 5, 5, 5], control=[1, 1, 1, 1])
    report = detect_events(state.prepared, state.quality, EventConfig(output_limits=OutputLimits(lower=-1.0, upper=1.0, confirmed=True)))
    event = next(event for event in report.events if event.category == "control_limit")
    state.metric_interval = (2, 4)
    state.performance = analyze_performance(state.prepared, state.quality, 2, 4, MetricConfig(), source_label="数学测试夹具")
    performance_before = state.performance.model_dump(mode="json")
    frame_before = state.prepared.frame.copy(deep=True)
    raw_before = state.parsed.raw_bytes
    shown = capture_view(monkeypatch)
    evidence._focus_view(state, event)
    chart = shown["charts"][0]
    assert len(chart.data) == 1 and chart.data[0].name == "控制输出"
    np.testing.assert_array_equal(chart.data[0].y, [1, 1, 1, 1])
    assert chart.layout.yaxis.title.text.endswith("[V]")
    assert chart.layout.shapes[0].x0 == event.start_s
    assert chart.layout.shapes[0].x1 == event.end_s
    assert state.metric_interval == (2, 4)
    assert state.performance.model_dump(mode="json") == performance_before
    pd.testing.assert_frame_equal(state.prepared.frame, frame_before)
    assert state.parsed.raw_bytes == raw_before


def test_unknown_time_focus_uses_original_rows_and_never_connects_across_missing_row(monkeypatch):
    state = make_state([0, 1, None, 3, 4, 5], [0] * 6)
    event = next(event for event in detect_events(state.prepared, state.quality).events if event.category == "data_record")
    assert event.start_s is None and event.end_s is None
    shown = capture_view(monkeypatch)
    evidence._focus_view(state, event)
    chart = shown["charts"][0]
    assert "原始数据行" in chart.layout.xaxis.title.text
    assert chart.layout.shapes[0].x0 == event.start_row == 3
    assert chart.layout.shapes[0].x1 == 3
    assert len(chart.data) == 4
    for trace in chart.data:
        rows = np.asarray(trace.x)
        assert 3 not in rows
        assert np.all(np.diff(rows) == 1)
        assert trace.connectgaps is False
    assert 3 in shown["tables"][0].index
    assert shown["tables"][0].loc[3, "time"] == ""


def test_control_fraction_summary_displays_sixty_percent_time_not_forty_percent_points():
    state = make_state(
        [0, 0.2, 1.2, 1.4, 2], [0] * 5, control=[0, 1, 1, 0, 0],
        quality_config=QualityConfig(min_interval_ratio=0.05, max_interval_ratio=10),
    )
    report = detect_events(state.prepared, state.quality, EventConfig(output_limits=OutputLimits(lower=-1.0, upper=1.0, confirmed=True)))
    event = next(event for event in report.events if event.category == "control_limit")
    summary = evidence._observed_summary(event)
    assert "60.00%" in summary
    assert "40.00%" not in summary


def test_near_threshold_summary_does_not_round_strict_exceedance_into_displayed_equality():
    state = make_state([0, 1], [0, 1.00000001])
    report = detect_events(state.prepared, state.quality, EventConfig(rate_limit=1.0, rate_limit_confirmed=True))
    event = next(event for event in report.events if event.category == "measurement_change")
    summary = evidence._observed_summary(event)
    values = re.search(r"绝对变化率 ([\deE+.\-]+) m/s，超过规则阈值 ([\deE+.\-]+) m/s", summary)
    assert values, summary
    assert float(values.group(1)) > float(values.group(2)), summary
