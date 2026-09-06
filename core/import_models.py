"""第二阶段的导入配置和会话内数据容器；不依赖 UI 或模型。"""

from dataclasses import dataclass, field
from typing import Literal

import pandas as pd
from pydantic import Field, model_validator

from core.models import ContractModel, Experiment, RawColumnName, Text

REQUIRED_FIELDS = ("time", "target", "actual")
OPTIONAL_FIELDS = ("control", "p_term", "i_term", "d_term")
IMPORT_VERSION = "csv-quality-v2.0"


class ParserOptions(ContractModel):
    encoding: Literal["auto", "utf-8", "utf-8-sig", "gb18030", "utf-16", "cp1252"] = "auto"
    delimiter: Literal["auto", ",", ";", "\t"] = "auto"
    max_bytes: int = Field(default=20 * 1024 * 1024, ge=1, strict=True)
    max_rows: int = Field(default=200000, ge=1, strict=True)


class MappingConfig(ContractModel):
    fields: dict[Text, RawColumnName]
    time_unit: Literal["s", "ms"]
    quantity: Text
    unit: Text
    optional_units: dict[Text, Text] = Field(default_factory=dict)
    confirmed: Literal[True]
    units_consistent: Literal[True]

    @model_validator(mode="after")
    def valid_mapping(self):
        if not set(REQUIRED_FIELDS) <= self.fields.keys():
            raise ValueError("必须映射 time、target、actual")
        if not self.fields.keys() <= set(REQUIRED_FIELDS + OPTIONAL_FIELDS):
            raise ValueError("存在不支持的语义字段")
        if len(set(self.fields.values())) != len(self.fields):
            raise ValueError("不同语义字段必须映射不同原始列")
        mapped_optional = self.fields.keys() & set(OPTIONAL_FIELDS)
        if self.optional_units.keys() != mapped_optional:
            raise ValueError("每个已映射可选字段必须明确填写单位")
        return self


class QualityConfig(ContractModel):
    min_interval_ratio: float = Field(default=0.5, gt=0, allow_inf_nan=False)
    max_interval_ratio: float = Field(default=2.0, gt=0, allow_inf_nan=False)

    @model_validator(mode="after")
    def valid_ratios(self):
        if not self.min_interval_ratio < 1 < self.max_interval_ratio:
            raise ValueError("间隔比例必须满足 0 < 下限 < 1 < 上限")
        return self


@dataclass
class ParsedCSV:
    raw_bytes: bytes
    sha256: str
    filename: str
    frame: pd.DataFrame  # 原始字符串，保持输入顺序
    source_lines: list[int]  # 每条数据记录起始物理行，一一对应 frame
    encoding: str
    delimiter: str
    operations: list[str] = field(default_factory=list)


@dataclass
class PreparedExperiment:
    parsed: ParsedCSV
    experiment: Experiment
    mapping: MappingConfig
    frame: pd.DataFrame  # 规范字段，time 单位 s；原始数据不改动
    operations: list[str]


class QualityIssue(ContractModel):
    code: Text
    row: int  # 数据行从 1 开始，不含表头
    csv_line: int
    field: Text
    message: Text
    blocking: bool = True


class ValidInterval(ContractModel):
    start_row: int
    end_row: int
    start_s: float
    end_s: float


@dataclass
class QualityReport:
    row_count: int
    duration_s: float | None
    interval_stats: dict[str, float | int | None]
    issues: list[QualityIssue]
    valid_intervals: list[ValidInterval]
    config: QualityConfig
    experiment_id: str

    @property
    def blocking(self) -> bool:
        return any(issue.blocking for issue in self.issues)
