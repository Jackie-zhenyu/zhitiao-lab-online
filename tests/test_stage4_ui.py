"""第四阶段页面验收：真实示例导入、确认、证据聚焦与会话内 A/B。"""

import json

import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

from test_app import APP_PATH, confirm_example, load_example_app
from test_performance_ui import confirm_step


def checkbox(app, prefix):
    return next(item for item in app.checkbox if item.label.startswith(prefix))


def save_example(app=None, example_id="closed_loop_pi_a"):
    if app is None:
        app = load_example_app(example_id)
    else:
        app.selectbox(key="example_choice").set_value(example_id).run()
        app.button(key="load_example").click().run()
    confirm_step(confirm_example(app))
    app.button(key="save_current_experiment").click().run()
    assert not app.exception and not app.error
    return app


def compare(app):
    checkbox(app, "我确认采用该共同观察时长").check().run()
    app.button(key="run_ab_comparison").click().run()
    assert not app.exception and not app.error
    return app.session_state["lab_session"].comparison


def test_closed_loop_load_has_visible_provenance_and_requires_limits_confirmation():
    app = confirm_example(load_example_app("closed_loop_pi_a"))
    state = app.session_state["lab_session"]
    assert state.quality.issues == [] and len(state.prepared.frame) == 1051
    assert state.evidence.config.output_limits is None
    assert next(c for c in state.evidence.checks if c.rule == "control_limit").status == "not_applicable"
    assert any("闭环仿真数据，非实测" in item.value for item in app.warning)
    thresholds = next(item.value for item in app.dataframe if "默认值" in item.value.columns)
    assert {"当前值", "默认值", "来源"} <= set(thresholds.columns)
    assert all(v == "默认 / 与默认一致" for v in thresholds["来源"])
    checkbox(app, "我已核对控制输出单位及上下限").check().run()
    limits = state.evidence.config.output_limits
    assert (limits.lower, limits.upper, limits.confirmed) == (-2.5, 2.5, True)
    assert next(c for c in state.evidence.checks if c.rule == "control_limit").status == "checked"
    app.selectbox(key="example_choice").set_value("closed_loop_pi_b").run()
    app.button(key="load_example").click().run()
    confirm_example(app)
    assert state.evidence.config.output_limits is None
    assert not checkbox(app, "我已核对控制输出单位及上下限").value


def test_physical_rate_is_only_used_after_explicit_confirmation_and_bad_config_clears():
    app = confirm_example(load_example_app("closed_loop_pi_b"))
    state = app.session_state["lab_session"]
    rate = next(item for item in app.text_input if item.label.startswith("合理物理变化率上限"))
    rate.set_value("0.5").run()
    assert state.evidence.config.rate_limit == .5
    assert not state.evidence.config.rate_limit_confirmed
    assert not state.evidence.events
    checkbox(app, "我确认该变化率上限").check().run()
    assert state.evidence.events and state.evidence.config.rate_limit_confirmed
    event = state.evidence.events[0]
    assert event.category == "measurement_change"
    assert event.start_s < event.end_s
    app.button(key=f"focus_evidence_{event.event_id}").click().run()
    assert state.focused_event_id == event.event_id
    assert len(app.get("plotly_chart")) == 4
    next(item for item in app.text_input if item.label.startswith("合理物理变化率上限")).set_value("inf").run()
    assert app.error and state.evidence is None and state.focused_event_id is None
    assert not app.exception


def test_data_record_card_focus_preserves_native_rows_and_analysis_selection():
    app = confirm_example(load_example_app("quality_issues"))
    state = app.session_state["lab_session"]
    next(item for item in app.selectbox if item.label == "选择连续有效区间").set_value(0).run()
    before = state.prepared.frame.copy(deep=True)
    interval = state.metric_interval
    metric_values = state.performance.model_dump(mode="json")
    event = next(event for event in state.evidence.events[:10] if event.category == "data_record")
    assert event.start_s is None and event.end_s is None
    app.button(key=f"focus_evidence_{event.event_id}").click().run()
    assert not app.exception and state.focused_event_id == event.event_id
    assert state.metric_interval == interval
    assert state.performance.model_dump(mode="json") == metric_values
    pd.testing.assert_frame_equal(before, state.prepared.frame)
    # 第六阶段证据迁入独立区域，按图表语义定位，仍验证原始行聚焦。
    focused_chart = next(chart for item in app.get("plotly_chart") if "原始数据行" in (chart := json.loads(item.proto.spec))["layout"]["xaxis"]["title"]["text"])
    assert "原始数据行" in focused_chart["layout"]["xaxis"]["title"]["text"]
    assert focused_chart["layout"]["shapes"]
    assert any("尚不能确定的原因" in item.value for item in app.markdown)
    before_key = state.evidence_key
    next(item for item in app.number_input if item.label == "波动观察窗 [s]").set_value(3.0).run()
    assert state.evidence_key != before_key and state.focused_event_id is None
    assert state.performance.model_dump(mode="json") == metric_values
    next(item for item in app.text_input if item.label == "目标 / 实测共同单位").set_value("pulse").run()
    assert state.evidence is None and not app.get("plotly_chart")


