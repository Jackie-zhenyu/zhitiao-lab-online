"""只读、会话内的四个确定性工具；不执行模型代码，不接受路径或身份。"""

from collections import Counter
from copy import deepcopy
from dataclasses import replace
import hashlib
import json
import math
from uuid import uuid4

from pydantic import Field, ValidationError

from agent.models import AgentContext, EvidenceRecord, EvidenceRef, TimeRange
from agent.privacy import source_label, unit_label, view_fingerprint
from core.comparison import compare_experiments
from core.comparison_models import ComparisonConfig
from core.events import detect_events as compute_events
from core.import_models import ValidInterval
from core.metric_models import NORMALIZATION
from core.models import ContractModel, FiniteNumber
from core.performance import analyze_performance
from core.quality import select_interval


class ToolError(Exception):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


class ProfileArgs(ContractModel):
    experiment_id: str = Field(min_length=1, max_length=100)


class EmptyOptions(ContractModel):
    pass


class AnalysisOptions(ContractModel):
    include_response: bool = Field(default=True, strict=True)


class AnalysisArgs(ProfileArgs):
    time_range: TimeRange | None = None
    options: AnalysisOptions = Field(default_factory=AnalysisOptions)


class EventsArgs(ProfileArgs):
    time_range: TimeRange | None = None
    options: EmptyOptions = Field(default_factory=EmptyOptions)


class CompareOptions(ContractModel):
    common_duration_s: FiniteNumber | None = Field(default=None, gt=0)


class CompareArgs(ContractModel):
    experiment_a: str = Field(min_length=1, max_length=100)
    experiment_b: str = Field(min_length=1, max_length=100)
    options: CompareOptions = Field(default_factory=CompareOptions)


_ARGUMENTS = {"profile_data":ProfileArgs,"analyze_run":AnalysisArgs,"detect_events":EventsArgs,"compare_runs":CompareArgs}
_DESCRIPTIONS = {
    "profile_data":"查看已授权实验的质量概要、行数、可用片段；不返回原始记录。",
    "analyze_run":"重算已确认有效片段中的跟踪指标。time_range为绝对秒；不跨断点，不自动确认阶跃/改变阈值。区间变更必须调用。",
    "detect_events":"以time_range绝对秒限定边界重新检测现象，沿用用户确认规则；不诊断根因。无区间时逐有效片段检查。",
    "compare_runs":"比较已确认阶跃快照，按t0对齐并原始时间戳重算；共同窗口必须在用户允许时长以内。",
}


def _fact(name, value, unit, *, status="success", reason="由程序计算。", interval=None):
    if value is not None and (isinstance(value, bool) or not math.isfinite(float(value))):
        raise ToolError("invalid_data", "数据产生非有限结果，未登记证据。")
    return {"name":name,"value":value,"unit":unit,"status":status,"reason":reason,"interval":interval}


def _metric_fact(metric, interval=None):
    return _fact(metric.name,metric.value,metric.unit,status=metric.status.value,reason=metric.reason,
                 interval=interval)


def _public_fact(value):
    return {k:(unit_label(v) if k=="unit" and v is not None else v) for k,v in value.items() if k in {"name","value","unit","status","interval"}}


def _event_summary(event):
    data = event.model_dump(mode="json")
    # 字段名/原始问题文字可能含诱导文本，只发送语义字段和受控质量代码。
    data["observations"] = {k:v for k,v in data["observations"].items() if k not in {"original_message", "time_location_reason"}}
    data.pop("phenomenon", None)
    data.pop("undetermined_causes", None)
    data.pop("limitations", None)
    for k,v in list(data["thresholds"].items()):
        if "unit" in k:
            data["thresholds"][k] = unit_label(v)
    return data


