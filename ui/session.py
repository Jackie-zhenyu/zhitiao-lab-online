"""每个 Streamlit 会话独立持有的状态，无共享实例或全局数据缓存。"""

from dataclasses import dataclass, field
import hashlib
import json

from core.import_models import ParsedCSV, ParserOptions, PreparedExperiment, QualityReport
from core.parser import parse_csv
from core.metric_models import PerformanceResult, StepSelection
from core.comparison_models import ComparisonResult, SavedExperiment
from core.event_models import EvidenceReport, EventConfig


def fingerprint(value: dict) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False).encode("utf-8")
    ).hexdigest()


def preserve_import_widgets(session_state) -> None:
    """每次渲染前恢复草稿，兼顾离页保留与返回时浏览器控件恢复。

    不保存上传控件或瞬时按钮的状态；上传原始字节属于 LabSession。
    """
    names = {"input_mode", "csv_encoding", "csv_delimiter", "max_mib", "max_rows", "source_kind", "example_choice", "interval_lower", "interval_upper", "display_points", "report_project", "report_notes", "report_scope"}
    prefixes = ("map_", "unit_", "time_unit_", "quantity_", "physical_unit_", "units_confirmed_", "issues_page_", "valid_interval_", "start_", "end_", "metric_", "step_", "event_cfg_", "evidence_page_", "snapshot_name_", "ab_", "ai_select_", "ai_duration_", "ai_condition_")
    for key in list(session_state):
        if key in names or key.startswith((*prefixes, "report_run_name_")):
            session_state[key] = session_state[key]


@dataclass
class LabSession:
    raw_bytes: bytes | None = None
    filename: str = ""
    source_metadata: dict = field(default_factory=dict)
    source_key: str = ""
    revision: int = 0
    parsed: ParsedCSV | None = None
    parse_key: str = ""
    confirmed_key: str = ""
    prepared: PreparedExperiment | None = None
    quality: QualityReport | None = None
    traceability: dict | None = None
    performance: PerformanceResult | None = None
    step_key: str = ""
    step_selection: StepSelection | None = None
    metric_interval: tuple[int, int] | None = None
    evidence: EvidenceReport | None = None
    evidence_key: str = ""
    focused_event_id: str | None = None
    saved_experiments: dict[str, SavedExperiment] = field(default_factory=dict)
    saved_event_configs: dict[str, EventConfig] = field(default_factory=dict)
    comparison: ComparisonResult | None = None
    comparison_key: str = ""

    def invalidate_step(self) -> None:
        self.performance = None
        self.step_key = ""
        self.step_selection = None

    def invalidate(self) -> None:
        self.invalidate_step()
        self.evidence = None
        self.evidence_key = ""
        self.focused_event_id = None
        self.confirmed_key = ""
        self.prepared = None
        self.quality = None
        self.traceability = None

    def clear(self) -> None:
        self.set_source(None, "", {})

    def set_source(self, raw: bytes | None, filename: str, metadata: dict) -> None:
        key = fingerprint({
            "sha256": hashlib.sha256(raw).hexdigest() if raw is not None else None,
            "filename": filename,
            "metadata": metadata,
        })
        if key != self.source_key:
            self.raw_bytes, self.filename = raw, filename
            self.source_metadata = dict(metadata)
            self.source_key = key
            self.revision += 1
            self.parsed = None
            self.parse_key = ""
            self.invalidate()

    def ensure_parsed(self, options: ParserOptions) -> ParsedCSV:
        key = fingerprint({"source": self.source_key, "options": options.model_dump()})
        if key != self.parse_key or self.parsed is None:
            self.invalidate()
            self.parsed = None
            self.parse_key = ""
            self.revision += 1
            if self.raw_bytes is None:
                raise ValueError("尚未加载文件")
            self.parsed = parse_csv(self.raw_bytes, self.filename, options)
            self.parse_key = key
        return self.parsed

    def invalidate_if_changed(self, draft_key: str) -> None:
        if self.confirmed_key and self.confirmed_key != draft_key:
            self.invalidate()
