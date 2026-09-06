"""托管入口的真实页面流程；沿用离线网络与空密钥测试约束。"""

from pathlib import Path

from streamlit.testing.v1 import AppTest


ROOT = Path(__file__).resolve().parents[1]


def test_hosted_upload_notice_remains_visible_on_every_page():
    app = AppTest.from_file(str(ROOT / "cloud_app.py"), default_timeout=15).run()
    assert not app.exception
    assert any("在线实验工作台" in item.value for item in app.sidebar.markdown)
    for page in ("实验分析", "实验对比", "报告预览"):
        app.sidebar.radio(key="workspace_page").set_value(page).run()
        assert not app.exception
        assert app.title[0].value == page
        # The disclosure precedes the page controls on each main entry.
        notice = app.info[0].value
        assert "CSV 会上传到托管服务器" in notice
        assert "仅在当前会话内处理" in notice
        assert "刷新页面或服务器重启可能丢失" in notice
        assert "主动下载项目包和报告" in notice
        assert any("在线评审版" in item.value for item in app.caption)


def test_hosted_without_model_still_imports_and_analyzes_public_example():
    app = AppTest.from_file(str(ROOT / "cloud_app.py"), default_timeout=15).run()
    assert not app.exception
    assert any("AI 未配置" in item.value for item in app.info)
    assert app.chat_input[0].disabled
    assert app.button(key="send_question").disabled
    app.button(key="demo_first_order").click().run()
    assert not app.exception
    assert app.session_state["lab_session"].prepared is None
    app.checkbox[0].check().run()
    app.button(key="confirm_mapping").click().run()
    assert not app.exception
    state = app.session_state["lab_session"]
    assert state.quality.issues == []
    assert state.performance is not None
    assert len(app.get("plotly_chart")) == 3
    assert any("CSV 会上传到托管服务器" in item.value for item in app.info)
    assert app.button(key="send_question").disabled


def test_local_entry_keeps_local_label_without_hosted_upload_notice():
    app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=15).run()
    assert not app.exception
    assert any("本地实验工作台" in item.value for item in app.sidebar.markdown)
    assert any("本地交付版" in item.value for item in app.caption)
    assert not any("在线评审版" in item.value for item in app.caption)
    assert not any("CSV 会上传到托管服务器" in item.value for item in app.info)
