"""显式下载、校验与恢复本地项目；不自动保存、不写入用户指定路径。"""
import hashlib
from pathlib import Path
from uuid import uuid4

import streamlit as st

from agent.config import configured_secrets
from projects.archive import MAX_PACKAGE_BYTES, ProjectError, export_project, load_project, project_filename
from ui.project_state import capture_session, prepare_session, project_for_snapshot
from ui.session import LabSession, fingerprint


def workspace_stamp(ss):
    """只对来源摘要与已确认结果取指纹，避免每次重绘复制完整 CSV。"""
    state = ss.get("lab_session", LabSession())
    report = ss.get("report_exports")
    history = ss.get("project_historical_report")
    return fingerprint({
        "name": ss.get("project_name", "智调 Lab 项目"), "notes": ss.get("project_notes", ""),
        "source": state.source_key, "parse": state.parse_key, "confirmed": state.confirmed_key,
        "performance": state.performance.model_dump(mode="json") if state.performance else None,
        "evidence": state.evidence.model_dump(mode="json") if state.evidence else None,
        "saved": list(state.saved_experiments),
        "rules": {k: v.model_dump(mode="json") for k, v in state.saved_event_configs.items()},
        "comparison": state.comparison.model_dump(mode="json") if state.comparison else None,
        "report": report.snapshot.sha256 if report else history.sha256 if history else None,
    })


def _capture(ss):
    state = ss.get("lab_session", LabSession())
    if state.raw_bytes is not None and state.parsed is None:
        raise ProjectError("当前文件尚未成功解析。请修正导入设置或明确清除该文件后保存，避免遗漏数据。")
    return capture_session(ss, ss.get("project_name", "智调 Lab 项目"), ss.get("project_notes", ""))


def _replace(project, notice):
    pending = prepare_session(project)
    pending["project_notice"] = notice
    _queue_replacement(pending)


def _queue_replacement(pending):
    # 清除服务端状态还不足以清除浏览器保留的文件控件；新身份隔离旧上传。
    token = uuid4().hex
    pending["analysis_uploader_key"] = "analysis_csv_" + token
    pending["project_uploader_key"] = "project_upload_" + token
    st.session_state["project_pending_state"] = pending
    st.rerun()


