"""第四阶段现象证据契约；不包含根因诊断或设备控制。"""

from typing import Literal
from pydantic import Field, model_validator
from core.models import ContractModel, DataSource, FiniteNumber, Text

EVENTS_VERSION = "evidence-rules-v4.0"
Scalar = FiniteNumber | Text | bool | None


class OutputLimits(ContractModel):
    lower: FiniteNumber
    upper: FiniteNumber
    confirmed: Literal[True]

    @model_validator(mode="after")
    def ordered(self):
        if self.lower >= self.upper:
            raise ValueError("输出下限必须小于上限")
        return self


class EventConfig(ContractModel):
    oscillation_window_s: FiniteNumber = Field(default=2.0, gt=0)
    oscillation_min_crossings: int = Field(default=4, ge=2, strict=True)
    oscillation_peak_to_peak: FiniteNumber = Field(default=0.05, gt=0)
    oscillation_deadband: FiniteNumber = Field(default=0.01, ge=0)
    near_target_band: FiniteNumber = Field(default=0.2, gt=0)
    target_flat_tolerance: FiniteNumber = Field(default=1e-6, ge=0)
    oscillation_min_time_fraction: FiniteNumber = Field(default=0.25, ge=0, le=1)
    control_near_fraction: FiniteNumber = Field(default=0.02, gt=0, lt=0.5)
    control_min_duration_s: FiniteNumber = Field(default=0.5, gt=0)
    output_limits: OutputLimits | None = None
    rate_limit: FiniteNumber | None = Field(default=None, gt=0)
    rate_limit_confirmed: bool = False
    statistical_rate_factor: FiniteNumber = Field(default=6.0, gt=0)
    statistical_min_jump: FiniteNumber = Field(default=0.05, gt=0)


class EvidenceEvent(ContractModel):
    event_id: Text
    experiment_id: Text
    category: Literal["oscillation", "control_limit", "measurement_change", "data_record"]
    start_s: FiniteNumber | None
    end_s: FiniteNumber | None
    start_row: int = Field(ge=1, strict=True)
    end_row: int = Field(ge=1, strict=True)
    rule: Text
    thresholds: dict[Text, Scalar]
    observations: dict[Text, Scalar]
    charts: list[Literal["response", "error", "control", "raw_data"]]
    phenomenon: Text
    undetermined_causes: Text
    limitations: list[Text]
    algorithm_version: Literal["evidence-rules-v4.0"] = EVENTS_VERSION

    @model_validator(mode="after")
    def consistent_interval(self):
        if self.start_row > self.end_row:
            raise ValueError("事件原始行范围倒置")
        if (self.start_s is None) != (self.end_s is None):
            raise ValueError("时间不可确定时起止时间同时为空")
        if self.start_s is not None and self.start_s > self.end_s:
            raise ValueError("事件时间范围倒置")
        return self


class RuleCheck(ContractModel):
    rule: Text
    status: Literal["checked", "not_applicable", "insufficient_data", "invalid_data"]
    reason: Text


class EvidenceReport(ContractModel):
    experiment_id: Text
    source: DataSource
    config: EventConfig
    checks: list[RuleCheck]
    events: list[EvidenceEvent]
    algorithm_version: Literal["evidence-rules-v4.0"] = EVENTS_VERSION
