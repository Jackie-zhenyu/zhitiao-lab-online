"""第二阶段页面验收：真实加载解析合成示例，不连接模型。"""

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest
from ui.session import LabSession


APP_PATH = Path(__file__).resolve().parents[1] / "app.py"
PAGES = ["实验分析", "实验对比", "报告预览"]


def test_app_starts_without_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    app = AppTest.from_file(str(APP_PATH), default_timeout=15).run()
    assert not app.exception
    assert app.title[0].value == "实验分析"
    assert app.sidebar.radio[0].options == PAGES
    assert any("无需 API Key" in caption.value for caption in app.caption)


@pytest.mark.parametrize("page", PAGES)
def test_each_page_is_reachable_and_explicitly_unimplemented(page: str) -> None:
    app = AppTest.from_file(str(APP_PATH), default_timeout=15).run()
    app.sidebar.radio[0].set_value(page).run()
    assert not app.exception
    assert app.title[0].value == page
    assert any("尚未实现" in notice.value for notice in app.info)
    assert not app.get("metric")
    assert not app.get("plotly_chart")
    assert len(app.button) > 0
    # 第一阶段的禁用约束只继续适用于未授权实现的功能。
    if page != "实验分析":
        assert all(button.disabled for button in app.button)
    else:
        assert app.button(key="send_question").disabled


def test_future_data_inputs_are_disabled() -> None:
    app = AppTest.from_file(str(APP_PATH), default_timeout=15).run()
    assert not app.exception
    uploaders = app.get("file_uploader")
    # 第八阶段增加独立项目包上传；两种入口均已实现，不能再断言只有 CSV。
    assert {item.key for item in uploaders} == {"analysis_csv", "project_upload"}
    assert all(not item.proto.disabled for item in uploaders)
    assert app.chat_input[0].disabled  # 第五阶段改为聊天输入；无密钥仍禁用。
    app.sidebar.radio[0].set_value("实验对比").run()
    assert not app.exception
    assert len(app.selectbox) == 2
    assert all(selector.disabled for selector in app.selectbox)


def load_example_app(example_id="first_order_step"):
    app = AppTest.from_file(str(APP_PATH), default_timeout=15).run()
    app.radio(key="input_mode").set_value("加载示例").run()
    app.selectbox(key="example_choice").set_value(example_id).run()
    app.button(key="load_example").click().run()
    assert not app.exception
    return app


def confirm_example(app):
    app.checkbox[0].check().run()
    app.button(key="confirm_mapping").click().run()
    assert not app.exception
    return app


def test_example_requires_confirmation_then_displays_quality_and_three_charts():
    app = load_example_app()
    assert any("解析函数合成数据" in item.value for item in app.warning)
    assert not app.get("plotly_chart")
    assert app.session_state["lab_session"].prepared is None
    confirm_example(app)
    assert len(app.get("plotly_chart")) == 3
    assert len(app.metric) == 7  # 三项质量摘要 + 四项一般跟踪指标；阶跃尚未确认。
    assert app.session_state["lab_session"].performance.response is None
    state = app.session_state["lab_session"]
    assert state.quality.issues == []
    assert state.parsed.raw_bytes.startswith(b"\xef\xbb\xbf")
    assert state.traceability["used_interval"]["start_row"] == 1
    assert state.traceability["source"]["kind"] == "simulated"


@pytest.mark.parametrize("example_id", ["irregular_sampling", "quality_issues"])
def test_problem_examples_block_full_data_until_valid_interval_selected(example_id):
    app = confirm_example(load_example_app(example_id))
    state = app.session_state["lab_session"]
    assert state.quality.blocking and state.quality.issues
    assert not app.get("plotly_chart")
    assert state.traceability is None
    selector = next(item for item in app.selectbox if item.label == "选择连续有效区间")
    selector.set_value(0).run()
    assert not app.exception
    assert len(app.get("plotly_chart")) == 3
    interval = app.session_state["lab_session"].traceability["used_interval"]
    assert interval["end_row"] < len(state.parsed.frame)


@pytest.mark.parametrize("change", ["mapping", "time_unit", "physical_unit", "quality_rule", "parse_options"])
def test_configuration_change_removes_old_quality_and_charts(change):
    app = confirm_example(load_example_app())
    assert app.get("plotly_chart")
    if change == "mapping":
        next(item for item in app.selectbox if item.label.startswith("actual ·")).set_value("control").run()
    elif change == "time_unit":
        next(item for item in app.selectbox if item.label == "原始时间单位").set_value("ms").run()
    elif change == "physical_unit":
        next(item for item in app.text_input if item.label == "目标 / 实测共同单位").set_value("pulse").run()
    elif change == "quality_rule":
        app.number_input(key="interval_upper").set_value(3.0).run()
    else:
        app.selectbox(key="csv_encoding").set_value("utf-8-sig").run()
    assert not app.exception
    assert not app.get("plotly_chart")
    assert app.session_state["lab_session"].quality is None
    assert app.session_state["lab_session"].traceability is None


def test_missing_mapping_and_unconfirmed_units_cannot_run():
    app = load_example_app()
    app.button(key="confirm_mapping").click().run()
    assert app.error and not app.get("plotly_chart")
    next(item for item in app.selectbox if item.label.startswith("time ·")).set_value("— 未映射 —").run()
    confirm_example(app)
    assert app.error and not app.get("plotly_chart")


def test_new_app_session_does_not_inherit_loaded_experiment():
    first = confirm_example(load_example_app())
    second = AppTest.from_file(str(APP_PATH), default_timeout=15).run()
    assert first.session_state["lab_session"].prepared is not None
    assert second.session_state["lab_session"].raw_bytes is None
    assert not second.get("plotly_chart")


def test_navigation_keeps_current_experiment_confirmation_and_selected_interval():
    app = confirm_example(load_example_app())
    state = app.session_state["lab_session"]
    before_hash, before_key = state.parsed.sha256, state.confirmed_key
    app.sidebar.radio[0].set_value("实验对比").run()
    app.sidebar.radio[0].set_value("报告预览").run()
    app.sidebar.radio[0].set_value("实验分析").run()
    assert not app.exception
    assert app.session_state["lab_session"].parsed.sha256 == before_hash
    assert app.session_state["lab_session"].confirmed_key == before_key
    assert next(item for item in app.text_input if item.label == "目标 / 实测共同单位").value == "1"
    assert app.checkbox[0].value is True
    assert len(app.get("plotly_chart")) == 3


def test_retained_uploaded_bytes_can_be_used_and_explicitly_cleared():
    app = AppTest.from_file(str(APP_PATH), default_timeout=15).run()
    state = LabSession()
    state.set_source(b"time,target,actual\n0,1,0\n1,1,0.5\n", "retained.csv", {"source_kind": "unknown"})
    app.session_state["lab_session"] = state
    app.run()
    next(item for item in app.text_input if item.label == "目标 / 实测物理量名称").set_value("测试脉冲计数").run()
    next(item for item in app.text_input if item.label == "目标 / 实测共同单位").set_value("pulse").run()
    confirm_example(app)
    assert len(app.get("plotly_chart")) == 2
    assert state.prepared.frame["actual"].tolist() == [0.0, 0.5]
    app.button(key="clear_retained_source").click().run()
    assert not app.exception and not app.get("plotly_chart")
    assert app.session_state["lab_session"].raw_bytes is None