def render_project_panel():
    ss = st.session_state
    state = ss.setdefault("lab_session", LabSession())
    with st.sidebar.expander("项目 · 保存与恢复", key="project_panel"):
        if ss.get("project_notice"):
            st.success(ss["project_notice"])
        st.text_input("本地项目名称", value="智调 Lab 项目", max_chars=200, key="project_name")
        st.text_area("项目备注", max_chars=4000, key="project_notes")
        st.caption("项目包含完整原始 CSV，未加密。显式下载后才保存到本机；请按实验资料保管。不会保存密钥、AI 授权或活动聊天证据。")
        has_content = bool(state.parsed or state.saved_experiments or ss.get("report_exports") or ss.get("project_historical_report"))
        if st.button("生成项目包（含原始 CSV）", key="project_export", disabled=not has_content):
            try:
                with st.spinner("正在核验原始数据、配置与结果…"):
                    raw = export_project(_capture(ss), secrets=configured_secrets(Path(__file__).resolve().parents[1]))
                ss["project_package"] = (raw, workspace_stamp(ss))
            except (ValueError, TypeError):
                # 输入或重算失败不得把原文件名、备注或潜在凭据拼入错误。
                st.error("项目包未生成：请核对当前文件已成功解析，配置与结果一致，且不含凭据。包上限及排查方法见 docs/PROJECTS.md。")
        package = ss.get("project_package")
        if package:
            raw, stamp = package
            if stamp != workspace_stamp(ss):
                st.warning("页面已变化。下方下载仍是上次生成的项目快照；保存最新内容请重新生成项目包。")
            st.download_button("下载项目包 .labproj", raw, file_name=project_filename(raw), mime="application/zip", key="project_download", on_click="ignore")
            st.caption(f"冻结项目包 · {len(raw):,} 字节 · SHA-256 {hashlib.sha256(raw).hexdigest()}")
        st.divider()
        uploaded = st.file_uploader("选择项目包", type=["labproj"], max_upload_size=MAX_PACKAGE_BYTES // (1024 * 1024), key=ss.get("project_uploader_key", "project_upload"))
        upload_id = getattr(uploaded, "file_id", id(uploaded)) if uploaded is not None else None
        candidate = ss.get("project_candidate")
        if candidate and candidate[0] != upload_id:
            ss.pop("project_candidate", None)
            candidate = None
        if st.button("校验项目包", disabled=uploaded is None, key="project_validate"):
            ss.pop("project_candidate", None)
            ss.pop("project_restore_ack", None)
            try:
                uploaded.seek(0)
                with st.spinner("校验包结构并重新计算实验结果…"):
                    project = load_project(uploaded, secrets=configured_secrets(Path(__file__).resolve().parents[1]))
                    prepare_session(project)  # 控件范围也须验证；失败不触碰工作会话。
                candidate = (upload_id, project)
                ss["project_candidate"] = candidate
            except (ValueError, TypeError):
                candidate = None
                st.error("项目包校验未通过：格式、摘要、版本、配置或数值核验失败，或超出本机限制。当前实验未替换。")
        if candidate:
            project = candidate[1]
            st.text("待恢复项目：" + project.name)
            st.caption(f"工作实验：{'有' if project.current else '无'}；历史快照：{len(project.saved_experiments)}；A/B 结果：{'有' if project.comparison else '无'}。")
            st.caption("恢复核验确认计算一致，不能鉴定数据来源真实性或人工填写的实验条件。")
            ack = st.checkbox("确认替换当前会话；未下载的数据将丢失", key="project_restore_ack")
            if st.button("恢复项目（替换当前会话）", disabled=not ack, key="project_restore"):
                try:
                    _replace(project, "项目已恢复并重新核验。AI 授权和活动聊天证据已清空。")
                except (ValueError, TypeError):
                    st.error("项目无法恢复到当前界面，原会话未替换。")
        if state.saved_experiments:
            st.divider()
            st.write("实验快照历史")
            chosen = st.selectbox("选择历史快照", list(state.saved_experiments), format_func=lambda key: state.saved_experiments[key].name, key="project_history")
            saved = state.saved_experiments[chosen]
            st.caption(f"来源摘要 {saved.prepared.parsed.sha256[:16]} · {saved.performance.source_label}")
            if st.button("载入快照为工作实验", key="project_open_history"):
                try:
                    _replace(project_for_snapshot(_capture(ss), chosen), "已载入历史快照为工作实验；原历史快照保留，AI 证据重新建立。")
                except (ValueError, TypeError):
                    st.error("历史快照载入失败，当前实验未替换；请先检查导入状态。")
            if st.button("移除所选历史快照", key="project_remove_history"):
                state.saved_experiments.pop(chosen)
                state.saved_event_configs.pop(chosen, None)
                state.comparison = None
                state.comparison_key = ""
                for key in ("project_history", "ab_selected_a", "ab_selected_b", "ab_remove_id"):
                    ss.pop(key, None)
                st.rerun()
        history = ss.get("project_historical_report")
        if history:
            st.caption("历史报告为导入的只读 JSON 归档。摘要校验不等于作者认证或事实核实；不作为活动 AI 证据。新报告请从报告预览重新生成。")
            st.download_button("下载历史报告 JSON", history.json_text.encode("utf-8"), file_name="lab-history-" + history.sha256[:20] + ".json", mime="application/json", key="project_history_report", on_click="ignore")
        st.divider()
        clear = st.checkbox("确认清空当前会话中的实验、快照和聊天", key="project_clear_ack")
        if st.button("清空当前会话", disabled=not clear, key="project_clear"):
            _queue_replacement({
                "workspace_page": "实验分析", "input_mode": "上传 CSV", "lab_input_mode": "上传 CSV",
                "project_name": "智调 Lab 项目", "project_notes": "", "project_clear_ack": False,
                "project_notice": "当前会话已清空。已下载的本机文件和其他会话不受影响。",
            })
