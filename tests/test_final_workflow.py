"""第七阶段实际页面链路验收；数学 CSV 与上传流替身只用于离线测试。

AppTest 尚无原生文件上传操作，本文件只替换 st.file_uploader 的返回流；
CSV 解析、字段确认、质量、指标、规则与会话仍执行正式应用代码。
真正浏览器文件选择另行验收，不把这里的流注入写成浏览器上传通过。
"""

import io
import math

import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

from test_app import APP_PATH, confirm_example, load_example_app
from test_performance_ui import confirm_step
from test_stage4_ui import checkbox


class UploadedCSV(io.BytesIO):
    def __init__(self, raw, name="同名实验.csv"):
        super().__init__(raw)
        self.name = name
        self.size = len(raw)


def upload_bytes(*, tau=1.0):
    # 解析函数合成负阶跃：y0=3,r1=-1，tau 可变；没有调用被测函数造期望。
    times = [index / 20 for index in range(-10, 121)]
    rows = [
        {"时钟刻度": 1000 * t, "给定通道": 3 if t < 0 else -1,
         "测量通道": 3 if t < 0 else -1 + 4 * math.exp(-t / tau),
         "备注": "解析函数合成数据，非实测"}
        for t in times
    ]
    return pd.DataFrame(rows).to_csv(index=False, sep=";").encode("utf-8-sig")


def uploaded_app(monkeypatch, raw):
    selected = {"upload": UploadedCSV(raw)}
    monkeypatch.setattr("ui.import_page.st.file_uploader", lambda *args, **kwargs: selected["upload"])
    app = AppTest.from_file(str(APP_PATH), default_timeout=15).run()
    assert not app.exception
    return app, selected


def map_uploaded(app, *, time_unit="ms", unit="pulse"):
    for field, column in (("time", "时钟刻度"), ("target", "给定通道"), ("actual", "测量通道")):
        next(w for w in app.selectbox if w.label.startswith(f"{field} ·")).set_value(column).run()
    next(w for w in app.selectbox if w.label == "原始时间单位").set_value(time_unit).run()
    next(w for w in app.text_input if w.label == "目标 / 实测物理量名称").set_value("脉冲计数").run()
    next(w for w in app.text_input if w.label == "目标 / 实测共同单位").set_value(unit).run()
    checkbox(app, "我确认目标值与实测值").check().run()
    app.button(key="confirm_mapping").click().run()
    assert not app.exception and not app.error
    return app


def test_uploaded_chinese_bom_semicolon_negative_step_matches_independent_theory(monkeypatch):
    raw = upload_bytes()
    app, _ = uploaded_app(monkeypatch, raw)
    state = app.session_state["lab_session"]
    assert state.parsed.encoding == "utf-8-sig" and state.parsed.delimiter == ";"
    assert state.prepared is None and not app.get("plotly_chart")
    map_uploaded(app)
    assert not state.quality.issues
    assert state.quality.duration_s == 6.5
    assert state.prepared.frame.time.tolist()[0] == -0.5
    assert state.prepared.frame.time.tolist()[-1] == 6
    assert state.prepared.frame.actual.iloc[0] == 3  # 不把 pulse 换成角度。
    confirm_step(app)
    result = state.performance
    assert result.step.r0 == result.step.y0 == 3
    assert result.step.r1 == -1 and result.step.t0 == 0
    assert result.step.y0_source == "baseline"
    assert result.response.metrics["rise_time"].value == pytest.approx(math.log(9), rel=0, abs=0.05**2)
    assert result.response.metrics["settling_time"].value == pytest.approx(-math.log(0.02), rel=0, abs=0.05**2)
    assert result.response.metrics["overshoot_pct"].value == 0
    assert result.tracking.metrics["rmse_t"].unit == "pulse"
    assert state.parsed.raw_bytes == raw
    assert state.parsed.frame["时钟刻度"].iloc[0] == "-500.0"
    assert len(app.get("plotly_chart")) == 2


def test_replacing_uploaded_same_name_revokes_confirmation_but_preserves_saved_snapshot(monkeypatch):
    first = upload_bytes()
    app, selected = uploaded_app(monkeypatch, first)
    confirm_step(map_uploaded(app))
    state = app.session_state["lab_session"]
    previous_id = state.prepared.experiment.experiment_id
    previous_digest = state.parsed.sha256
    app.button(key="save_current_experiment").click().run()
    saved = next(iter(state.saved_experiments.values()))
    previous_result = saved.performance.model_dump(mode="json")
    second = upload_bytes(tau=0.5)
    selected["upload"] = UploadedCSV(second)
    app.run()
    assert not app.exception
    assert state.parsed.filename == saved.prepared.parsed.filename == "同名实验.csv"
    assert state.parsed.sha256 != previous_digest
    assert state.prepared is state.performance is state.evidence is state.traceability is None
    assert state.step_selection is None and not state.confirmed_key
    assert not app.get("plotly_chart")
    assert not checkbox(app, "我确认目标值与实测值").value
    assert saved.prepared.parsed.raw_bytes == first
    assert saved.performance.model_dump(mode="json") == previous_result
    confirm_step(map_uploaded(app))
    assert state.prepared.experiment.experiment_id != previous_id
    assert state.performance.response.metrics["rise_time"].value == pytest.approx(0.5 * math.log(9), rel=0, abs=0.05**2 / 0.5)
    assert state.parsed.raw_bytes == second


