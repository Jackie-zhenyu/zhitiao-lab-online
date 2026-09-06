"""把应用拥有的工作实验/确认快照转换为有限AI上下文；不信任模型身份。"""

from copy import deepcopy

from agent.models import AgentContext, ToolExperiment
from agent.privacy import digest, view_fingerprint
from core.event_models import EventConfig
from core.metric_models import MetricConfig


def available_views(state):
    views = {}
    if state.prepared is not None and state.quality is not None and state.evidence is not None:
        performance = state.performance
        views["current"] = ToolExperiment(
            state.prepared,state.quality,performance.config if performance else MetricConfig(
                min_interval_ratio=state.quality.config.min_interval_ratio,
                max_interval_ratio=state.quality.config.max_interval_ratio,
                reference_interval_s=state.quality.interval_stats.get("median_s")),
            state.evidence.config,
            performance.start_row if performance else None,performance.end_row if performance else None,
            performance=performance,source_label=state.source_metadata.get("source_label","用户声明来源"),
        )
    for key,saved in state.saved_experiments.items():
        views[key] = ToolExperiment(
            saved.prepared,saved.quality,saved.performance.config,
            state.saved_event_configs.get(key,EventConfig()),
            saved.performance.start_row,saved.performance.end_row,
            performance=saved.performance,saved=saved,source_label=saved.performance.source_label,conditions=saved.conditions,
        )
    return views


def build_context(views, a_key, b_key, previous=None, *, allowed_duration=None, max_events=20):
    chosen = {"A":views[a_key]}
    if b_key is not None:
        chosen["B"] = views[b_key]
    signatures = {alias:view_fingerprint(view) for alias,view in chosen.items()}
    identifiers = {alias:view.prepared.experiment.experiment_id for alias,view in chosen.items()}
    if "B" in chosen and identifiers["A"]==identifiers["B"] and signatures["A"]!=signatures["B"]:
        raise ValueError("两份选择是同一实验ID的不同配置版本，请选择同一快照自比或另一个实验，避免指代歧义。")
    key = digest({"selections":{k:[identifiers[k],signatures[k]] for k in chosen},"duration":allowed_duration,"events":max_events})
    if previous is not None and previous.context_key==key:
        return previous
    context = AgentContext(context_key=key,allowed_comparison_duration_s=allowed_duration,max_events=max_events)
    if previous is not None:
        context.session_id = previous.session_id
    context.aliases = identifiers
    context.experiments = {identifiers[alias]:deepcopy(view) for alias,view in chosen.items()}
    return context