class ToolRegistry:
    def __init__(self, context: AgentContext):
        self.context = context

    def schemas(self):
        return [{"type":"function", "function":{"name":name,"description":_DESCRIPTIONS[name],"parameters":cls.model_json_schema()}} for name,cls in _ARGUMENTS.items()]

    def validate_call(self, name, arguments):
        if name not in _ARGUMENTS:
            raise ToolError("unknown_tool", "工具未注册，已拒绝执行。")
        if not isinstance(arguments,str) or len(arguments)>12000:
            raise ToolError("invalid_arguments", "工具参数必须为有界JSON对象。")
        try:
            raw = json.loads(arguments)
            if not isinstance(raw,dict):
                raise ValueError()
            return _ARGUMENTS[name].model_validate(raw).model_dump(mode="json")
        except (ValueError, TypeError, ValidationError, RecursionError):
            raise ToolError("invalid_arguments", "工具参数不符合白名单结构，请核对实验、区间及受支持选项。") from None

    def _experiment(self, identifier):
        view = self.context.experiments.get(identifier)
        if view is None or view.prepared.experiment.experiment_id != identifier:
            raise ToolError("not_owned", "实验不在当前会话本轮授权的选择中。")
        if view.quality.experiment_id != identifier or hashlib.sha256(view.prepared.parsed.raw_bytes).hexdigest() != view.prepared.parsed.sha256:
            raise ToolError("stale_data", "实验来源或质量结果已失效，请重新确认。")
        return view

    def _rows(self, view, requested):
        if view.start_row is None or view.end_row is None:
            raise ToolError("select_interval", "请先在本地选择一个连续有效区间。")
        frame = select_interval(view.prepared,view.quality,view.start_row,view.end_row)
        if requested is not None:
            bounds = TimeRange.model_validate(requested)
            if bounds.start_s < float(frame.time.iloc[0]) or bounds.end_s > float(frame.time.iloc[-1]):
                raise ToolError("outside_interval", "请求区间超出已确认连续片段；未静默裁剪或跨断点。")
            frame = frame.loc[(frame.time>=bounds.start_s)&(frame.time<=bounds.end_s)]
        if len(frame)<2:
            raise ToolError("insufficient_data", "区间内不足两个原始有效采样点。")
        return int(frame.index[0])+1,int(frame.index[-1])+1

    def invoke(self, name, arguments, *, request_id):
        args = self.validate_call(name,arguments)
        ids = [args["experiment_a"],args["experiment_b"]] if name=="compare_runs" else [args["experiment_id"]]
        views = [self._experiment(identifier) for identifier in ids]
        facts, events, details, limitations = {}, {}, {}, []
        try:
            if name=="profile_data":
                view = views[0]
                facts = {
                    "row_count":_fact("原始数据行数",view.quality.row_count,"行"),
                    "issue_count":_fact("质量问题条数",len(view.quality.issues),"条"),
                    "duration_s":_fact("记录时长",view.quality.duration_s,"s",status="success" if view.quality.duration_s is not None else "invalid_data",reason="首尾时间跨度；时间不可靠时不可用。"),
                }
                details = {"quality_codes":dict(Counter(i.code for i in view.quality.issues)), "valid_intervals":[s.model_dump() for s in view.quality.valid_intervals[:20]],"valid_interval_count":len(view.quality.valid_intervals),"quality_config":view.quality.config.model_dump()}
                limitations.append("片段列表最多返回二十条；零质量问题不证明设备正常。")
            elif name=="analyze_run":
                view = views[0]
                start,end = self._rows(view,args["time_range"])
                step,step_start,step_end = None,None,None
                previous = view.performance
                if args["options"]["include_response"]:
                    if previous and previous.step and start<=previous.step_start_row<previous.step_end_row<=end:
                        step,step_start,step_end = previous.step,previous.step_start_row,previous.step_end_row
                    else:
                        limitations.append("当前区间未完整包含已确认的单阶跃及其基线；仅重算跟踪指标，响应指标未计算。")
                result = analyze_performance(view.prepared,view.quality,start,end,view.metric_config,source_label=source_label(view),step=step,step_start_row=step_start,step_end_row=step_end)
                facts = {key:_metric_fact(m,result.interval.model_dump()) for key,m in result.tracking.metrics.items()}
                if result.response:
                    response_interval = {"start_s":step.t0,"end_s":float(view.prepared.frame.iloc[step_end-1].time)}
                    facts.update({key:_metric_fact(m,response_interval) for key,m in result.response.metrics.items()})
                details = {"actual_interval":result.interval.model_dump(),"start_row":start,"end_row":end,"config":result.config.model_dump(),"normalization":NORMALIZATION,"algorithm_version":result.algorithm_version}
            elif name=="detect_events":
                view = views[0]
                quality = view.quality
                if args["time_range"] is not None:
                    start,end = self._rows(view,args["time_range"])
                    frame = view.prepared.frame
                    segment = ValidInterval(start_row=start,end_row=end,start_s=float(frame.iloc[start-1].time),end_s=float(frame.iloc[end-1].time))
                    quality = replace(quality,issues=[i for i in quality.issues if start<=i.row<=end],valid_intervals=[segment])
                report = compute_events(view.prepared,quality,view.event_config)
                selected = report.events[:self.context.max_events]
                events = {e.event_id:_event_summary(e) for e in selected}
                facts = {"event_count":_fact("规则现象事件数",len(report.events),"条"),"returned_event_count":_fact("本次返回事件数",len(selected),"条")}
                details = {"config":report.config.model_dump(),"checks":[{"rule":c.rule,"status":c.status} for c in report.checks],"algorithm_version":report.algorithm_version}
                if args["time_range"] is not None:
                    details.update(actual_interval=segment.model_dump(),start_row=start,end_row=end)
                limitations.append("规则只描述现象；未发现异常不证明系统正常。事件超过发送上限时明确截断，不跨无效行。")
            else:
                a,b = views
                allowed = self.context.allowed_comparison_duration_s
                duration = args["options"]["common_duration_s"]
                duration = allowed if duration is None else duration
                if allowed is None or duration is None or not math.isfinite(allowed) or duration>allowed:
                    raise ToolError("confirm_window", "请确认共同观察时长；工具不能扩大授权窗口。")
                if a.saved is None or b.saved is None:
                    raise ToolError("confirm_step", "比较需要两份已保存并确认单次阶跃的实验快照。")
                result = compare_experiments(a.saved,b.saved,ComparisonConfig(common_duration_s=duration,confirmed=True),a.conditions,b.conditions)
                for item in result.differences:
                    for label, side, metric in (("A", result.a, item.a), ("B", result.b, item.b)):
                        # 手动t0可落在采样点之间；响应和原生跟踪区间必须分别追溯。
                        interval = ({"start_s": side.step.t0, "end_s": side.response.details.get("observation_end_s")}
                                    if item.key in side.response.metrics
                                    else {"start_s": side.start_s, "end_s": side.end_s})
                        facts[label+"."+item.key] = _metric_fact(metric, interval)
                    facts["delta."+item.key] = _fact(item.name+"差值 B−A",item.delta,item.a.unit,status="success" if item.delta is not None else "not_applicable",reason=item.reason)
                    facts["percent."+item.key] = _fact(item.name+"变化百分比",item.percent,"%",status="success" if item.percent is not None else "not_applicable",reason=item.reason)
                details = {"common_duration_s":duration,"checks":[{"name":c.name,"status":c.status} for c in result.checks],"actual_intervals":{k:{"start_s":s.start_s,"end_s":s.end_s,"relative_start_s":s.relative_start_s,"relative_end_s":s.relative_end_s,"t0":s.step.t0} for k,s in (("A",result.a),("B",result.b))},"normalization":NORMALIZATION,"algorithm_version":result.algorithm_version}
                limitations.extend(result.warnings)
        except ToolError:
            raise
        except (ValueError, TypeError, KeyError, IndexError, OverflowError):
            raise ToolError("invalid_data", "现有分析接口拒绝了该数据或配置；请检查连续区间、基线和可比性，未生成替代数值。") from None
        result_id = "result-"+uuid4().hex
        payload = {"result_id":result_id,"tool":name,"experiment_ids":ids,"requested_time_range":args.get("time_range"),"facts":{k:_public_fact(v) for k,v in facts.items()},"events":list(events.values()),"details":details,"limitations":limitations,"sources":[source_label(v) for v in views],"source_ids":[v.prepared.parsed.sha256 for v in views],"adapter_version":"agent-tools-v5.0"}
        # 序列化检查阻止NaN/无穷或不可追溯对象进入模型消息。
        json.dumps(payload,allow_nan=False)
        self.context.evidence[result_id] = EvidenceRecord(result_id,self.context.session_id,self.context.context_key,request_id,name,ids,deepcopy(payload),deepcopy(facts),deepcopy(events),{i:view_fingerprint(v) for i,v in zip(ids,views)})
        return payload

    def resolve(self, ref: EvidenceRef, *, request_id=None):
        ref = EvidenceRef.model_validate(ref)
        record = self.context.evidence.get(ref.result_id)
        if record is None or record.session_id!=self.context.session_id or record.context_key!=self.context.context_key or (request_id is not None and record.request_id!=request_id):
            raise ToolError("invalid_reference", "证据不存在、不是本轮结果、属于其他会话或已失效。")
        for identifier in record.experiment_ids:
            view = self._experiment(identifier)
            if record.experiment_fingerprints.get(identifier)!=view_fingerprint(view):
                raise ToolError("stale_reference", "证据配置或数据已变化，必须重新调用分析工具。")
        value = record.facts.get(ref.metric_key) if ref.metric_key is not None else record.events.get(ref.event_id)
        if value is None:
            raise ToolError("invalid_reference", "该结果不包含所引用的指标或事件。")
        return deepcopy(value)
