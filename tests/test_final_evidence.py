"""第七阶段交付回归：失效证据、报告脱敏和原生/响应区间，全部离线。"""

from copy import deepcopy
import json

import pytest

from agent.models import EvidenceRef, ToolExperiment
from agent.tools import ToolError, ToolRegistry
from agent_fixtures import context, ScriptedProvider, tool_reply, answer_latest
from core.event_models import EventConfig
from reports.snapshot import capture_ai
from test_agent_runner import run as run_agent_test
from test_agent_ui import configured, consent, submit
from test_app import load_example_app, confirm_example
from test_report_ui import generate
from test_stage4_regressions import saved_fixture
from ui.agent_panel import AgentPanelState


def test_redaction_secret_loading_is_bounded_local_and_respects_empty_environment(tmp_path, monkeypatch):
    from agent.config import configured_secrets, read_settings
    secret = "FILE-ONLY-TEST-CREDENTIAL"
    (tmp_path / ".env").write_text("LLM_MODEL=\nLLM_API_KEY=" + secret + "\n", encoding="utf-8")
    monkeypatch.delenv("LLM_API_KEY")
    assert read_settings(tmp_path)[0] is None
    assert configured_secrets(tmp_path) == (secret,)
    child = tmp_path / "child"
    child.mkdir()
    assert configured_secrets(child) == ()  # 不查找父目录 .env。
    monkeypatch.setenv("LLM_API_KEY", "")
    assert configured_secrets(tmp_path) == ()
    monkeypatch.setenv("LLM_API_KEY", "ENV-ONLY-TEST-CREDENTIAL")
    assert configured_secrets(tmp_path) == ("ENV-ONLY-TEST-CREDENTIAL",)
    monkeypatch.delenv("LLM_API_KEY")
    (tmp_path / ".env").write_bytes(b"#" + b"x" * 65536 + b"\nLLM_API_KEY=" + secret.encode())
    assert configured_secrets(tmp_path) == ()
    assert "64 KiB" in read_settings(tmp_path)[1]


