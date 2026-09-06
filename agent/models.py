"""第五阶段边界：模型只能选择工具、证据引用和有限的解释/验证类别。"""

from dataclasses import dataclass, field
from typing import Literal
from uuid import uuid4

from pydantic import Field, model_validator

from core.comparison_models import ExperimentConditions, SavedExperiment
from core.event_models import EventConfig
from core.import_models import PreparedExperiment, QualityReport
from core.metric_models import MetricConfig, PerformanceResult
from core.models import ContractModel, FiniteNumber, Text


class ToolCall(ContractModel):
    id: str = Field(min_length=1, max_length=200)
    name: str = Field(min_length=1, max_length=100)
    arguments: str = Field(max_length=12000)


class ModelReply(ContractModel):
    content: str | None = Field(default=None, max_length=40000)
    tool_calls: list[ToolCall] = Field(default_factory=list, max_length=32)


class TimeRange(ContractModel):
    start_s: FiniteNumber
    end_s: FiniteNumber

    @model_validator(mode="after")
    def ordered(self):
        if self.start_s >= self.end_s:
            raise ValueError("时间范围必须有正跨度，单位秒")
        return self


class EvidenceRef(ContractModel):
    result_id: str = Field(min_length=1, max_length=100)
    metric_key: str | None = Field(default=None, min_length=1, max_length=100)
    event_id: str | None = Field(default=None, min_length=1, max_length=100)

    @model_validator(mode="after")
    def one_locator(self):
        if (self.metric_key is None) == (self.event_id is None):
            raise ValueError("引用必须选择指标键或事件ID之一")
        return self


HypothesisCode = Literal["tracking_lag", "noise_or_dynamics", "actuator_limit", "recording_artifact", "changed_conditions", "insufficient_evidence"]
VerificationCode = Literal["inspect_raw_interval", "check_sampling", "confirm_output_limits", "repeat_same_conditions", "check_measurement_chain", "confirm_step_baseline", "extend_observation"]
LimitationCode = Literal["observation_only", "not_causal", "sampling_resolution", "conditions_unverified", "no_windup_diagnosis", "statistical_candidate_only"]
ClarificationCode = Literal["select_experiment", "confirm_fields", "confirm_step", "select_valid_interval", "confirm_comparison_window", "clarify_question"]


class CandidateExplanation(ContractModel):
    code: HypothesisCode
    evidence: list[EvidenceRef] = Field(min_length=1, max_length=8)


class AgentAnswer(ContractModel):
    # 模型不能提供value/单位/自算百分比或任意“测得事实”文本。
    measured_facts: list[EvidenceRef] = Field(default_factory=list, max_length=24)
    candidate_explanations: list[CandidateExplanation] = Field(default_factory=list, max_length=6)
    next_verification: list[VerificationCode] = Field(default_factory=list, max_length=7)
    limitations: list[LimitationCode] = Field(default_factory=list, max_length=6)
    evidence_references: list[EvidenceRef] = Field(default_factory=list, max_length=32)
    clarification: ClarificationCode | None = None


@dataclass
class ToolExperiment:
    prepared: PreparedExperiment
    quality: QualityReport
    metric_config: MetricConfig
    event_config: EventConfig
    start_row: int | None = None
    end_row: int | None = None
    performance: PerformanceResult | None = None
    saved: SavedExperiment | None = None
    source_label: str = "用户声明来源"
    conditions: ExperimentConditions = field(default_factory=ExperimentConditions)


@dataclass
class EvidenceRecord:
    result_id: str
    session_id: str
    context_key: str
    request_id: str
    tool: str
    experiment_ids: list[str]
    payload: dict
    facts: dict[str, dict] = field(default_factory=dict)
    events: dict[str, dict] = field(default_factory=dict)
    experiment_fingerprints: dict[str, str] = field(default_factory=dict)


@dataclass
class AgentContext:
    session_id: str = field(default_factory=lambda: uuid4().hex)
    context_key: str = ""
    experiments: dict[str, ToolExperiment] = field(default_factory=dict)
    aliases: dict[str, str] = field(default_factory=dict)
    evidence: dict[str, EvidenceRecord] = field(default_factory=dict)
    allowed_comparison_duration_s: float | None = None
    max_events: int = 20


class ExecutionRecord(ContractModel):
    kind: Literal["model", "tool", "validation"]
    status: Literal["started", "success", "rejected", "error", "timeout", "retry"]
    name: str
    detail: str
    result_id: str | None = None
    arguments: dict | None = None


@dataclass
class AgentTurn:
    request_id: str
    question: str
    context_key: str
    status: str
    message: str
    answer: AgentAnswer | None = None
    records: list[ExecutionRecord] = field(default_factory=list)
    result_ids: list[str] = field(default_factory=list)


@dataclass
class AgentConversation:
    turns: list[AgentTurn] = field(default_factory=list)
    # 历史只保留经校验的问答，不保留未校验的模型原文/异常/思维链。
    context_key: str = ""
