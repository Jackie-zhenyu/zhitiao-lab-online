"""云请求共享的最小摘要及授权摘要；不读取任意文件或序列化原始数据。"""

import hashlib
import json
import re

import pandas as pd


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False).encode()).hexdigest()


def unit_label(value) -> str:
    text = str(value)
    return text if len(text) <= 24 and re.fullmatch(r"[\w°/%·.\-^]+", text) else "用户定义单位（仅本地）"


def source_label(view) -> str:
    if view.source_label in ("解析函数合成数据", "闭环仿真数据，非实测"):
        return view.source_label
    return {"measured":"实测数据（用户声明）", "simulated":"合成或仿真数据（用户声明）", "unknown":"来源未确认"}[view.prepared.experiment.source.kind.value]


def view_fingerprint(view) -> str:
    return digest({
        "raw":hashlib.sha256(view.prepared.parsed.raw_bytes).hexdigest(),
        "frame":hashlib.sha256(pd.util.hash_pandas_object(view.prepared.frame, index=True).values.tobytes()).hexdigest(),
        "mapping":view.prepared.mapping.model_dump(), "quality":view.quality.config.model_dump(),
        "metrics":view.metric_config.model_dump(), "events":view.event_config.model_dump(),
        "rows":[view.start_row,view.end_row], "performance":view.performance.model_dump(mode="json") if view.performance else None,
        "saved":view.saved.snapshot_id if view.saved else None, "conditions":view.conditions.model_dump(),
        "source":view.prepared.experiment.source.kind.value,
    })


def model_context(context) -> dict:
    result = {"aliases":dict(context.aliases), "experiments":[], "comparison_max_duration_s":context.allowed_comparison_duration_s}
    for identifier, view in context.experiments.items():
        step = view.performance.step if view.performance else None
        selected = None
        if view.start_row is not None and view.end_row is not None:
            selected = {"start_s":float(view.prepared.frame.iloc[view.start_row-1].time), "end_s":float(view.prepared.frame.iloc[view.end_row-1].time)}
        result["experiments"].append({
            "experiment_id":identifier, "source_label":source_label(view), "source_id":view.prepared.parsed.sha256,
            "fields":list(view.prepared.mapping.fields), "unit":unit_label(view.prepared.mapping.unit),
            "selected_range":selected, "row_count":view.quality.row_count,
            "has_confirmed_step":step is not None, "step":{
                "t0":step.t0,"r0":step.r0,"r1":step.r1,"y0":step.y0,
            } if step else None,
            "metric_config":view.metric_config.model_dump(),
            "event_config":view.event_config.model_dump(),
            "condition_known":{k: bool(v) for k,v in view.conditions.model_dump().items()},
        })
    return result


def safe_history(conversation, context_key, max_turns) -> list[dict]:
    if max_turns <= 0:
        return []
    history = []
    for turn in [t for t in conversation.turns if t.context_key == context_key and t.answer is not None][-max_turns:]:
        history.extend([
            {"role":"user", "content":turn.question},
            {"role":"assistant", "content":turn.answer.model_dump_json()},
        ])
    return history


def disclosure(question, context, conversation, settings) -> dict:
    return {
        "destination":settings.public_config(),
        "question":question,
        "context":model_context(context),
        "history":safe_history(conversation, context.context_key, settings.max_history_turns),
        "tool_data_scope":"仅所选实验的指标、质量计数/规则、事件阈值与观测量、A/B计算差值和实际区间；不发送原始CSV、文件名、备注、条件原文或路径。",
        "max_events_per_result":settings.max_events,
        "max_tool_result_chars":settings.max_tool_result_chars,
        "max_request_context_chars":settings.max_context_chars,
    }


def consent_fingerprint(question, context, conversation, settings) -> str:
    return digest({"session":context.session_id,"context":context.context_key,"disclosure":disclosure(question,context,conversation,settings),"policy":"agent-v5.0"})
