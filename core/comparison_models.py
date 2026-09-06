"""会话内确认快照与按阶跃对齐的描述性对比契约。"""

from dataclasses import dataclass
from typing import Literal
from pydantic import Field
from core.import_models import PreparedExperiment, QualityReport
from core.metric_models import MetricConfig, MetricComputation, PerformanceResult, StepSelection, NORMALIZATION
from core.models import ContractModel, DataSource, FiniteNumber, MetricResult, Text

COMPARISON_VERSION = "step-comparison-v4.0"


class ExperimentConditions(ContractModel):
    plant: Text | None = None
    load: Text | None = None
    sampling: Text | None = None
    environment: Text | None = None
    controller: Text | None = None


@dataclass
class SavedExperiment:
    snapshot_id: str
    name: str
    prepared: PreparedExperiment
    quality: QualityReport
    performance: PerformanceResult
    conditions: ExperimentConditions


class ComparisonConfig(ContractModel):
    common_duration_s: FiniteNumber = Field(gt=0)
    confirmed: Literal[True]


class CompatibilityCheck(ContractModel):
    name: Text
    status: Literal["match", "mismatch", "unknown"]
    reason: Text


class ComparisonSide(ContractModel):
    snapshot_id: Text
    name: Text
    source: DataSource
    source_label: Text
    quantity: Text
    unit: Text
    step: StepSelection
    config: MetricConfig
    conditions: ExperimentConditions
    step_start_row: int
    start_row: int
    end_row: int
    start_s: FiniteNumber
    end_s: FiniteNumber
    relative_start_s: FiniteNumber
    relative_end_s: FiniteNumber
    tracking: MetricComputation
    response: MetricComputation


class MetricDifference(ContractModel):
    key: Text
    name: Text
    a: MetricResult
    b: MetricResult
    delta: FiniteNumber | None = None
    percent: FiniteNumber | None = None
    reason: Text


class ComparisonResult(ContractModel):
    comparison_id: Text
    a: ComparisonSide
    b: ComparisonSide
    config: ComparisonConfig
    checks: list[CompatibilityCheck]
    differences: list[MetricDifference]
    warnings: list[Text]
    descriptive_only: bool
    can_overlay: bool
    normalization: Text = NORMALIZATION
    algorithm_version: Literal["step-comparison-v4.0"] = COMPARISON_VERSION
