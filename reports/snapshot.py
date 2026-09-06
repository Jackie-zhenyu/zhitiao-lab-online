"""将生成时的已核验结果冻结为JSON；不保存原始CSV或引用可变会话对象。"""

from dataclasses import dataclass, asdict
from datetime import datetime, timezone
import hashlib
import json
import re
from copy import deepcopy
from uuid import uuid4

from core.import_models import ParserOptions
from core.parser import parse_csv
from core.quality import prepare_experiment, check_quality, select_interval
from core.performance import analyze_performance, DEFINITIONS
from core.events import detect_events
from core.comparison import compare_experiments
from core.metric_models import NORMALIZATION
from ui.charts import build_charts


REPORT_VERSION = "lab-report-v6.0"
MAX_JSON_BYTES = 32 * 1024 * 1024


def _json(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,allow_nan=False,
                      default=lambda obj:obj.model_dump(mode="json"))


def fingerprint(value):
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ReportSnapshot:
    json_text: str
    sha256: str

    def data(self):
        if hashlib.sha256(self.json_text.encode("utf-8")).hexdigest()!=self.sha256:
            raise ValueError("报告快照摘要不匹配")
        return json.loads(self.json_text)  # 每次返回独立副本。


def freeze(payload, *, secrets=()):
    removed = 0
    def redact(value):
        nonlocal removed
        if isinstance(value,str):
            original = value
            for secret in secrets:
                if secret:
                    value = value.replace(secret,"[凭据已移除]")
            value = re.sub(r"\bsk-[\w-]{16,}","[疑似凭据已移除]",value)
            removed += int(original!=value)
            return value
        if isinstance(value,list): return [redact(v) for v in value]
        if isinstance(value,dict): return {redact(k):redact(v) for k,v in value.items()}
        return value
    payload = redact(json.loads(_json(payload)))
    payload["privacy"] = {"redacted_text_fields":removed,"raw_csv_included":False}
    encoded = _json(payload)
    if len(encoded.encode("utf-8"))>MAX_JSON_BYTES:
        raise ValueError("报告结构超过32 MiB上限；未静默省略问题或事件，请缩小报告范围。")
    return ReportSnapshot(encoded,hashlib.sha256(encoded.encode("utf-8")).hexdigest())


def _verified(prepared, quality, performance, evidence):
    """复用既有解析/检查/计算函数验证来源一致性，不定义新的数值算法。"""
    parsed = prepared.parsed
    rebuilt = parse_csv(parsed.raw_bytes,parsed.filename,ParserOptions(
        encoding=parsed.encoding,delimiter=parsed.delimiter,
        max_bytes=max(1,len(parsed.raw_bytes)),max_rows=max(1,len(parsed.frame))))
    # 复核解析采用更紧的字节/行上限；保留原导入操作记录，避免把复核上限当作用户配置。
    canonical = prepare_experiment(parsed,prepared.mapping,prepared.experiment.source.kind)
    if (rebuilt.sha256!=parsed.sha256 or not rebuilt.frame.equals(parsed.frame)
        or rebuilt.source_lines!=parsed.source_lines or not canonical.frame.equals(prepared.frame)
        or canonical.experiment!=prepared.experiment or canonical.operations!=prepared.operations):
        raise ValueError("原始摘要、数据或转换记录不一致，请重新导入分析。")
    checked = check_quality(canonical,quality.config)
    if checked!=quality:
        raise ValueError("质量结果与当前数据或配置不一致。")
    if performance is not None:
        p = performance
        fresh = analyze_performance(canonical,checked,p.start_row,p.end_row,p.config,
            source_label=p.source_label,step=p.step,step_start_row=p.step_start_row,step_end_row=p.step_end_row)
        if fresh.model_dump(mode="json")!=p.model_dump(mode="json"):
            raise ValueError("分析结果与当前字段、区间或配置不一致，拒绝混合导出。")
    if evidence is not None:
        if detect_events(canonical,checked,evidence.config)!=evidence:
            raise ValueError("异常证据与当前数据或规则不一致。")


def _source_label(prepared, declared):
    if prepared.experiment.source.kind.value=="simulated" and declared in ("解析函数合成数据","闭环仿真数据，非实测"):
        return declared
    return {"measured":"实测数据（用户声明，未经独立鉴定）","simulated":"合成或仿真数据（用户声明，具体类型未知）","unknown":"来源未知，未冒称实测"}[prepared.experiment.source.kind.value]


