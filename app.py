"""智调 Lab 实验工作台；本地与在线入口共用分析流程。"""

import streamlit as st

from ui.components import apply_theme, render_sidebar
from ui.pages import render_analysis, render_comparison, render_report
from ui.session import preserve_import_widgets
from ui.agent_panel import render_agent_panel
from ui.project_panel import render_project_panel
from ui.project_state import apply_pending_project


def main(*, hosted: bool = False) -> None:
    apply_pending_project(st.session_state)
    st.set_page_config(
        page_title="智调 Lab · 实验工作台",
        page_icon="🧪",
        layout="wide",
        initial_sidebar_state="auto",
    )
    apply_theme()
    selected_page = render_sidebar(hosted=hosted)
    preserve_import_widgets(st.session_state)
    pages = {
        "实验分析": render_analysis,
        "实验对比": render_comparison,
        "报告预览": render_report,
    }
    if hosted:
        st.info(
            "在线评审提示：CSV 会上传到托管服务器；应用仅在当前会话内处理数据，不自动保存。"
            "刷新页面或服务器重启可能丢失会话，请主动下载项目包和报告。"
        )
    workspace, ai = st.tabs(["实验工作台", "AI 分析"])
    with workspace:
        pages[selected_page]()
    with ai:
        render_agent_panel()
    render_project_panel()
    st.divider()
    if hosted:
        st.caption("智调 Lab · 在线评审版｜分析在托管服务器运行，AI 配置后经逐轮授权调用工具。")
    else:
        st.caption("智调 Lab · 本地交付版｜本地分析与报告无需 API Key，AI 经授权调用工具。")


if __name__ == "__main__":
    main()
