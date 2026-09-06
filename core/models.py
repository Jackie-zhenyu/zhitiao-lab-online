"""可追溯的输入输出契约；本模块不读取文件，也不计算实验指标。"""

from enum import Enum
from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator


Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
RawColumnName = Annotated[str, StringConstraints(min_length=1, pattern=r"\S")]
FiniteNumber = Annotated[float, Field(strict=True, allow_inf_nan=False)]
Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-fA-F]{64}$")]


class ContractModel(BaseModel):
    """拒绝未声明字段及非有限值，避免拼写错误被静默忽略。"""

    model_config = ConfigDict(
        extra="forbid", allow_inf_nan=False, revalidate_instances="always"
    )


class SourceKind(str, Enum):
    MEASURED = "measured"
    SIMULATED = "simulated"
    UNKNOWN = "unknown"


class ResultStatus(str, Enum):
    SUCCESS = "success"
    NOT_APPLICABLE = "not_applicable"
    NOT_REACHED = "not_reached"
    INSUFFICIENT_DATA = "insufficient_data"
    INVALID_DATA = "invalid_data"
    NOT_COMPUTABLE = "not_computable"
    NOT_IMPLEMENTED = "not_implemented"
    ERROR = "error"


class AnalysisInterval(ContractModel):
    """相对于实验时间基准的闭区间，单位为秒；允许单点区间。"""

    start_s: FiniteNumber
    end_s: FiniteNumber

    @model_validator(mode="after")
    def ordered(self) -> Self:
        if self.start_s > self.end_s:
            raise ValueError("分析区间起点不得晚于终点")
        return self


class DataSource(ContractModel):
    """来源元数据；声明来源类型不等于验证过来源真实性。"""

    source_id: Text
    experiment_id: Text
    kind: SourceKind
    filename: Text | None = None
    sha256: Sha256 | None = None


class Experiment(ContractModel):
    experiment_id: Text
    name: Text
    source: DataSource
    # 原始列名 -> 文件声明的单位；未知单位为 None，不能猜测。
    raw_fields: dict[RawColumnName, Text | None] = Field(default_factory=dict)

    @model_validator(mode="after")
    def source_matches_experiment(self) -> Self:
        if self.source.experiment_id != self.experiment_id:
            raise ValueError("来源与实验的 experiment_id 必须一致")
        return self


class AnalysisConfig(ContractModel):
    interval: AnalysisInterval
    # 语义字段名 -> 原始列名；确认单位按同一语义字段名记录。
    field_mapping: dict[Text, RawColumnName] = Field(default_factory=dict)
    confirmed_units: dict[Text, Text] = Field(default_factory=dict)
    # 按执行顺序记录；空列表表示尚未声明任何数据处理操作。
    processing_rules: list[Text] = Field(default_factory=list)

    @model_validator(mode="after")
    def units_have_fields(self) -> Self:
        if not self.confirmed_units.keys() <= self.field_mapping.keys():
            raise ValueError("确认单位必须对应已映射的语义字段")
        return self


def _validate_numeric_outcome(
    status: ResultStatus,
    value: float | None,
    unit: str | None,
    reason: str | None,
) -> None:
    if status == ResultStatus.SUCCESS:
        if value is None or unit is None:
            raise ValueError("成功结果必须包含有限数值和明确单位")
    else:
        if value is not None:
            raise ValueError("无法计算或未成功的结果数值必须为 None")
        if reason is None:
            raise ValueError("非成功结果必须说明原因")


class MetricResult(ContractModel):
    """指标的数值与状态；追溯上下文由 AnalysisResult 统一持有。"""

    name: Text
    status: ResultStatus
    value: FiniteNumber | None = None
    unit: Text | None = None
    reason: Text | None = None

    @model_validator(mode="after")
    def validate_outcome(self) -> Self:
        _validate_numeric_outcome(self.status, self.value, self.unit, self.reason)
        return self


class EventResult(ContractModel):
    """检测事件及其数值证据；没有检测到事件时应使用空事件列表。"""

    name: Text
    status: ResultStatus
    interval: AnalysisInterval | None = None
    value: FiniteNumber | None = None
    unit: Text | None = None
    reason: Text | None = None

    @model_validator(mode="after")
    def validate_outcome(self) -> Self:
        _validate_numeric_outcome(self.status, self.value, self.unit, self.reason)
        if self.status == ResultStatus.SUCCESS and self.interval is None:
            raise ValueError("成功事件必须包含发生区间")
        return self


class AnalysisResult(ContractModel):
    """一份分析的完整追溯上下文；序列化结果时应保存整个容器。"""

    experiment_id: Text
    source: DataSource
    config: AnalysisConfig
    algorithm_version: Text
    status: ResultStatus
    reason: Text | None = None
    metrics: list[MetricResult] = Field(default_factory=list)
    events: list[EventResult] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_context(self) -> Self:
        if self.source.experiment_id != self.experiment_id:
            raise ValueError("来源与分析结果的 experiment_id 必须一致")
        if self.status != ResultStatus.SUCCESS and self.reason is None:
            raise ValueError("非成功分析必须说明原因")
        bounds = self.config.interval
        for event in self.events:
            if event.interval is not None and (
                event.interval.start_s < bounds.start_s
                or event.interval.end_s > bounds.end_s
            ):
                raise ValueError("事件区间必须位于分析区间内")
        return self
