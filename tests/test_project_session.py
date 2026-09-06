"""本地项目恢复的真实页面回归；不使用真实模型、网络或持久化会话身份。"""

from copy import deepcopy

import pytest
from streamlit.testing.v1 import AppTest

from projects.archive import ProjectData, ProjectError, export_project, load_project
from test_app import APP_PATH, confirm_example, load_example_app
from test_performance_ui import confirm_step
from test_stage4_ui import checkbox, compare, save_example
from ui.project_state import (
    apply_pending_project, capture_session, prepare_session, project_for_snapshot,
)


def _capture(app, **kwargs):
    return capture_session(app.session_state.filtered_state, "中文恢复项目", "中文备注", **kwargs)


def _restored(project, *, blocking=False):
    # AppTest注入应用内部待恢复对象，后续执行实际app及所有现有页面控件。
    app = AppTest.from_file(str(APP_PATH), default_timeout=20)
    app.session_state["project_pending_state"] = prepare_session(project)
    app.run()
    assert not app.exception
    if blocking:
        assert [notice.value for notice in app.error] == [
            "存在阻断性数据问题，禁止直接使用整份文件绘图。请选择一个连续有效区间；原始文件与其他区间仍完整保留。"
        ]
    else:
        assert not app.error
    return app


@pytest.fixture(scope="module")
def analyzed_project():
    app = confirm_step(confirm_example(load_example_app()))
    return _capture(app)


def test_roundtrip_restores_same_confirmed_baseline_result_and_rules(analyzed_project):
    expected = deepcopy(analyzed_project.current)
    project = load_project(export_project(analyzed_project))
    app = _restored(project)
    state = app.session_state["lab_session"]
    assert state.raw_bytes == expected.parsed.raw_bytes
    assert state.prepared.mapping == expected.prepared.mapping
    assert state.quality == expected.quality
    assert state.performance == expected.performance
    assert state.evidence == expected.evidence
    assert state.step_selection.y0_source == "baseline"
    assert state.step_selection.baseline_start_row == expected.performance.step.baseline_start_row
    assert app.radio(key="input_mode").value == "上传 CSV"
    assert app.radio(key="step_mode").value == "手动指定"
    assert len(app.get("plotly_chart")) == 3
    for page in ("实验对比", "报告预览", "实验分析"):
        app.radio(key="workspace_page").set_value(page).run()
        assert not app.exception
    assert state.performance == expected.performance
    assert state.source_metadata == project.current.metadata


@pytest.mark.parametrize("change", ["unit", "time_unit", "interval", "metric_rule", "quality_rule", "event_rule"])
def test_restored_page_keeps_existing_invalidation_contract(analyzed_project, change):
    app = _restored(deepcopy(analyzed_project))
    state = app.session_state["lab_session"]
    original_performance = deepcopy(state.performance)
    original_events = deepcopy(state.evidence)
    old_context = app.session_state["agent_panel_state"].context.context_key
    if change == "unit":
        next(w for w in app.text_input if w.label == "目标 / 实测共同单位").set_value("pulse").run()
    elif change == "time_unit":
        next(w for w in app.selectbox if w.label == "原始时间单位").set_value("ms").run()
    elif change == "interval":
        next(w for w in app.number_input if w.label == "结束数据行").set_value(100).run()
    elif change == "metric_rule":
        app.number_input(key="metric_band").set_value(.03).run()
    elif change == "quality_rule":
        app.number_input(key="interval_upper").set_value(3.).run()
    else:
        next(w for w in app.number_input if w.label == "波动观察窗 [s]").set_value(3.).run()
    assert not app.exception
    assert app.session_state["agent_panel_state"].context.context_key != old_context
    if change in ("unit", "time_unit", "quality_rule"):
        assert state.prepared is state.performance is state.evidence is None
    elif change == "event_rule":
        assert state.performance == original_performance
        assert state.evidence != original_events
        assert state.evidence.config.oscillation_window_s == 3.
    else:
        assert state.performance.response is None and state.step_selection is None


