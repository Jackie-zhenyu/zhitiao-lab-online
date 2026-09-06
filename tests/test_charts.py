"""仅以合成测试夹具检查显示与分析数据的隔离。"""

import numpy as np
import pandas as pd
import pytest

from core.import_models import MappingConfig
from ui.charts import build_charts


def chart_input(control=True):
    target = np.linspace(0, 1, 101)
    frame = pd.DataFrame({"time": np.linspace(0, 10, 101), "target": target, "actual": target / 2, "error": target / 2})
    fields = {"time": "time", "target": "target", "actual": "actual"}
    if control:
        frame["control"] = target
        fields["control"] = "control"
    mapping = MappingConfig(fields=fields, time_unit="s", quantity="测试位置", unit="rad", optional_units={"control": "V"} if control else {}, confirmed=True, units_consistent=True)
    return frame, mapping


def test_downsampling_only_changes_display_and_keeps_endpoints():
    frame, mapping = chart_input()
    before = frame.copy(deep=True)
    figures = build_charts(frame, mapping, max_points=10)
    pd.testing.assert_frame_equal(frame, before)
    assert len(figures) == 3
    response = figures[0][1]
    assert len(response.data[0].x) == 10
    assert response.data[0].x[0] == 0 and response.data[0].x[-1] == 10
    assert response.data[0].customdata[-1] == 101
    assert response.layout.yaxis.title.text == "测试位置 [rad]"
    assert figures[2][1].layout.yaxis.title.text == "控制输出 [V]"
    assert all(trace.connectgaps is False for _, figure in figures for trace in figure.data)


def test_without_control_has_response_and_error_only():
    frame, mapping = chart_input(control=False)
    assert len(build_charts(frame, mapping)) == 2


def test_chart_refuses_nonfinite_or_discontinuous_rows():
    frame, mapping = chart_input()
    with pytest.raises(ValueError, match="连续原始"):
        build_charts(frame.drop(index=30), mapping)
    frame.loc[30, "actual"] = np.nan
    with pytest.raises(ValueError, match="非有限"):
        build_charts(frame, mapping)
