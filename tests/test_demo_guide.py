"""演示入口使用正式解析和确认流程，不能伪造或继承指标。"""

import pytest
from streamlit.testing.v1 import AppTest

from examples.catalog import load_example
from examples.closed_loop import load_closed_loop_example
from test_app import APP_PATH, PAGES, confirm_example
from test_performance_ui import confirm_step


@pytest.mark.parametrize("button,example_id", [
    ("demo_first_order", "first_order_step"),
    ("demo_quality", "quality_issues"),
    ("demo_pi_a", "closed_loop_pi_a"),
    ("demo_pi_b", "closed_loop_pi_b"),
])
def test_demo_loads_catalog_bytes_without_confirming_fields_or_step(button, example_id):
    app = AppTest.from_file(str(APP_PATH), default_timeout=20).run()
    app.button(key=button).click().run()
    assert not app.exception
    loader = load_closed_loop_example if example_id.startswith("closed_loop_") else load_example
    filename, raw, metadata = loader(example_id)
    state = app.session_state["lab_session"]
    assert state.filename == filename
    assert state.raw_bytes == raw == state.parsed.raw_bytes
    assert state.source_metadata == metadata
    assert state.prepared is None and state.quality is None and state.performance is None
    assert state.step_selection is None
    assert not app.checkbox(key=f"units_confirmed_{state.revision}").value
    assert not app.get("plotly_chart")
    assert app.radio(key="input_mode").value == "加载示例"
    assert app.selectbox(key="example_choice").value == example_id
    assert app.sidebar.radio[0].options == PAGES

    confirm_example(app)
    assert state.quality is not None
    if example_id == "quality_issues":
        assert state.quality.blocking
        assert not app.get("plotly_chart")
    else:
        assert len(app.get("plotly_chart")) == 3
        assert state.performance.tracking is not None
        assert state.performance.response is None


def test_demo_replaces_working_result_but_keeps_explicitly_saved_snapshot():
    app = AppTest.from_file(str(APP_PATH), default_timeout=20).run()
    app.button(key="demo_pi_a").click().run()
    confirm_step(confirm_example(app))
    app.button(key="save_current_experiment").click().run()
    state = app.session_state["lab_session"]
    saved = {key: item.performance.model_dump(mode="json") for key, item in state.saved_experiments.items()}
    assert len(saved) == 1 and state.performance.response is not None
    app.button(key="demo_pi_b").click().run()
    assert not app.exception
    assert state.source_metadata["example_id"] == "closed_loop_pi_b"
    assert state.performance is None and state.step_selection is None and state.confirmed_key == ""
    assert {key: item.performance.model_dump(mode="json") for key, item in state.saved_experiments.items()} == saved
    assert not app.get("plotly_chart")
    assert app.chat_input[0].disabled


def test_failed_demo_read_keeps_working_experiment_and_hides_raw_error(monkeypatch):
    app = AppTest.from_file(str(APP_PATH), default_timeout=20).run()
    app.button(key="demo_first_order").click().run()
    confirm_example(app)
    state = app.session_state["lab_session"]
    before = (state.source_key, state.confirmed_key, state.raw_bytes)

    def fail(*args, **kwargs):
        raise ValueError("private-file-path-and-sensitive-log")

    monkeypatch.setattr("ui.demo_guide.load_example", fail)
    app.button(key="demo_quality").click().run()
    assert not app.exception
    assert (state.source_key, state.confirmed_key, state.raw_bytes) == before
    assert any("当前实验未替换" in error.value for error in app.error)
    assert all("private-file-path" not in error.value for error in app.error)
    assert len(app.get("plotly_chart")) == 3


def test_quick_demo_is_session_scoped_and_does_not_add_navigation():
    first = AppTest.from_file(str(APP_PATH), default_timeout=20).run()
    first.button(key="demo_first_order").click().run()
    second = AppTest.from_file(str(APP_PATH), default_timeout=20).run()
    assert second.session_state["lab_session"].raw_bytes is None
    assert not second.get("plotly_chart")
    for page in PAGES[1:]:
        second.sidebar.radio[0].set_value(page).run()
        assert not second.exception
        assert not any(button.key.startswith("demo_") for button in second.button)
        assert second.sidebar.radio[0].options == PAGES