def test_manual_y0_and_nondefault_configs_restore_without_fabricating_baseline():
    app = confirm_example(load_example_app("closed_loop_pi_b"))
    app.number_input(key="metric_band").set_value(.03).run()
    app.number_input(key="metric_observation").set_value(.8).run()
    next(w for w in app.number_input if w.label == "起始数据行").set_value(51).run()
    app.radio(key="step_mode").set_value("手动指定").run()
    next(w for w in app.number_input if w.label.startswith("阶跃前目标 r0")).set_value(0.).run()
    next(w for w in app.radio if w.label == "响应初值 y0 来源").set_value("手动提供 y0").run()
    next(w for w in app.text_input if w.label.startswith("明确填写响应初值")).set_value("0.0").run()
    confirm_step(app)
    checkbox(app, "我已核对控制输出单位及上下限").check().run()
    next(w for w in app.text_input if w.label.startswith("合理物理变化率上限")).set_value(".3").run()
    checkbox(app, "我确认该变化率上限").check().run()
    next(w for w in app.number_input if w.label == "波动观察窗 [s]").set_value(3.).run()
    project = _capture(app)
    restored = _restored(load_project(export_project(project)))
    state = restored.session_state["lab_session"]
    assert state.performance == project.current.performance
    assert state.evidence == project.current.evidence
    assert state.step_selection.y0_source == "manual" and state.step_selection.y0 == 0.
    assert state.step_selection.baseline_start_row is None
    assert checkbox(restored, "我已核对控制输出单位及上下限").value
    assert checkbox(restored, "我确认该变化率上限").value
    assert state.evidence.config.output_limits.lower == -2.5


def test_unconfirmed_and_blocking_imports_remain_unconfirmed_or_blocking():
    unconfirmed = _capture(load_example_app())
    app = _restored(load_project(export_project(unconfirmed)))
    assert app.session_state["lab_session"].prepared is None
    assert not app.get("plotly_chart")
    assert not checkbox(app, "我确认目标值与实测值").value
    blocking = _capture(confirm_example(load_example_app("quality_issues")))
    app = _restored(load_project(export_project(blocking)), blocking=True)
    state = app.session_state["lab_session"]
    assert state.quality.blocking and state.performance is None
    assert state.evidence == blocking.current.evidence
    assert next(w for w in app.selectbox if w.label == "选择连续有效区间").value is None
    assert not app.get("plotly_chart")


def test_ab_conditions_window_and_snapshot_activation_survive_navigation():
    app = save_example()
    save_example(app, "closed_loop_pi_b")
    app.radio(key="workspace_page").set_value("实验对比").run()
    next(w for w in app.text_input if w.label == "A · 负载").set_value("本地记录：负载未知").run()
    next(w for w in app.number_input if w.label == "共同请求观察时长 [s]").set_value(10.).run()
    expected = deepcopy(compare(app))
    project = load_project(export_project(_capture(app)))
    first_id = expected.a.snapshot_id
    project = project_for_snapshot(project, first_id)
    restored = _restored(project)
    state = restored.session_state["lab_session"]
    assert state.performance == project.saved_experiments[first_id].performance
    assert state.comparison == expected
    for page in ("实验对比", "报告预览", "实验分析", "实验对比"):
        restored.radio(key="workspace_page").set_value(page).run()
        assert not restored.exception
        assert state.comparison == expected
    assert restored.selectbox(key="ab_selected_a").value == expected.a.snapshot_id
    assert restored.selectbox(key="ab_selected_b").value == expected.b.snapshot_id
    assert next(w for w in restored.number_input if w.label == "共同请求观察时长 [s]").value == 10.
    assert checkbox(restored, "我确认采用该共同观察时长").value
    next(w for w in restored.number_input if w.label == "共同请求观察时长 [s]").set_value(9.).run()
    assert state.comparison is None
    assert not checkbox(restored, "我确认采用该共同观察时长").value


def test_capture_and_prepare_isolate_old_chat_authorization_and_historical_report(analyzed_project):
    from test_report_ui import generate
    app = _restored(deepcopy(analyzed_project))
    bundle = generate(app)
    source = app.session_state.filtered_state
    source.update({"LLM_API_KEY": "TEST-KEY-NOT-REAL", "ai_consent_whatever": True,
                   "arbitrary_private_text": "should not persist"})
    project = capture_session(source, "新项目", "项目备注")
    target = prepare_session(project)
    assert target["project_historical_report"] == bundle.snapshot
    assert not {"agent_panel_state", "session_id", "report_exports", "report_draft",
                "LLM_API_KEY", "ai_consent_whatever", "arbitrary_private_text"} & target.keys()
    assert not any(key.startswith("ai_") for key in target)
    target["lab_session"].prepared.frame.iloc[0, 0] = 123
    assert project.current.prepared.frame.iloc[0, 0] != 123
    no_history = capture_session(source, "新项目", include_report=False)
    assert no_history.historical_report is None
    restored = _restored(project)
    assert restored.session_state["agent_panel_state"].context.session_id != source["agent_panel_state"].context.session_id
    assert not restored.session_state["agent_panel_state"].conversation.turns