def capture_run(prepared, quality, performance, evidence, *, name, source_label, conditions=None, notes=""):
    _verified(prepared,quality,performance,evidence)
    p = performance.model_dump(mode="json") if performance is not None else None
    e = evidence.model_dump(mode="json") if evidence is not None else None
    context = {"source":prepared.experiment.source.model_dump(mode="json"),"mapping":prepared.mapping.model_dump(mode="json"),
               "quality":asdict(quality),"performance":p,"evidence":e}
    result_id = "report-result-"+fingerprint(context)
    charts = []
    if performance:
        frame = select_interval(prepared,quality,performance.start_row,performance.end_row)
        charts = [{"title":title,"figure":json.loads(fig.to_json())} for title,fig in build_charts(frame,prepared.mapping,5000,response=performance.response)]
    return {"result_id":result_id,"experiment_id":prepared.experiment.experiment_id,"name":name,"notes":notes,
            "source_label":_source_label(prepared,source_label),"conditions":conditions or {},**context,
            "operations":list(prepared.operations),"parser":{"encoding":prepared.parsed.encoding,"delimiter":prepared.parsed.delimiter},
            "configuration_id":fingerprint({"mapping":context["mapping"],"quality":quality.config.model_dump(),"metric":p["config"] if p else None,"event":e["config"] if e else None}),
            "charts":charts,"chart_policy":"每条最多5000个原始点，仅影响显示；不改变指标计算，可能遗漏尖峰。"}


def capture_ai(panel, allowed_views):
    """只收录与报告数据/配置仍匹配的已校验AI结果；解释引用可有自己的较小区间。"""
    from agent.models import AgentAnswer
    from agent.privacy import view_fingerprint
    from agent.runner import _validate_answer, explicit_range
    from agent.tools import ToolRegistry, ToolError
    from core.comparison_models import ExperimentConditions
    if panel is None:
        return {"status":"not_participated","message":"AI 未参与","turns":[]}
    registry = ToolRegistry(panel.context)
    entries = []
    for turn in panel.conversation.turns:
        if turn.status!="success" or turn.answer is None or turn.context_key!=panel.context.context_key:
            continue
        try:
            answer = _validate_answer(AgentAnswer.model_validate(turn.answer).model_dump_json(),registry,turn.request_id,explicit_range(turn.question))
            if not answer.measured_facts:
                continue
            records = {}
            for ref in answer.evidence_references:
                record = panel.context.evidence[ref.result_id]
                for identifier in record.experiment_ids:
                    actual = panel.context.experiments[identifier]
                    # 条件可独立于本地对比填写，因此原文和配置在AI证据区单独保留，不能冒充主表条件。
                    copy_actual = deepcopy(actual)
                    copy_actual.conditions = ExperimentConditions()
                    matched = False
                    for view in allowed_views:
                        if view.prepared.experiment.experiment_id!=identifier: continue
                        copy_view = deepcopy(view)
                        copy_view.conditions = ExperimentConditions()
                        if view_fingerprint(copy_actual)==view_fingerprint(copy_view): matched = True
                    if not matched: raise ValueError("AI配置已失效或不属于报告实验")
                records[ref.result_id] = {"payload":record.payload,"facts":record.facts,"events":record.events,
                    "context_key":record.context_key,"experiment_fingerprints":record.experiment_fingerprints,
                    "conditions":{i:panel.context.experiments[i].conditions.model_dump() for i in record.experiment_ids}}
            entries.append({"request_id":turn.request_id,"question":turn.question,"answer":answer.model_dump(mode="json"),"evidence":records})
        except (ValueError,TypeError,KeyError,ToolError):
            continue
    return {"status":"included" if entries else "not_participated","message":"AI辅助解释，事实仍由程序计算" if entries else "AI 未参与（无适用且仍有效的AI结果）","turns":entries}


