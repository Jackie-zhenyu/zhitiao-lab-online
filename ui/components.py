"""页面共享的导航、说明和轻量样式。"""

import streamlit as st
from functools import wraps


def sidebar_controls(function):
    """把已有控制组放进左侧，保持输入键和核心调用行为。"""
    @wraps(function)
    def wrapped(*args, **kwargs):
        with st.sidebar:
            return function(*args, **kwargs)
    return wrapped


def apply_theme() -> None:
    """仅调整留白与字体；保持原生控件和键盘交互。"""
    st.html(
        """
        <style>
        .stMainBlockContainer {max-width: 1280px; padding-top: 2.4rem;}
        [data-testid="stSidebar"] {border-right: 1px solid #dee7e2;}
        [data-testid="stSidebar"] [data-testid="stHorizontalBlock"] {flex-direction:column;gap:.4rem;}
        [data-testid="stSidebar"] [data-testid="stColumn"] {width:100%!important;flex:1 1 100%!important;min-width:0!important;}
        button p, label p, summary p {white-space:normal;overflow-wrap:anywhere;}
        [data-testid="stMetricValue"] {font-size:1.6rem;overflow-wrap:anywhere;}
        @media(max-width:760px){
          .stMainBlockContainer{padding:4.5rem .8rem 1rem;}
          [data-testid="stHorizontalBlock"]{flex-wrap:wrap;gap:.7rem;}
          [data-testid="stColumn"]{min-width:100%!important;flex:1 1 100%!important;}
          h1{font-size:1.7rem;}h2{font-size:1.4rem;}
        }
        h1 {letter-spacing: -0.035em;}
        h2, h3 {letter-spacing: -0.015em;}
        </style>
        """
    )


def render_sidebar(*, hosted: bool = False) -> str:
    with st.sidebar:
        st.markdown("## 智调 Lab")
        st.caption("控制 · 电机 · 机器人实验")
        st.divider()
        selected_page = st.radio(
            "工作台", ["实验分析", "实验对比", "报告预览"], key="workspace_page"
        )
        st.divider()
        st.markdown("**在线实验工作台**" if hosted else "**本地实验工作台**")
        st.caption("左侧配置 · 主区查看结果 · 独立 AI 分析")
        st.caption("数据按会话隔离；AI 每轮发送前核对摘要并授权。")
        st.caption("闭环示例：闭环仿真数据，非实测。")
    return selected_page


def render_page_header(title: str, description: str) -> None:
    st.caption("智调 LAB / 实验工作台")
    st.title(title)
    st.write(description)
    st.caption("程序计算与可追溯报告；沿用已确认指标口径。")
    if title == "实验分析":
        from ui.demo_guide import render_demo_guide

        render_demo_guide()


def render_unimplemented(features: str) -> None:
    st.info(f"尚未实现：{features}。")