def test_report_redacts_present_key_even_when_other_model_configuration_is_invalid(monkeypatch):
    # 非 sk- 形态的明确测试凭据；无效模型配置必须继续禁用 AI。
    secret = "DELIVERY-TEST-CREDENTIAL-NOT-REAL"
    monkeypatch.setenv("LLM_PROVIDER", "qwen")
    monkeypatch.setenv("LLM_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
    monkeypatch.setenv("LLM_MODEL", "")
    monkeypatch.setenv("LLM_API_KEY", secret)
    app = confirm_example(load_example_app())
    assert app.chat_input[0].disabled
    assert any("AI 未配置" in message.value for message in app.info)
    app.sidebar.radio[0].set_value("报告预览").run()
    app.text_area(key="report_notes").set_value("测试备注 " + secret).run()
    next(item for item in app.text_input if item.key.startswith("report_run_name_")).set_value(secret).run()
    app.button(key="generate_report").click().run()
    assert not app.exception and not app.error
    bundle = app.session_state["report_exports"]
    assert secret not in bundle.snapshot.json_text
    assert secret not in bundle.html
    assert secret not in bundle.csv.decode("utf-8-sig")
    assert bundle.snapshot.data()["privacy"]["redacted_text_fields"] >= 2
    assert bundle.snapshot.data()["ai"]["status"] == "not_participated"


def test_agent_comparison_response_evidence_starts_at_confirmed_t0_not_first_post_sample():
    # 等间隔原始记录；t0=0在-.5与.5之间。跟踪[.5,3.5]，响应[0,3.5]。
    saved = saved_fixture([-1.5, -.5, .5, 1.5, 2.5, 3.5], name="non-grid-trigger")
    view = ToolExperiment(saved.prepared, saved.quality, saved.performance.config,
                          EventConfig(), 1, 6, saved.performance, saved,
                          conditions=saved.conditions)
    ctx = context()
    identifier = saved.prepared.experiment.experiment_id
    ctx.experiments = {identifier: view}
    ctx.aliases = {"A": identifier, "B": identifier}
    ctx.allowed_comparison_duration_s = 3.5
    payload = ToolRegistry(ctx).invoke("compare_runs", json.dumps({
        "experiment_a": identifier, "experiment_b": identifier,
    }), request_id="non-grid-request")
    for side in ("A", "B"):
        assert payload["facts"][side + ".rmse_t"]["interval"] == {"start_s": .5, "end_s": 3.5}
        for key in ("rise_time", "overshoot_pct", "settling_time"):
            assert payload["facts"][side + "." + key]["interval"] == {"start_s": 0., "end_s": 3.5}
    assert payload["facts"]["delta.overshoot_pct"]["value"] == 0.


@pytest.mark.parametrize("change", ["raw", "unit", "time_unit", "range", "metric_threshold", "event_threshold"])
def test_changed_data_or_configuration_rejects_old_tool_and_exported_ai_evidence(change):
    ctx = context()
    turn, ctx, history = run_agent_test(ScriptedProvider(tool_reply(ctx), answer_latest), ctx)
    ref = turn.answer.measured_facts[0]
    panel = AgentPanelState(context=ctx, conversation=history)
    views = deepcopy(list(ctx.experiments.values()))
    assert capture_ai(panel, views)["status"] == "included"
    view = ctx.experiments[ctx.aliases["A"]]
    if change == "raw":
        view.prepared.parsed.raw_bytes += b"6,1,0\n"
    elif change == "unit":
        view.prepared.mapping.unit = "rad"
    elif change == "time_unit":
        view.prepared.mapping.time_unit = "ms"
    elif change == "range":
        view.start_row = 3
    elif change == "metric_threshold":
        view.metric_config.relative_band = .03
    else:
        view.event_config.oscillation_window_s = 3.
    with pytest.raises(ToolError):
        ToolRegistry(ctx).resolve(ref, request_id=turn.request_id)
    assert capture_ai(panel, views)["status"] == "not_participated"


@pytest.mark.parametrize("change", ["file", "unit", "range", "metric_threshold", "event_threshold"])
def test_page_changes_revoke_pending_consent_chat_evidence_and_new_report_ai(monkeypatch, change):
    provider, _ = configured(monkeypatch)
    app = confirm_example(load_example_app())
    panel = app.session_state["agent_panel_state"]
    provider.steps.extend([tool_reply(panel.context), answer_latest])
    previous = submit(app, "分析实验A")
    old_ref = previous.answer.measured_facts[0]
    old_context = panel.context.context_key
    old_report = generate(app)
    assert old_report.snapshot.data()["ai"]["status"] == "included"
    app.sidebar.radio[0].set_value("实验分析").run()
    app.chat_input[0].set_value("继续分析").run()
    consent(app).check().run()
    if change == "file":
        app.selectbox(key="example_choice").set_value("irregular_sampling").run()
        app.button(key="load_example").click().run()
    elif change == "unit":
        next(item for item in app.text_input if item.key.startswith("physical_unit_")).set_value("rad").run()
    elif change == "range":
        next(item for item in app.number_input if item.key.startswith("start_")).set_value(21).run()
    elif change == "metric_threshold":
        app.number_input(key="metric_band").set_value(.03).run()
    else:
        next(item for item in app.number_input if item.label == "波动观察窗 [s]").set_value(3.).run()
    assert not app.exception
    assert panel.context.context_key != old_context
    assert old_ref.result_id not in panel.context.evidence
    assert app.button(key="send_question").disabled
    assert len(provider.requests) == 2
    assert any("旧结果已失效" in message.value for message in app.warning)
    if change in ("file", "unit"):
        confirm_example(app)
    new_report = generate(app)
    assert new_report.snapshot.data()["ai"]["status"] == "not_participated"
    assert old_report.snapshot.data()["ai"]["status"] == "included"


def test_unowned_result_from_identical_experiment_in_other_session_is_rejected():
    source = context()
    target = deepcopy(source)
    target.session_id = "a-different-application-session"
    payload = ToolRegistry(source).invoke("analyze_run", json.dumps({
        "experiment_id": source.aliases["A"],
    }), request_id="source-request")
    target.evidence = deepcopy(source.evidence)
    with pytest.raises(ToolError, match="其他会话"):
        ToolRegistry(target).resolve(EvidenceRef(result_id=payload["result_id"], metric_key="rmse_t"),
                                     request_id="source-request")