def create_report(runs, *, comparison=None, comparison_inputs=None, ai=None, project="智调 Lab", notes="", secrets=()):
    if not runs:
        raise ValueError("没有已确认字段的数据，不能生成空实验报告。")
    if not isinstance(project,str) or not project.strip() or len(project)>200 or not isinstance(notes,str) or len(notes)>4000:
        raise ValueError("项目名称须为1–200字，报告备注最多4000字。")
    comparison_data = None
    comparison_charts = []
    if comparison is not None:
        if comparison_inputs is None:
            raise ValueError("缺少对比所用的实验快照。")
        a,b = comparison_inputs
        fresh = compare_experiments(a,b,comparison.config,comparison.a.conditions,comparison.b.conditions)
        if fresh!=comparison:
            raise ValueError("对比结果与生成时快照不一致。")
        if [r["experiment_id"] for r in runs]!=[a.prepared.experiment.experiment_id,b.prepared.experiment.experiment_id]:
            raise ValueError("报告实验与A/B对比不匹配。")
        comparison_data = comparison.model_dump(mode="json")
        from reports.charts import comparison_figures
        comparison_charts = comparison_figures(comparison,a,b)
    return freeze({"schema_version":REPORT_VERSION,"report_id":"report-"+uuid4().hex,
        "created_at":datetime.now(timezone.utc).isoformat(),"project":project.strip(),"notes":notes,
        "normalization":NORMALIZATION,"runs":runs,"comparison":comparison_data,"comparison_charts":comparison_charts,
        "ai":ai or {"status":"not_participated","message":"AI 未参与","turns":[]},
        "limitations":["程序结果只描述本次观测，不保证未来表现。","规则现象不构成根因诊断，A/B差异不能直接归因PID参数。","未知设备型号与实验条件保持未知。"],
        "next_verification":["核对原始摘要、字段单位与选定区间。","结合异常证据检查采样、测量链路及限幅条件。","在记录明确的相同条件下重复实验，再验证差异。"]},secrets=secrets)


def metric_rows(data):
    """页面、HTML、CSV共用投影；不重新计算指标/差值，不把None变成0。"""
    rows = []
    def append_metrics(run, computation, scope, interval, config, result_id):
        if computation is None: return
        for key,metric in computation["metrics"].items():
            rows.append({"report_id":data["report_id"],"result_id":result_id,"experiment_id":run["experiment_id"],"experiment_name":run["name"],
                "scope":scope,"metric_key":key,"name":metric["name"],"value":metric["value"],"unit":metric["unit"],"status":metric["status"],"reason":metric["reason"],
                "start_s":interval.get("start_s"),"end_s":interval.get("end_s"),"configuration_id":fingerprint(config),"config_json":_json(config),"definition":DEFINITIONS.get(key,"")})
    for run in data["runs"]:
        p = run["performance"]
        if p is None: continue
        append_metrics(run,p["tracking"],"一般跟踪",p["interval"],p["config"],run["result_id"])
        if p["response"]:
            interval = {"start_s":p["step"]["t0"],"end_s":p["response"]["details"].get("observation_end_s")}
            append_metrics(run,p["response"],"确认单阶跃",interval,p["config"],run["result_id"])
    c = data.get("comparison")
    if c:
        for label,index in (("a",0),("b",1)):
            side = c[label]
            interval = {"start_s":side["start_s"],"end_s":side["end_s"]}
            for scope in ("tracking","response"):
                used_interval = interval if scope=="tracking" else {"start_s":side['step']['t0'],"end_s":side[scope]['details'].get('observation_end_s')}
                append_metrics(data["runs"][index],side[scope],"A/B · "+label.upper()+" · "+scope,used_interval,side["config"],c["comparison_id"])
        for diff in c["differences"]:
            for key,unit in (("delta",diff["a"]["unit"]),("percent","%")):
                rows.append({"report_id":data["report_id"],"result_id":c["comparison_id"],"experiment_id":"A/B","experiment_name":"A/B", "scope":"B−A" if key=="delta" else "变化百分比",
                    "metric_key":key+"."+diff["key"],"name":diff["name"],"value":diff[key],"unit":unit,"status":"success" if diff[key] is not None else "not_applicable","reason":diff["reason"],
                    "start_s":None,"end_s":None,"configuration_id":fingerprint(c),"config_json":_json({"common":c["config"],**{side:{k:c[side][k] for k in ('config','step','start_s','end_s','conditions')} for side in ('a','b')}}),"definition":"B−A；百分比=100×(B−A)/abs(A)，零分母或不可比时不计算。"})
    return rows