@pytest.mark.parametrize("change", ["physical_unit", "time_unit", "control_unit", "control_mapping", "quality_rule"])
def test_rule_approvals_cannot_survive_changed_field_unit_or_quality_confirmation(change):
    app = confirm_example(load_example_app("closed_loop_pi_a"))
    state = app.session_state["lab_session"]
    checkbox(app, "我已核对控制输出单位及上下限").check().run()
    next(w for w in app.text_input if w.label.startswith("合理物理变化率上限")).set_value("0.1").run()
    checkbox(app, "我确认该变化率上限").check().run()
    assert state.evidence.config.output_limits is not None
    assert state.evidence.config.rate_limit_confirmed
    old_events = {e.event_id for e in state.evidence.events}
    assert old_events
    if change == "physical_unit":
        next(w for w in app.text_input if w.label == "目标 / 实测共同单位").set_value("pulse").run()
    elif change == "time_unit":
        next(w for w in app.selectbox if w.label == "原始时间单位").set_value("ms").run()
    elif change == "control_unit":
        next(w for w in app.text_input if w.label == "control 单位").set_value("V").run()
    elif change == "control_mapping":
        # 交换映射，仍满足一个原始列仅映射一次，不创建非法重复映射。
        next(w for w in app.selectbox if w.label.startswith("control ·")).set_value("i_term").run()
        next(w for w in app.selectbox if w.label.startswith("i_term ·")).set_value("control").run()
    else:
        app.number_input(key="interval_upper").set_value(3.0).run()
    assert state.prepared is state.evidence is None
    app.button(key="confirm_mapping").click().run()
    assert not app.exception and not app.error
    assert state.evidence.config.output_limits is None
    assert not state.evidence.config.rate_limit_confirmed
    assert not checkbox(app, "我已核对控制输出单位及上下限").value
    assert not checkbox(app, "我确认该变化率上限").value
    assert old_events.isdisjoint(e.event_id for e in state.evidence.events)


def test_invalid_then_restored_interval_never_resurrects_confirmed_step():
    app = confirm_step(confirm_example(load_example_app()))
    state = app.session_state["lab_session"]
    old_end = state.performance.end_row
    next(w for w in app.number_input if w.label == "结束数据行").set_value(1).run()
    assert not app.exception and app.error
    assert state.performance is state.step_selection is state.traceability is None
    assert not app.get("plotly_chart")
    next(w for w in app.number_input if w.label == "结束数据行").set_value(old_end).run()
    assert not app.exception
    assert state.performance.response is None and state.step_selection is None
    assert len(app.get("plotly_chart")) == 3


def test_rule_configuration_survives_navigation_but_not_return_to_old_units():
    app = confirm_example(load_example_app("closed_loop_pi_a"))
    state = app.session_state["lab_session"]
    checkbox(app, "我已核对控制输出单位及上下限").check().run()
    next(w for w in app.number_input if w.label == "波动观察窗 [s]").set_value(3.0).run()
    next(w for w in app.text_input if w.label.startswith("合理物理变化率上限")).set_value("0.1").run()
    checkbox(app, "我确认该变化率上限").check().run()
    before = state.evidence.model_dump(mode="json")
    app.sidebar.radio[0].set_value("实验对比").run()
    app.sidebar.radio[0].set_value("实验分析").run()
    assert not app.exception
    assert state.evidence.model_dump(mode="json") == before
    assert state.evidence.config.oscillation_window_s == 3
    assert checkbox(app, "我已核对控制输出单位及上下限").value
    assert checkbox(app, "我确认该变化率上限").value
    original_confirmation = state.confirmed_key
    for unit in ("pulse", "1"):
        next(w for w in app.text_input if w.label == "目标 / 实测共同单位").set_value(unit).run()
        app.button(key="confirm_mapping").click().run()
        assert not app.exception and not app.error
        assert not checkbox(app, "我已核对控制输出单位及上下限").value
        assert not checkbox(app, "我确认该变化率上限").value
        assert state.evidence.config.oscillation_window_s == 2
    # 最后回到完全相同的确认指纹，也不会恢复已撤销的物理阈值授权。
    assert state.confirmed_key == original_confirmation
    assert state.evidence.config.output_limits is None
    assert state.evidence.config.rate_limit is None


def test_repeated_comparison_window_changes_and_reruns_preserve_report_scope():
    from test_stage4_ui import save_example, compare
    from test_report_ui import generate

    app = save_example()
    generate(app)  # 报告页以前选择过 current，之后应仍可选择 A/B。
    app.sidebar.radio[0].set_value("实验分析").run()
    save_example(app, "closed_loop_pi_b")
    app.sidebar.radio[0].set_value("实验对比").run()
    state = app.session_state["lab_session"]
    first = compare(app).model_dump(mode="json")
    for _ in range(3):
        app.run()
        assert state.comparison.model_dump(mode="json") == first
    for duration in (10.0, 20.0):
        next(w for w in app.number_input if w.label == "共同请求观察时长 [s]").set_value(duration).run()
        assert state.comparison is None
        expected = compare(app).model_dump(mode="json")
        for _ in range(3):
            app.run()
            assert state.comparison.model_dump(mode="json") == expected
    app.sidebar.radio[0].set_value("报告预览").run()
    assert not app.exception
    assert state.comparison.model_dump(mode="json") == expected
    assert "已确认A/B对比及两侧实验" in app.selectbox(key="report_scope").options
    bundle = generate(app, "comparison")
    assert bundle.snapshot.data()["comparison"] == expected
    for _ in range(3):
        app.sidebar.radio[0].set_value("实验对比").run()
        assert not app.exception
        assert state.comparison.model_dump(mode="json") == expected
        app.run()
        assert state.comparison.model_dump(mode="json") == expected
        app.sidebar.radio[0].set_value("报告预览").run()
        assert "已确认A/B对比及两侧实验" in app.selectbox(key="report_scope").options
        assert state.comparison.model_dump(mode="json") == expected
