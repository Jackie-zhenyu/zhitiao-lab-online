"""AI聊天、逐请求摘要授权与程序化证据展示；不含演示模型或假回复。"""

from dataclasses import dataclass, field, replace
from pathlib import Path
import re

import streamlit as st

from agent.config import read_settings
from agent.models import AgentContext, AgentConversation
from agent.presentation import HYPOTHESES, VERIFICATIONS, LIMITATIONS, CLARIFICATIONS
from agent.privacy import consent_fingerprint, disclosure
from agent.provider import QwenProvider
from agent.runner import run_agent
from agent.tools import ToolError, ToolRegistry
from core.comparison import available_duration
from core.comparison_models import ExperimentConditions
from core.metric_models import NORMALIZATION
from ui.agent_context import available_views, build_context
from ui.session import LabSession


@dataclass
class AgentPanelState:
    context: AgentContext = field(default_factory=AgentContext)
    conversation: AgentConversation = field(default_factory=AgentConversation)
    pending_question: str = ""


def _render_answer(turn, context):
    st.write("**测得事实（数值由程序渲染）**")
    registry = ToolRegistry(context)
    for ref in turn.answer.measured_facts:
        value = registry.resolve(ref,request_id=turn.request_id)
        if ref.metric_key:
            display = "不可用" if value["value"] is None else f"{value['value']:.9g}"
            st.text(f"{value['name']}：{display} {value.get('unit') or ''} · {value['status']}")
            if value.get("reason"):
                st.caption(value["reason"])
        else:
            st.text(f"事件 {value['category']} · 原始行 {value['start_row']}–{value['end_row']} · {value['rule']}")
        st.caption(f"证据：{ref.result_id} / {ref.metric_key or ref.event_id}")
    if not turn.answer.measured_facts:
        st.caption("本轮没有经校验的数值事实。")
    if turn.answer.clarification:
        st.info(CLARIFICATIONS[turn.answer.clarification])
    st.write("**候选解释**")
    for candidate in turn.answer.candidate_explanations:
        st.write(HYPOTHESES[candidate.code])
    if not turn.answer.candidate_explanations:
        st.caption("本轮未提出有证据支持的候选解释。")
    st.write("**下一步验证**")
    for code in turn.answer.next_verification:
        st.write(VERIFICATIONS[code])
    st.write("**限制**")
    st.caption(NORMALIZATION)
    for code in dict.fromkeys(["observation_only","not_causal",*turn.answer.limitations]):
        st.write(LIMITATIONS[code])
    with st.expander("证据引用 · 展开程序结果"):
        for ref in turn.answer.evidence_references:
            st.json({"reference":ref.model_dump(),"evidence":registry.resolve(ref,request_id=turn.request_id)})


def _history(panel):
    for turn in panel.conversation.turns:
        with st.chat_message("user"):
            st.text(turn.question)
        with st.chat_message("assistant"):
            st.caption(turn.message)
            if turn.context_key!=panel.context.context_key:
                st.warning("实验、区间或配置已变化，本条旧结果已失效，请重新提问。")
                continue
            if turn.answer:
                try:
                    _render_answer(turn,panel.context)
                except (ToolError,ValueError):
                    st.warning("引用证据已失效，已拒绝显示旧数值，请重新分析。")
            with st.expander("本轮实际执行记录与工具结果"):
                if not turn.records:
                    st.caption("本轮未执行模型或分析工具。")
                for record in turn.records:
                    st.json(record.model_dump(exclude_none=True))
                for result_id in turn.result_ids:
                    evidence = panel.context.evidence.get(result_id)
                    if evidence is not None:
                        st.json(evidence.payload)


