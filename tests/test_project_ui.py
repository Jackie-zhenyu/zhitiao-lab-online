"""项目面板实际 AppTest；上传流为测试替身，解析和恢复执行正式代码。"""
import io

import pytest
from streamlit.testing.v1 import AppTest

from projects.archive import export_project, load_project
from test_app import APP_PATH, confirm_example, load_example_app
from test_performance_ui import confirm_step
from ui.project_state import capture_session


@pytest.fixture(scope="module")
def saved_package():
    app = confirm_step(confirm_example(load_example_app()))
    app.button(key="save_current_experiment").click().run()
    app.sidebar.radio[0].set_value("报告预览").run()
    app.button(key="generate_report").click().run()
    assert not app.exception and not app.error
    project = capture_session(app.session_state.filtered_state, "中文本地项目", "</script><script>alert(1)</script>")
    return export_project(project)


def install_upload(monkeypatch, raw):
    import streamlit as st
    original = st.file_uploader
    chosen = {"file": None}

    class UploadedProject(io.BytesIO):
        name = "同名项目.labproj"

        def __init__(self, content):
            super().__init__(content)
            self.size = len(content)
            self.file_id = str(id(self))

    def uploader(*args, **kwargs):
        if kwargs.get("key", "").startswith("project_upload"):
            return chosen["file"]
        return original(*args, **kwargs)

    monkeypatch.setattr(st, "file_uploader", uploader)
    chosen["file"] = UploadedProject(raw)
    return chosen, UploadedProject


def test_generate_package_matches_current_results_and_warns_after_change():
    app = confirm_step(confirm_example(load_example_app()))
    app.button(key="project_export").click().run()
    assert not app.exception and not app.error
    raw, stamp = app.session_state["project_package"]
    restored = load_project(raw)
    state = app.session_state["lab_session"]
    assert restored.current.performance == state.performance
    assert restored.current.prepared.mapping == state.prepared.mapping
    assert restored.current.parsed.raw_bytes == state.raw_bytes
    assert any(item.label == "下载项目包 .labproj" for item in app.get("download_button"))
    app.text_input(key="project_name").set_value("新名称").run()
    assert any("页面已变化" in w.value for w in app.warning)
    assert app.session_state["project_package"] == (raw, stamp)


def test_invalid_or_changed_same_name_package_leaves_working_data(monkeypatch, saved_package):
    chosen, upload = install_upload(monkeypatch, saved_package)
    app = confirm_step(confirm_example(load_example_app()))
    before = app.session_state["lab_session"].performance.model_dump(mode="json")
    app.button(key="project_validate").click().run()
    assert not app.exception and not app.error
    assert app.button(key="project_restore").disabled
    chosen["file"] = upload(b"not a valid ZIP")
    app.run()
    assert "project_candidate" not in app.session_state
    app.button(key="project_validate").click().run()
    assert not app.exception and app.error
    assert app.session_state["lab_session"].performance.model_dump(mode="json") == before
    assert "project_candidate" not in app.session_state


def test_upload_validate_restore_archive_clear_and_restore_again(monkeypatch, saved_package):
    install_upload(monkeypatch, saved_package)
    app = AppTest.from_file(str(APP_PATH), default_timeout=20).run()
    app.button(key="project_validate").click().run()
    assert not app.exception and not app.error
    app.checkbox(key="project_restore_ack").check().run()
    app.button(key="project_restore").click().run()
    assert not app.exception and not app.error
    expected = load_project(saved_package)
    state = app.session_state["lab_session"]
    assert state.performance == expected.current.performance
    assert len(state.saved_experiments) == 1
    assert app.text_input(key="project_name").value == "中文本地项目"
    assert app.text_area(key="project_notes").value == expected.notes
    assert len(app.get("plotly_chart")) == 3
    assert "report_exports" not in app.session_state
    assert app.session_state["project_historical_report"] == expected.historical_report
    upload_generation = app.session_state["project_uploader_key"]
    assert upload_generation.startswith("project_upload_")
    assert app.session_state["analysis_uploader_key"].startswith("analysis_csv_")
    assert "project_candidate" not in app.session_state
    assert any(item.label == "下载历史报告 JSON" for item in app.get("download_button"))
    app.button(key="project_open_history").click().run()
    assert not app.exception and len(app.get("plotly_chart")) == 3
    app.button(key="project_remove_history").click().run()
    assert not app.exception
    assert not app.session_state["lab_session"].saved_experiments
    app.checkbox(key="project_clear_ack").check().run()
    app.button(key="project_clear").click().run()
    assert not app.exception
    assert app.session_state["lab_session"].raw_bytes is None
    assert app.radio(key="input_mode").value == "上传 CSV"
    assert app.text_input(key="project_name").value == "智调 Lab 项目"
    assert app.text_area(key="project_notes").value == ""
    assert not app.checkbox(key="project_clear_ack").value
    assert app.session_state["project_uploader_key"] != upload_generation
    assert "project_historical_report" not in app.session_state
    assert not app.get("plotly_chart")
    # 本机上传文件流还存在；显式再次校验/恢复，不自动恢复已清空数据。
    app.button(key="project_validate").click().run()
    app.checkbox(key="project_restore_ack").check().run()
    app.button(key="project_restore").click().run()
    assert not app.exception and not app.error
    assert app.session_state["lab_session"].performance == expected.current.performance


def test_current_unparsed_bytes_cannot_be_silently_omitted(saved_package):
    from ui.project_panel import _capture
    from ui.session import LabSession
    from projects.archive import ProjectError
    state = LabSession()
    state.set_source(b"bad CSV", "not-parsed.csv", {})
    state.saved_experiments = load_project(saved_package).saved_experiments
    with pytest.raises(ProjectError, match="尚未成功解析"):
        _capture({"lab_session": state})