def test_actual_test_agent_turn_is_only_historical_and_restore_never_sends(monkeypatch):
    from agent_fixtures import tool_reply, answer_latest
    from test_agent_ui import configured, submit, consent
    from test_report_ui import generate

    provider, _ = configured(monkeypatch)
    app = confirm_example(load_example_app())
    panel = app.session_state["agent_panel_state"]
    provider.steps.extend([tool_reply(panel.context), answer_latest])
    turn = submit(app, "分析实验A")
    assert turn.status == "success" and turn.result_ids
    bundle = generate(app)
    assert bundle.snapshot.data()["ai"]["status"] == "included"
    app.chat_input[0].set_value("继续分析").run()
    consent(app).check().run()
    assert len(provider.requests) == 2
    project = load_project(export_project(_capture(app)))
    restored = _restored(project)
    restored_panel = restored.session_state["agent_panel_state"]
    assert len(provider.requests) == 2  # 恢复不能复用发送授权或自动请求。
    assert restored_panel.context.session_id != panel.context.session_id
    assert restored_panel.pending_question == ""
    assert restored_panel.context.evidence == {}
    assert restored_panel.conversation.turns == []
    assert restored.button(key="send_question").disabled
    assert restored.session_state["project_historical_report"].data()["ai"]["status"] == "included"
    assert generate(restored).snapshot.data()["ai"]["status"] == "not_participated"


def test_same_filename_new_content_after_restore_revokes_current_results(analyzed_project, monkeypatch):
    from test_final_workflow import UploadedCSV, upload_bytes

    app = _restored(deepcopy(analyzed_project))
    state = app.session_state["lab_session"]
    original_sha = state.parsed.sha256
    uploaded = UploadedCSV(upload_bytes(), name=state.filename)
    monkeypatch.setattr("ui.import_page.st.file_uploader",
                        lambda label, *args, **kwargs: uploaded if label == "选择 CSV 文件" else None)
    app.run()
    assert not app.exception
    assert state.parsed.filename == analyzed_project.current.parsed.filename
    assert state.parsed.sha256 != original_sha
    assert state.prepared is state.performance is state.evidence is None
    assert state.step_selection is None and not state.confirmed_key
    assert not app.get("plotly_chart")


@pytest.mark.parametrize("field,value", [("tail_fraction", .005), ("relative_band", .995),
                                          ("baseline_window_s", .0001), ("rise_lower", .0001)])
def test_ui_cannot_represent_config_refuses_before_replacing_session(analyzed_project, field, value):
    project = deepcopy(analyzed_project)
    setattr(project.current.performance.config, field, value)
    original = {"private": object(), "lab_session": object()}
    expected = dict(original)
    with pytest.raises(ProjectError, match="页面控件范围"):
        original["project_pending_state"] = prepare_session(project)
    assert original == expected


def test_baseline_source_is_never_silently_replaced(analyzed_project):
    project = deepcopy(analyzed_project)
    project.current.performance.step.y0 = .123
    with pytest.raises(ProjectError, match="基线"):
        prepare_session(project)


def test_apply_is_prepared_first_then_clears_only_current_session(analyzed_project):
    old = {"lab_session": object(), "agent_panel_state": object(), "ai_consent_old": True}
    other_session = {"lab_session": object(), "private": "unchanged"}
    untouched = dict(other_session)
    old["project_pending_state"] = prepare_session(analyzed_project)
    apply_pending_project(old)
    assert "project_pending_state" not in old and "agent_panel_state" not in old
    assert other_session == untouched
    assert old["lab_session"].performance == analyzed_project.current.performance
    old["project_pending_state"] = {"workspace_page": "实验分析", "project_notice": "已清除"}
    apply_pending_project(old)
    assert old == {"workspace_page": "实验分析", "project_notice": "已清除"}
    broken = {"project_pending_state": "invalid", "private": "kept"}
    with pytest.raises(ProjectError):
        apply_pending_project(broken)
    assert broken["private"] == "kept"


def test_missing_snapshot_and_non_integer_mib_fail_without_mutation(analyzed_project):
    project = deepcopy(analyzed_project)
    with pytest.raises(ProjectError, match="不属于"):
        project_for_snapshot(project, "foreign-snapshot")
    project.current.parser_options.max_bytes -= 1
    with pytest.raises(ProjectError, match="整数 MiB"):
        prepare_session(project)


@pytest.mark.parametrize("change", ["missing_kind", "different_label"])
def test_source_declaration_cannot_change_during_first_render(analyzed_project, change):
    project = deepcopy(analyzed_project)
    if change == "missing_kind":
        del project.current.metadata["source_kind"]
    else:
        project.current.metadata["source_label"] = "改变了来源声明"
    with pytest.raises(ProjectError, match="来源"):
        prepare_session(project)
