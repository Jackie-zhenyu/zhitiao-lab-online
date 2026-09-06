"""评审与首次使用导览；快捷入口只载入原始示例，仍须正式确认。"""

import streamlit as st

from examples.catalog import load_example
from examples.closed_loop import load_closed_loop_example
from ui.session import LabSession


def _load_demo(example_id: str) -> None:
    """按钮回调先有界读取，再替换当前来源；失败不清除原工作实验。"""
    try:
        max_bytes = int(st.session_state.get("max_mib", 20)) * 1024 * 1024
        loader = load_closed_loop_example if example_id.startswith("closed_loop_") else load_example
        filename, raw, metadata = loader(example_id, max_bytes=max_bytes)
    except (OSError, ValueError):
        st.session_state["demo_load_error"] = "示例暂时无法载入，请检查示例文件和导入上限；当前实验未替换。"
        return

    state = st.session_state.setdefault("lab_session", LabSession())
    # 和左侧“加载示例”使用同一目录及解析管线；不生成任何分析结果。
    # 新一次演示从未确认状态开始，保留用户已保存的独立实验快照。
    state.clear()
    state.set_source(raw, filename, metadata)
    st.session_state.update({
        "input_mode": "加载示例", "lab_input_mode": "加载示例",
        "example_choice": example_id, "csv_encoding": "auto", "csv_delimiter": "auto",
    })
    st.session_state.pop("demo_load_error", None)


def render_demo_guide() -> None:
    """不增加主导航，不代替字段、单位或阶跃的用户确认。"""
    state = st.session_state.get("lab_session")
    empty = not isinstance(state, LabSession) or state.raw_bytes is None
    with st.expander("作品演示导览 · 从日志到可追溯报告", expanded=empty):
        st.markdown("**先看一份数据如何得出结论，再对比两次实验。**")
        st.caption("演示主线：加载示例 → 确认字段与单位 → 查看阶跃与证据 → A/B 对比 → 导出报告。")
        first, second, third = st.columns(3)
        with first:
            st.markdown("**01 / 看懂一次实验**")
            st.write("加载解析响应，在左侧核对字段与单位；确认阶跃后，查看程序计算的指标、定义和曲线标记。")
            st.button("加载解析响应", key="demo_first_order", on_click=_load_demo,
                      args=("first_order_step",), width="stretch")
            st.caption("解析函数合成数据，非实测。")
        with second:
            st.markdown("**02 / 追到问题位置**")
            st.write("加载问题数据并确认映射，查看阻断原因；选择连续有效区间，在“异常证据”中聚焦事件。")
            st.button("加载问题数据", key="demo_quality", on_click=_load_demo,
                      args=("quality_issues",), width="stretch")
            st.caption("人工注入问题的解析函数合成数据。规则命中不等于根因诊断。")
        with third:
            st.markdown("**03 / 对比并交付**")
            st.write("依次加载 A、B，各自确认阶跃并保存快照；进入“实验对比”，确认共同观察时长，再到“报告预览”导出。")
            left, right = st.columns(2)
            left.button("加载 PI A", key="demo_pi_a", on_click=_load_demo,
                        args=("closed_loop_pi_a",), width="stretch")
            right.button("加载 PI B", key="demo_pi_b", on_click=_load_demo,
                         args=("closed_loop_pi_b",), width="stretch")
            st.caption("闭环仿真数据，非实测。同一对象与条件，仅 PI 参数不同。")
        st.info("AI 追问在独立的“AI 分析”标签中：先核对发送摘要并授权，再查看工具调用和证据引用。AI 未配置时，全部本地分析与报告仍可用。")
        st.caption("快捷加载会替换当前工作实验，保留已保存快照；使用自动编码/分隔符，保留当前读取限制。字段、单位和阶跃仍须你确认。离开前可在左侧下载项目包。")
        if error := st.session_state.get("demo_load_error"):
            st.error(error)
