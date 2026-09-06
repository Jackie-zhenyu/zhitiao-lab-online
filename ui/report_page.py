"""报告预览与下载，仅在明确生成时抓取冻结结果。"""
import streamlit as st
from pathlib import Path
import pandas as pd

from agent.config import configured_secrets
from core.events import detect_events
from core.event_models import EventConfig
from core.metric_models import NORMALIZATION
from reports.snapshot import capture_run, capture_ai, create_report, metric_rows
from reports.generator import build_exports, export_filename, STATUS
from ui.agent_context import available_views
from ui.components import render_page_header
from ui.session import LabSession, fingerprint


def build_session_report(state,scope, *, project,name,notes,panel=None,secrets=()):
    available = available_views(state)
    runs,views = [],[]
    comparison = None
    inputs = None
    if scope=="current":
        if state.prepared is None or state.quality is None or not state.confirmed_key:
            raise ValueError("请先确认当前实验的字段和单位。")
        runs.append(capture_run(state.prepared,state.quality,state.performance,state.evidence,name=name,
            source_label=state.source_metadata.get("source_label",""),conditions=state.source_metadata.get("conditions",{})))
        if "current" in available: views.append(available['current'])
    elif scope=="comparison":
        comparison = state.comparison
        if comparison is None: raise ValueError("尚无有效A/B对比结果，请先确认共同窗口并计算。")
        inputs = tuple(state.saved_experiments[side.snapshot_id] for side in (comparison.a,comparison.b))
        for saved,side in zip(inputs,(comparison.a,comparison.b)):
            config = state.saved_event_configs.get(saved.snapshot_id,EventConfig())
            evidence = detect_events(saved.prepared,saved.quality,config)
            run = capture_run(saved.prepared,saved.quality,saved.performance,evidence,name=saved.name,
                source_label=saved.performance.source_label,conditions=side.conditions.model_dump())
            run['evidence_provenance'] = '用保存时的规则在报告生成时调用既有函数检测；不采用工作实验的新规则。'
            runs.append(run)
            views.append(available[saved.snapshot_id])
    else:
        raise ValueError("报告范围不支持。")
    ai = capture_ai(panel,views)
    return create_report(runs,comparison=comparison,comparison_inputs=inputs,ai=ai,project=project,notes=notes,secrets=secrets)


def render_report_page():
    render_page_header("报告预览","生成冻结快照，核对程序结果，再下载可离线查看的报告。")
    st.caption(NORMALIZATION)
    state = st.session_state.setdefault("lab_session",LabSession())
    choices = []
    if state.prepared is not None and state.quality is not None and state.confirmed_key: choices.append("current")
    if state.comparison is not None: choices.append("comparison")
    with st.sidebar:
        st.subheader("报告设置")
        project = st.text_input("项目名称",value="智调 Lab",max_chars=200,key="report_project")
        scope = st.selectbox("报告范围",choices or ["current"],format_func=lambda v:{"current":"当前实验（生成时结果）","comparison":"已确认A/B对比及两侧实验"}[v],disabled=not choices,key="report_scope")
        name = st.text_input("报告中的实验名称",value=state.filename or "未加载实验",max_chars=200,disabled=scope=="comparison",key="report_run_name_"+str(state.revision))
        notes = st.text_area("报告备注（原样作为文本记录）",max_chars=4000,key="report_notes")
        st.caption("未知条件留空；备注不作为程序计算事实。不得填入密钥。")
        generate = st.button("生成 / 更新报告快照",disabled=not choices,type="primary",key="generate_report")
    if not choices:
        st.info("尚无可生成报告的已确认数据。请先导入并确认字段和单位；数据不足时也可导出质量报告。")
    st.caption("HTML、JSON和指标CSV均取自同一快照；未包含完整原始CSV。HTML内嵌Plotly，无需CDN。")
    st.info("尚未实现：Word和PDF导出。")
    panel = st.session_state.get('agent_panel_state')
    draft = fingerprint({"scope":scope,"project":project,"name":name,"notes":notes,"source":state.source_key,
        "ai_context":panel.context.context_key if panel else None,
        "ai_requests":[turn.request_id for turn in panel.conversation.turns] if panel else [],
        "performance":state.performance.model_dump(mode="json") if state.performance else None,
        "evidence_key":state.evidence_key,"confirmed_key":state.confirmed_key,
        "comparison":state.comparison.model_dump(mode="json") if state.comparison else None})
    if generate:
        try:
            secrets = configured_secrets(Path(__file__).resolve().parents[1])
            snapshot = build_session_report(state,scope,project=project,name=name,notes=notes,panel=st.session_state.get('agent_panel_state'),secrets=secrets)
            st.session_state['report_exports'] = build_exports(snapshot)
            st.session_state['report_draft'] = draft
            st.success("报告快照已生成。所有下载内容已冻结，不会自动换成新配置。")
        except (ValueError,TypeError,KeyError):
            # 原报告不覆盖；不把异常中的配置/凭据/原始数据直接写进页面。
            st.error("报告未更新：来源、结果或输入未通过一致性/大小检查，请回到实验页核对并重新分析。旧快照保持原样。")
    bundle = st.session_state.get('report_exports')
    if bundle is None:
        st.button("下载 HTML 报告",disabled=True,key="export_report")
        return
    data = bundle.snapshot.data()
    if st.session_state.get('report_draft')!=draft:
        st.warning("页面数据、设置或报告备注已变化。下面仍是生成时的旧快照；如需新内容，请重新生成。")
    st.write("**冻结报告：** "+data['project'])
    st.caption(data['report_id']+' · '+data['created_at']+' · SHA-256 '+bundle.snapshot.sha256)
    for extension,label,mime,content in [('html','下载 HTML 报告','text/html',bundle.html),('json','下载结构化 JSON','application/json',bundle.json),('csv','下载指标 CSV','text/csv',bundle.csv)]:
        st.download_button(label,data=content,file_name=export_filename(bundle.snapshot,extension),mime=mime,key='download_report_'+extension,on_click='ignore')
    st.subheader("报告指标与配置核对")
    rows = metric_rows(data)
    if rows:
        display = pd.DataFrame(rows)[['experiment_name','scope','name','value','unit','status','start_s','end_s','result_id','configuration_id']]
        display['status'] = display['status'].map(lambda v:STATUS.get(v,v))
        st.dataframe(display.rename(columns={'experiment_name':'实验','scope':'范围','name':'指标','value':'数值','unit':'单位','status':'状态','start_s':'起点 [s]','end_s':'终点 [s]','result_id':'结果ID','configuration_id':'配置ID'}),hide_index=True,width='stretch')
    else: st.info("该快照仅包含质量结果，没有可用性能指标；指标CSV将仅含表头。")
    st.caption(data['ai']['message'])
    with st.expander("结构化快照 · 区间、配置与证据"):
        st.json(data)
    st.subheader("自包含 HTML 预览")
    # 当前已安装Streamlit提供iframe；仅传入由安全生成器构造的HTML。
    st.iframe(bundle.html,height=850)