def render_agent_panel():
    st.divider()
    st.subheader("AI 分析 · 依据实际工具结果追问")
    settings,status = read_settings(Path(__file__).resolve().parents[1])
    configured = settings is not None and settings.ready
    if not configured:
        st.info("AI 未配置。全部本地导入、指标、证据和A/B对比仍可使用。")
        st.caption(status+" 请在本地 .env 或服务器环境填写配置，无需把密钥发到聊天中。")
    else:
        st.caption("服务端模型配置已读取；只有本轮明确授权后才发送。"+status)
    state = st.session_state.setdefault("lab_session",LabSession())
    panel = st.session_state.setdefault("agent_panel_state",AgentPanelState())
    views = available_views(state)
    can_analyze = bool(views)
    if views:
        choices = list(views)
        for key in ("ai_select_a","ai_select_b"):
            if st.session_state.get(key) not in [None,*choices]:
                del st.session_state[key]
        label = lambda key: "当前工作实验（字段已确认）" if key=="current" else state.saved_experiments[key].name+" · "+key[-8:]
        left,right = st.columns(2)
        a = left.selectbox("AI 实验 A",choices,format_func=label,key="ai_select_a")
        b = right.selectbox("AI 实验 B（可不选）",[None,*choices],format_func=lambda key:"不比较" if key is None else label(key),key="ai_select_b")
        st.caption("本轮仅授权所选A/B；文件名只在本地帮助选择，不发送模型。快照沿用保存时确认的指标和现象规则；未保存旧规则的快照使用明示默认规则。")
        with st.expander("本轮实验条件（仅本地核对）"):
            st.caption("初值来自保存快照。本轮可补充或修正；只发送已知/未知及程序比较结论，不发送条件原文。此处条件独立于上方本地对比表。")
            labels = {"plant":"被控对象","load":"负载","sampling":"采样设置","environment":"其他环境","controller":"控制器记录"}
            for selected in dict.fromkeys([a,b]):
                if selected is None:
                    continue
                values = {}
                for field_name,field_label in labels.items():
                    identity = views[selected].prepared.experiment.experiment_id
                    value = st.text_input(f"AI · {label(selected)} · {field_label}",value=getattr(views[selected].conditions,field_name) or "",key=f"ai_condition_{selected}_{identity}_{field_name}",max_chars=500)
                    values[field_name] = value.strip() or None
                views[selected] = replace(views[selected],conditions=ExperimentConditions(**values))
        duration = None
        if b is not None and views[a].saved is not None and views[b].saved is not None:
            try:
                maximum = min(available_duration(views[a].saved),available_duration(views[b].saved))
                duration = st.number_input("AI 对比共同观察时长上限 [s]",min_value=min(1e-9,maximum),max_value=float(maximum),value=float(maximum),key="ai_duration_"+a+"_"+b)
                st.caption("发送授权同时确认该共同窗口；模型可请求更短窗口，不能扩大到未授权区间。")
            except ValueError:
                st.warning("快照无法通过确认检查，请返回本地重新保存。")
                can_analyze = False
        try:
            panel.context = build_context(views,a,b,panel.context,allowed_duration=duration,max_events=settings.max_events if settings else 20)
        except ValueError as error:
            st.warning(str(error))
            can_analyze = False
            panel.context = AgentContext(session_id=panel.context.session_id)
    else:
        if panel.context.experiments:
            panel.context = AgentContext(session_id=panel.context.session_id)
        st.caption("先确认实验字段和单位；存在数据问题时可查询概要/证据，数值分析还需选择有效区间。")
    _history(panel)
    question = st.chat_input("例如：分析实验A；只看其中2～5秒；与实验B比较超调和响应时间",disabled=not configured or not can_analyze,max_chars=2000,key="agent_chat_input")
    if question:
        if re.search(r"\bsk-[\w-]{16,}",question) or (configured and settings.api_key.get_secret_value() in question):
            st.error("问题中可能含凭据，已拒绝保留或发送。请只填写实验问题。")
        else:
            panel.pending_question = question
    if panel.pending_question and configured and can_analyze:
        st.write("**发送前核对本轮摘要范围**")
        st.text(panel.pending_question)
        st.caption("将发送本轮问题、有限历史、所选实验的配置/范围，以及模型实际调用工具得到的必要指标或事件摘要。不会发送整份CSV、原始行值、文件名、备注、条件原文或本地路径。")
        st.caption("授权仅适用于本轮及显示的目的地址；后续追问、配置和选择变化需要重新授权。")
        with st.expander("查看将发送的摘要与最大范围",expanded=True):
            st.json(disclosure(panel.pending_question,panel.context,panel.conversation,settings))
        fingerprint = consent_fingerprint(panel.pending_question,panel.context,panel.conversation,settings)
        authorized = st.checkbox("我已核对目的地址和摘要范围，授权发送本轮问题及受限工具结果",key="ai_consent_"+fingerprint)
        send = st.button("授权并发送本轮请求",disabled=not authorized,type="primary",key="send_question")
        if send:
            progress = st.empty()
            run_agent(panel.pending_question,panel.context,panel.conversation,settings,QwenProvider(settings),authorized=authorized,consent_fingerprint=fingerprint,expected_fingerprint=fingerprint,on_record=lambda item:progress.info(item.detail))
            panel.pending_question = ""
            st.rerun()
    else:
        st.button("授权并发送本轮请求",disabled=True,key="send_question")