def test_complete_ab_flow_requires_common_window_and_recomputes_on_changes():
    app = save_example()
    a_id = next(iter(app.session_state["lab_session"].saved_experiments))
    save_example(app, "closed_loop_pi_b")
    state = app.session_state["lab_session"]
    assert len(state.saved_experiments) == 2
    app.sidebar.radio[0].set_value("实验对比").run()
    assert not app.exception and state.comparison is None
    app.button(key="run_ab_comparison").click().run()
    assert app.error and state.comparison is None
    result = compare(app)
    assert result.config.common_duration_s == 20
    assert len(result.differences) == 7 and len(app.get("plotly_chart")) == 2
    assert result.a.start_s == result.b.start_s == 1
    assert result.a.relative_start_s == result.b.relative_start_s == 0
    assert result.a.end_s == result.b.end_s == 21
    assert result.a.source_label == result.b.source_label == "闭环仿真数据，非实测"
    assert result.a.response.metrics["rise_time"].value > result.b.response.metrics["rise_time"].value
    overshoot = next(d for d in result.differences if d.key == "overshoot_pct")
    assert overshoot.delta == 0 and overshoot.percent is None
    assert any("PID" in item.value for item in app.warning)
    chart = json.loads(app.get("plotly_chart")[0].proto.spec)
    assert "t−t0" in chart["layout"]["xaxis"]["title"]["text"]
    assert len(chart["data"]) == 4
    # 条件变化撤销旧结果，重新运行显示未知条件警告。
    next(item for item in app.text_input if item.label == "B · 负载").set_value("").run()
    assert state.comparison is None and not app.get("plotly_chart")
    unknown = compare(app)
    assert unknown.descriptive_only and any(c.name == "load" and c.status == "unknown" for c in unknown.checks)
    next(item for item in app.number_input if item.label == "共同请求观察时长 [s]").set_value(10.0).run()
    assert state.comparison is None
    assert not checkbox(app, "我确认采用该共同观察时长").value
    truncated = compare(app)
    assert truncated.a.end_s == truncated.b.end_s == 11
    assert truncated.a.end_row < result.a.end_row
    # 自比重新确认；可用差值为0，0分母仍不计算百分比。
    app.selectbox(key="ab_selected_b").set_value(a_id).run()
    assert state.comparison is None
    same = compare(app)
    assert all(d.delta == 0 for d in same.differences if d.a.value is not None)
    assert next(d for d in same.differences if d.key == "overshoot_pct").percent is None


def test_snapshots_are_independent_of_active_data_and_can_be_removed():
    app = save_example()
    state = app.session_state["lab_session"]
    snapshot = next(iter(state.saved_experiments.values()))
    original = snapshot.prepared.parsed.raw_bytes
    app.selectbox(key="example_choice").set_value("quality_issues").run()
    app.button(key="load_example").click().run()
    assert len(state.saved_experiments) == 1 and snapshot.prepared.parsed.raw_bytes == original
    fresh = AppTest.from_file(str(APP_PATH), default_timeout=15).run()
    assert fresh.session_state["lab_session"].saved_experiments == {}
    app.sidebar.radio[0].set_value("实验对比").run()
    compare(app)
    app.button(key="remove_saved_experiment").click().run()
    assert not app.exception
    assert not state.saved_experiments and state.comparison is None
    assert all(selector.disabled for selector in app.selectbox)


@pytest.mark.parametrize("page", ["实验分析", "实验对比", "报告预览"])
def test_closed_loop_label_remains_explicit_on_each_page(page):
    app = AppTest.from_file(str(APP_PATH), default_timeout=15).run()
    app.sidebar.radio[0].set_value(page).run()
    assert any("闭环仿真数据，非实测" in item.value for item in app.caption)
    assert not app.exception
