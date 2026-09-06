"""第三阶段页面测试：确认、配置失效、手动初值、图表和完整追溯。"""

import json
import math

import pytest

from core.models import ResultStatus
from test_app import confirm_example, load_example_app


def confirm_step(app):
    next(item for item in app.checkbox if item.label.startswith("我已核对本片段")).check().run()
    app.button(key="confirm_step").click().run()
    assert not app.exception
    return app


def test_step_requires_confirmation_then_shows_theoretical_values_and_markers():
    app = confirm_example(load_example_app())
    result = app.session_state["lab_session"].performance
    assert len(result.tracking.metrics) == 4 and result.response is None
    assert all(m.status == ResultStatus.SUCCESS for m in result.tracking.metrics.values())
    confirm_step(app)
    result = app.session_state["lab_session"].performance
    # 示例独立公式 τ=.7，采样间隔.05；交点线性误差按 h²/τ 约束。
    assert result.response.metrics["rise_time"].value == pytest.approx(.7 * math.log(9), abs=.05 ** 2 / .7)
    assert result.response.metrics["settling_time"].value == pytest.approx(-.7 * math.log(.02), abs=.05 ** 2 / .7)
    assert result.response.metrics["overshoot_pct"].value == 0
    assert result.step.y0_source == "baseline" and result.step.y0 == 0
    assert result.step.baseline_start_row == 11 and result.step.baseline_end_row == 20
    assert "时间加权" in result.step.baseline_method
    assert result.response.details["baseline_start_row"] == 11
    assert "stepinfo" in result.normalization
    assert result.source.sha256 == app.session_state["lab_session"].parsed.sha256
    assert len(app.metric) == 10 and len(app.get("plotly_chart")) == 3
    chart = json.loads(app.get("plotly_chart")[0].proto.spec)
    assert any(shape["type"] == "rect" for shape in chart["layout"]["shapes"])
    assert result.response.markers["t0"] == 1
    assert app.session_state["lab_session"].traceability["performance"]["normalization"] == result.normalization


@pytest.mark.parametrize("change", ["parameter", "interval", "step_end", "mapping"])
def test_response_confirmation_invalidates_when_inputs_change(change):
    app = confirm_step(confirm_example(load_example_app()))
    assert app.session_state["lab_session"].performance.response is not None
    if change == "parameter":
        app.number_input(key="metric_observation").set_value(3.0).run()
    elif change == "interval":
        next(item for item in app.number_input if item.label == "结束数据行").set_value(100).run()
    elif change == "step_end":
        next(item for item in app.number_input if item.label == "阶跃观察截止数据行").set_value(70).run()
    else:
        next(item for item in app.text_input if item.label == "目标 / 实测共同单位").set_value("pulse").run()
    assert not app.exception
    state = app.session_state["lab_session"]
    assert state.step_selection is None
    assert state.performance is None or state.performance.response is None


def test_manual_step_without_baseline_requires_explicit_y0():
    app = confirm_example(load_example_app())
    next(item for item in app.number_input if item.label == "起始数据行").set_value(21).run()
    app.radio(key="step_mode").set_value("手动指定").run()
    next(item for item in app.number_input if item.label.startswith("阶跃前目标 r0")).set_value(0.0).run()
    confirm_step(app)
    assert app.error and app.session_state["lab_session"].performance.response is None
    next(item for item in app.radio if item.label == "响应初值 y0 来源").set_value("手动提供 y0").run()
    confirm_step(app)
    assert app.error and app.session_state["lab_session"].performance.response is None
    next(item for item in app.text_input if item.label.startswith("明确填写响应初值")).set_value("0").run()
    confirm_step(app)
    result = app.session_state["lab_session"].performance
    assert result.step.y0_source == "manual" and result.step.y0 == 0
    assert result.step.baseline_start_s is None
    assert result.response.metrics["rise_time"].status == ResultStatus.SUCCESS


def test_short_step_observation_shows_reason_instead_of_fake_zero():
    app = confirm_example(load_example_app())
    next(item for item in app.number_input if item.label == "阶跃观察截止数据行").set_value(40).run()
    confirm_step(app)
    result = app.session_state["lab_session"].performance
    assert result.response.metrics["rise_time"].status == ResultStatus.NOT_REACHED
    assert result.response.metrics["rise_time"].value is None
    assert result.response.metrics["settling_time"].value is None
    assert result.response.metrics["overshoot_pct"].value == 0
    assert any("观测内未达到" in c.value for c in app.caption)


def test_step_confirmation_survives_navigation_but_is_session_local():
    app = confirm_step(confirm_example(load_example_app()))
    before = app.session_state["lab_session"].performance.model_dump(mode="json")
    app.sidebar.radio[0].set_value("报告预览").run()
    assert any("stepinfo" in item.value for item in app.caption)
    app.sidebar.radio[0].set_value("实验分析").run()
    assert not app.exception
    assert app.session_state["lab_session"].performance.model_dump(mode="json") == before
    fresh = confirm_example(load_example_app())
    assert fresh.session_state["lab_session"].performance.response is None


def test_bad_data_blocks_metrics_until_valid_continuous_interval_selected():
    app = confirm_example(load_example_app("quality_issues"))
    assert app.session_state["lab_session"].performance is None
    next(item for item in app.selectbox if item.label == "选择连续有效区间").set_value(0).run()
    assert not app.exception
    assert app.session_state["lab_session"].performance is not None
    assert all(m.status == ResultStatus.SUCCESS for m in app.session_state["lab_session"].performance.tracking.metrics.values())
