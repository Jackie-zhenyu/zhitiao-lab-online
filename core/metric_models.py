"""第三阶段的指标配置、阶跃确认和可追溯结果；不依赖 UI。"""

from typing import Literal

from pydantic import Field, model_validator

from core.import_models import MappingConfig, QualityConfig
from core.models import (
    AnalysisInterval, ContractModel, DataSource, FiniteNumber, MetricResult,
    ResultStatus, Text,
)

METRICS_VERSION = "control-metrics-v3.0"
NORMALIZATION = "相对于目标值的归一化约定；不是按实测稳态值归一化的标准 stepinfo 结果。"


class MetricConfig(ContractModel):
    tail_fraction: FiniteNumber = Field(default=0.1, gt=0, le=1)
    rise_lower: FiniteNumber = Field(default=0.1, gt=0, lt=1)
    rise_upper: FiniteNumber = Field(default=0.9, gt=0, lt=1)
    relative_band: FiniteNumber = Field(default=0.02, ge=0, lt=1)
    absolute_tolerance: FiniteNumber = Field(default=0.0, ge=0)
    min_observation_s: FiniteNumber = Field(default=0.5, ge=0)
    amplitude_epsilon: FiniteNumber = Field(default=1e-9, gt=0)
    target_flat_tolerance: FiniteNumber = Field(default=1e-6, ge=0)
    baseline_window_s: FiniteNumber = Field(default=0.5, gt=0)
    baseline_min_duration_s: FiniteNumber = Field(default=0.1, ge=0)
    baseline_min_points: int = Field(default=3, ge=2, strict=True)
    baseline_range_tolerance: FiniteNumber = Field(default=0.02, ge=0)
    min_interval_ratio: FiniteNumber = Field(default=0.5, gt=0, lt=1)
    max_interval_ratio: FiniteNumber = Field(default=2.0, gt=1)
    # UI 使用整份质量报告的参考间隔；独立调用默认用当前帧正间隔中位数。
    reference_interval_s: FiniteNumber | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def ordered_thresholds(self):
        if self.rise_lower >= self.rise_upper:
            raise ValueError("上升时间阈值必须满足 0 < 下阈值 < 上阈值 < 1")
        return self


class StepCandidate(ContractModel):
    pre_start_row: int
    step_row: int
    end_row: int
    t0: FiniteNumber
    r0: FiniteNumber
    r1: FiniteNumber
    previous_sample_s: FiniteNumber


class BaselineEstimate(ContractModel):
    status: ResultStatus
    value: FiniteNumber | None = None
    reason: Text
    start_s: FiniteNumber | None = None
    end_s: FiniteNumber | None = None
    start_row: int | None = None
    end_row: int | None = None

    @model_validator(mode="after")
    def valid_outcome(self):
        if (self.status == ResultStatus.SUCCESS) != (self.value is not None):
            raise ValueError("基线成功须有有限值；失败值必须为空")
        return self


class StepSelection(ContractModel):
    t0: FiniteNumber
    r0: FiniteNumber
    r1: FiniteNumber
    y0: FiniteNumber
    y0_source: Literal["baseline", "manual"]
    baseline_start_s: FiniteNumber | None = None
    baseline_end_s: FiniteNumber | None = None
    baseline_start_row: int | None = Field(default=None, ge=1, strict=True)
    baseline_end_row: int | None = Field(default=None, ge=1, strict=True)
    baseline_method: Text | None = None
    confirmed: Literal[True]


class MetricComputation(ContractModel):
    metrics: dict[Text, MetricResult]
    markers: dict[Text, FiniteNumber] = Field(default_factory=dict)
    details: dict[Text, FiniteNumber | Text | None] = Field(default_factory=dict)


class PerformanceResult(ContractModel):
    experiment_id: Text
    source: DataSource
    source_label: Text
    interval: AnalysisInterval
    start_row: int
    end_row: int
    mapping: MappingConfig
    quality_rules: QualityConfig
    config: MetricConfig
    definitions: dict[Text, Text]
    algorithm_version: Literal["control-metrics-v3.0"] = METRICS_VERSION
    normalization: Literal["相对于目标值的归一化约定；不是按实测稳态值归一化的标准 stepinfo 结果。"] = NORMALIZATION
    step: StepSelection | None = None
    step_start_row: int | None = None
    step_end_row: int | None = None
    tracking: MetricComputation
    response: MetricComputation | None = None

    @model_validator(mode="after")
    def matching_context(self):
        if self.source.experiment_id != self.experiment_id:
            raise ValueError("指标结果与来源实验 ID 不一致")
        if self.start_row >= self.end_row:
            raise ValueError("指标区间至少两个连续采样点")
        if (self.step is None) != (self.response is None):
            raise ValueError("阶跃确认与响应结果必须同时存在")
        return self
