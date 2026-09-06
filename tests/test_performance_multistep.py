"""页面集成：多阶跃独立观察区间、基线来源与切换后重新确认。"""

from pathlib import Path

import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

from core.models import ResultStatus


@pytest.fixture
def multistep_app(monkeypatch):
    target = [0] * 10 + [1] * 20 + [2] * 20
    raw = pd.DataFrame({
        "time": [index / 10 for index in range(50)],
        "target": target,
        "actual": target,
    }).to_csv(index=False).encode("utf-8")
    metadata = {
        "example_id": "first_order_step", "title": "双阶跃集成测试",
        "description": "解析函数合成数据，仅用于测试区间边界。",
        "time_unit": "s", "quantity": "响应量", "unit": "1",
        "source_kind": "simulated", "source_label": "解析函数合成数据",
    }
    # 只替换文件读取，CSV 解析、映射确认、质量检查、选段和指标仍走真实页面。
    monkeypatch.setattr(
        "ui.import_page.load_example",
        lambda example_id, *, max_bytes: ("multistep.csv", raw, metadata.copy()),
    )
    app = AppTest.from_file(Path(__file__).resolve().parents[1] / "app.py").run()
    app.radio(key="input_mode").set_value("加载示例").run()
    app.button(key="load_example").click().run()
    next(widget for widget in app.checkbox if widget.label.startswith("我确认目标值")).check().run()
    app.button(key="confirm_mapping").click().run()
    assert not app.exception
    return app


def confirm_step(app):
    next(widget for widget in app.checkbox if widget.label.startswith("我已核对本片段")).check().run()
    app.button(key="confirm_step").click().run()
    assert not app.exception
    return app.session_state["lab_session"].performance


def test_candidate_switch_revokes_confirmation_and_keeps_independent_intervals(multistep_app):
    app = multistep_app
    first = confirm_step(app)
    assert (first.start_row, first.end_row) == (1, 50)
    assert (first.step_start_row, first.step_end_row, first.step.t0) == (1, 30, 1.0)
    assert first.response.details["observation_start_s"] == 1.0
    assert first.response.details["observation_end_s"] == 2.9
    assert (first.step.baseline_start_s, first.step.baseline_end_s) == (0.5, 0.9)
    assert (first.step.baseline_start_row, first.step.baseline_end_row) == (6, 10)

    next(widget for widget in app.selectbox if widget.label.startswith("选择候选阶跃")).set_value(1).run()
    assert not app.exception
    state = app.session_state["lab_session"]
    assert state.step_selection is None
    assert state.performance.response is None

    second = confirm_step(app)
    assert (second.start_row, second.end_row) == (1, 50)
    assert (second.step_start_row, second.step_end_row, second.step.t0) == (11, 50, 3.0)
    assert second.response.details["observation_start_s"] == 3.0
    assert second.response.details["observation_end_s"] == 4.9
    assert (second.step.baseline_start_s, second.step.baseline_end_s) == (2.5, 2.9)
    assert (second.step.baseline_start_row, second.step.baseline_end_row) == (26, 30)
    assert second.step.baseline_method
    assert second.response.details["baseline_start_row"] == 26
    assert second.response.details["baseline_end_row"] == 30
    assert (second.step.r0, second.step.r1) == (1.0, 2.0)

    # 返回此前计算过的候选也必须再次确认，不从旧的候选结果恢复响应指标。
    next(widget for widget in app.selectbox if widget.label.startswith("选择候选阶跃")).set_value(0).run()
    assert not app.exception
    assert app.session_state["lab_session"].step_selection is None
    assert app.session_state["lab_session"].performance.response is None


def test_manual_mode_cannot_treat_two_target_changes_as_one_step(multistep_app):
    app = multistep_app
    confirm_step(app)
    app.radio(key="step_mode").set_value("手动指定").run()
    assert app.session_state["lab_session"].step_selection is None
    next(widget for widget in app.number_input if widget.label.startswith("手动阶跃时刻")).set_value(1.0).run()
    result = confirm_step(app)
    assert (result.step_start_row, result.step_end_row) == (1, 50)
    assert result.step.y0_source == "baseline"
    assert all(metric.status == ResultStatus.NOT_APPLICABLE for metric in result.response.metrics.values())
    assert all(metric.value is None for metric in result.response.metrics.values())
    assert not result.response.markers
