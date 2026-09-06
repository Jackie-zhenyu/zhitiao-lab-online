"""有界、只读、重新计算核验的本地项目包；不执行包内代码或写入路径。"""
from dataclasses import asdict, dataclass, field
import hashlib
import io
import json
import math
import re
import stat
import struct
from typing import BinaryIO
import zipfile

from pydantic import BaseModel

from core.import_models import ParsedCSV, ParserOptions, PreparedExperiment, QualityReport
from core.metric_models import PerformanceResult
from core.event_models import EvidenceReport, EventConfig
from core.comparison_models import SavedExperiment, ComparisonResult
from reports.snapshot import ReportSnapshot
from reports.snapshot import REPORT_VERSION
from core.import_models import IMPORT_VERSION, MappingConfig, QualityConfig
from core.metric_models import METRICS_VERSION
from core.event_models import EVENTS_VERSION
from core.comparison_models import COMPARISON_VERSION, ExperimentConditions
from core.comparison import capture_experiment, compare_experiments
from core.models import SourceKind
from core.parser import parse_csv, read_limited
from core.quality import check_quality, prepare_experiment
from core.performance import analyze_performance
from core.events import detect_events

PROJECT_VERSION = "lab-project-v8.0"
MAX_PACKAGE_BYTES = 128 * 1024 * 1024
MAX_RAW_BYTES = 64 * 1024 * 1024
MAX_CSV_BYTES = 20 * 1024 * 1024
MAX_CSV_ROWS = 200000
MAX_MANIFEST_BYTES = 16 * 1024 * 1024
MAX_REPORT_BYTES = 32 * 1024 * 1024
MAX_SAVED_EXPERIMENTS = 8
MAX_JSON_DEPTH = 48
_ALGORITHMS = {"import": IMPORT_VERSION, "metrics": METRICS_VERSION,
               "events": EVENTS_VERSION, "comparison": COMPARISON_VERSION,
               "report": REPORT_VERSION}
_MANIFEST_KEYS = {"schema_version", "algorithms", "name", "notes", "current",
                  "saved_experiments", "saved_event_configs", "comparison", "historical_report"}
_RUN_KEYS = {"raw_sha256", "filename", "metadata", "parser_options", "parsed",
             "prepared", "quality", "performance", "evidence"}
_METADATA_KEYS = {"source_label", "source_kind", "example_id", "quantity", "unit",
                  "time_unit", "optional_units", "control_unit", "output_limits", "conditions"}
_SECRET_PATTERN = re.compile(r"\bsk-[\w-]{16,}")
_HASH = re.compile(r"[a-f0-9]{64}\Z")

@dataclass
class ProjectRun:
    parsed: ParsedCSV
    metadata: dict
    parser_options: ParserOptions
    prepared: PreparedExperiment | None = None
    quality: QualityReport | None = None
    performance: PerformanceResult | None = None
    evidence: EvidenceReport | None = None

@dataclass
class ProjectData:
    name: str
    notes: str = ""
    current: ProjectRun | None = None
    saved_experiments: dict[str, SavedExperiment] = field(default_factory=dict)
    saved_event_configs: dict[str, EventConfig] = field(default_factory=dict)
    comparison: ComparisonResult | None = None
    historical_report: ReportSnapshot | None = None

class ProjectError(ValueError):
    """只暴露固定的中文边界错误，不包含输入内容。"""

def export_project(project: ProjectData, *, secrets=()) -> bytes:
    """显式保存完整原始 CSV；重算核验不通过时整体拒绝，不静默修复。"""
    try:
        return _export(project, _secrets(secrets))
    except ProjectError:
        raise
    except Exception:
        raise ProjectError("项目包导出失败：来源、配置、结果或结构不一致；请重新确认分析。") from None

def load_project(source: bytes | BinaryIO, *, secrets=()) -> ProjectData:
    """有界读取、逐项验证并重算后才返回完整新对象；不修改现有会话。"""
    try:
        raw = source if isinstance(source, bytes) else read_limited(source, MAX_PACKAGE_BYTES)
        if not isinstance(raw, bytes) or len(raw) > MAX_PACKAGE_BYTES:
            raise ProjectError("项目包超过本机允许的大小上限。")
        return _load(raw, _secrets(secrets))
    except ProjectError:
        raise
    except Exception:
        raise ProjectError("项目包恢复失败：格式、摘要、版本或计算核验不通过；未恢复任何数据。") from None

def project_filename(raw: bytes) -> str:
    return "lab-project-" + hashlib.sha256(raw).hexdigest()[:20] + ".labproj"


def _secrets(values):
    if isinstance(values, str):
        values = (values,)
    return tuple(value for value in values if isinstance(value, str) and value)


def _scan_text(value, secrets):
    if any(secret in value for secret in secrets) or _SECRET_PATTERN.search(value):
        raise ProjectError("项目内容含已配置凭据或疑似密钥；请移除敏感信息后重新保存或导入。")


def _scan_raw(raw, secrets):
    # UTF-16 和 GB18030 中的 ASCII 密钥可能不具有 UTF-8 字节形式。
    # 扫描只读，不改变 CSV；严格解析仍由用户记录的编码决定。
    for encoding in ("utf-8", "utf-16-le", "utf-16-be", "gb18030", "cp1252"):
        text = raw.decode(encoding, errors="ignore")
        _scan_text(text, secrets)


def _plain(value):
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    raise TypeError("无法序列化")


def _encode(value):
    # 保留已确认字段映射的插入顺序：prepare_experiment 按该顺序建列。
    # 身份摘要仍由原核心模块各自的规范序列化定义，本层不改写其算法。
    return json.dumps(value, ensure_ascii=False, sort_keys=False, allow_nan=False,
                      separators=(",", ":"), default=_plain).encode("utf-8")


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ProjectError("项目 JSON 含重复字段，不能确定唯一含义。")
        result[key] = value
    return result


def _json(raw, limit, secrets):
    if not isinstance(raw, bytes) or len(raw) > limit:
        raise ProjectError("项目 JSON 超过本机允许的大小上限。")
    text = raw.decode("utf-8", errors="strict")
    # 在解码容器前限制层数，避免 json.loads 深递归；引号内字符不算层数。
    depth = 0
    quoted = escaped = False
    for char in text:
        if quoted:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
        elif char == '"':
            quoted = True
        elif char in "[{":
            depth += 1
            if depth > MAX_JSON_DEPTH:
                raise ProjectError("项目 JSON 嵌套层数超过限制。")
        elif char in "]}":
            depth -= 1
    def invalid_number(_):
        raise ProjectError("项目 JSON 必须使用有限标准数值。")
    value = json.loads(text, object_pairs_hook=_object, parse_constant=invalid_number)
    pending = [value]
    while pending:
        item = pending.pop()
        if isinstance(item, dict):
            pending.extend(item.keys())
            pending.extend(item.values())
        elif isinstance(item, list):
            pending.extend(item)
        elif isinstance(item, str):
            _scan_text(item, secrets)
        elif isinstance(item, float) and not math.isfinite(item):
            invalid_number(None)
    return value


def _keys(value, expected):
    if not isinstance(value, dict) or set(value) != set(expected):
        raise ProjectError("项目结构缺少必要字段或包含不允许的字段。")


def _text(value, limit, *, empty=False):
    if not isinstance(value, str) or len(value) > limit or (not empty and not value.strip()):
        raise ProjectError("项目名称、备注或来源文本格式不符合限制。")
    return value


def _metadata(value, secrets, *, exporting=False):
    if not isinstance(value, dict):
        raise ProjectError("项目来源元数据必须为结构化对象。")
    _json(_encode(value), MAX_MANIFEST_BYTES, secrets)
    if not exporting and not set(value) <= _METADATA_KEYS:
        raise ProjectError("项目来源元数据包含不允许的字段。")
    result = {key: val for key, val in value.items() if key in _METADATA_KEYS}
    for key, val in result.items():
        if key == "source_kind":
            SourceKind(val)
        elif key == "time_unit":
            if val not in ("s", "ms"):
                raise ProjectError("项目时间单位无效。")
        elif key == "optional_units":
            if not isinstance(val, dict) or not set(val) <= {"control", "p_term", "i_term", "d_term"}:
                raise ProjectError("项目可选字段单位无效。")
            for unit in val.values():
                _text(unit, 200)
        elif key == "conditions":
            result[key] = ExperimentConditions.model_validate(val).model_dump(mode="json")
        elif key == "output_limits":
            _keys(val, {"lower", "upper"})
            if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in val.values()) or val["lower"] >= val["upper"]:
                raise ProjectError("项目输出限幅提示无效。")
        else:
            _text(val, 2000)
    return result


def _options(value):
    options = ParserOptions.model_validate(value)
    if options.max_bytes > MAX_CSV_BYTES or options.max_rows > MAX_CSV_ROWS:
        raise ProjectError("项目内解析限制超过本机上限；项目不能自行放宽限制。")
    return options


def _saved_options(parsed):
    # 旧 SavedExperiment 只保留解析操作文本；精确提取已有程序生成的限制，
    # 而不是用更紧新限制改写转换记录。记录缺失时拒绝保存。
    match = re.fullmatch(r"导入限制：(\d+) bytes / (\d+) 数据记录", parsed.operations[-1])
    if not match:
        raise ProjectError("实验快照缺少原解析限制记录。")
    return _options(ParserOptions(encoding=parsed.encoding, delimiter=parsed.delimiter,
                    max_bytes=int(match[1]), max_rows=int(match[2])))


def _parsed_summary(parsed):
    return {"encoding": parsed.encoding, "delimiter": parsed.delimiter,
            "operations": list(parsed.operations), "row_count": len(parsed.frame)}


def _prepared_summary(prepared):
    return {"mapping": prepared.mapping.model_dump(mode="json"),
            "experiment": prepared.experiment.model_dump(mode="json"),
            "operations": list(prepared.operations)}


def _quality_summary(quality):
    return json.loads(_encode(asdict(quality)))


def _run_export(run, blobs, secrets):
    if not isinstance(run, ProjectRun):
        raise ProjectError("项目实验条目类型错误。")
    parsed = run.parsed
    _text(parsed.filename, 1000)
    options = _options(run.parser_options)
    if not isinstance(parsed.raw_bytes, bytes) or len(parsed.raw_bytes) > min(MAX_CSV_BYTES, options.max_bytes):
        raise ProjectError("项目原始 CSV 超过允许的单文件大小上限。")
    _scan_raw(parsed.raw_bytes, secrets)
    canonical = parse_csv(parsed.raw_bytes, parsed.filename, options)
    if (canonical.sha256 != parsed.sha256 or not canonical.frame.equals(parsed.frame)
        or canonical.source_lines != parsed.source_lines
        or _parsed_summary(canonical) != _parsed_summary(parsed)):
        raise ProjectError("原始 CSV、摘要、解析表或操作记录不一致。")
    if parsed.sha256 not in blobs:
        if sum(map(len, blobs.values())) + len(parsed.raw_bytes) > MAX_RAW_BYTES:
            raise ProjectError("项目中唯一原始 CSV 总大小超过本机上限。")
        blobs[parsed.sha256] = parsed.raw_bytes
    value = {"raw_sha256": parsed.sha256, "filename": parsed.filename,
             "metadata": _metadata(run.metadata, secrets, exporting=True),
             "parser_options": options.model_dump(mode="json"), "parsed": _parsed_summary(parsed),
             "prepared": _prepared_summary(run.prepared) if run.prepared is not None else None,
             "quality": _quality_summary(run.quality) if run.quality is not None else None,
             "performance": run.performance.model_dump(mode="json") if run.performance is not None else None,
             "evidence": run.evidence.model_dump(mode="json") if run.evidence is not None else None}
    rebuilt = _run_load(value, blobs, secrets)
    if run.prepared is not None and (run.prepared.parsed is not parsed or not rebuilt.prepared.frame.equals(run.prepared.frame)):
        # 允许独立但等值的 ParsedCSV，不能把不同来源对象藏在 prepared 中。
        other = run.prepared.parsed
        if (other.raw_bytes != parsed.raw_bytes or other.sha256 != parsed.sha256
            or other.filename != parsed.filename or not other.frame.equals(parsed.frame)
            or other.source_lines != parsed.source_lines or _parsed_summary(other) != _parsed_summary(parsed)
            or not rebuilt.prepared.frame.equals(run.prepared.frame)):
            raise ProjectError("规范数据与当前原始 CSV 不一致。")
    return value


def _run_load(value, blobs, secrets):
    _keys(value, _RUN_KEYS)
    digest = value["raw_sha256"]
    if not isinstance(digest, str) or not _HASH.fullmatch(digest) or digest not in blobs:
        raise ProjectError("项目缺少对应原始 CSV 摘要成员。")
    metadata = _metadata(value["metadata"], secrets)
    options = _options(value["parser_options"])
    parsed = parse_csv(blobs[digest], _text(value["filename"], 1000), options)
    if parsed.sha256 != digest or _parsed_summary(parsed) != value["parsed"]:
        raise ProjectError("项目 CSV 摘要、格式或解析记录不一致。")
    run = ProjectRun(parsed, metadata, options)
    if value["prepared"] is None:
        if any(value[key] is not None for key in ("quality", "performance", "evidence")):
            raise ProjectError("未确认字段的实验不能携带分析结果。")
        return run
    _keys(value["prepared"], {"mapping", "experiment", "operations"})
    if value["quality"] is None:
        raise ProjectError("已确认实验缺少质量检查配置和结果。")
    prepared_value = value["prepared"]
    kind = SourceKind(prepared_value["experiment"]["source"]["kind"])
    if metadata.get("source_kind", kind.value) != kind.value:
        raise ProjectError("项目来源声明与分析来源不一致。")
    prepared = prepare_experiment(parsed, MappingConfig.model_validate(prepared_value["mapping"]), kind)
    if _prepared_summary(prepared) != prepared_value:
        raise ProjectError("字段、单位、来源或转换记录与原始 CSV 不一致。")
    quality = check_quality(prepared, QualityConfig.model_validate(value["quality"]["config"]))
    if _quality_summary(quality) != value["quality"]:
        raise ProjectError("项目质量结果与原始 CSV 重新检查结果不一致。")
    run.prepared, run.quality = prepared, quality
    if value["performance"] is not None:
        old = PerformanceResult.model_validate(value["performance"])
        if (old.config.min_interval_ratio != quality.config.min_interval_ratio
            or old.config.max_interval_ratio != quality.config.max_interval_ratio
            or (old.config.reference_interval_s is not None
                and old.config.reference_interval_s != quality.interval_stats.get("median_s"))):
            raise ProjectError("项目指标连续性配置与质量检查参数不一致。")
        fresh = analyze_performance(prepared, quality, old.start_row, old.end_row, old.config,
            source_label=old.source_label, step=old.step,
            step_start_row=old.step_start_row, step_end_row=old.step_end_row)
        if fresh.model_dump(mode="json") != value["performance"]:
            raise ProjectError("项目分析结果、区间或参数与当前算法重算不一致。")
        run.performance = fresh
    if value["evidence"] is not None:
        old = EvidenceReport.model_validate(value["evidence"])
        fresh = detect_events(prepared, quality, old.config)
        if fresh.model_dump(mode="json") != value["evidence"]:
            raise ProjectError("项目异常证据与当前规则重算不一致。")
        run.evidence = fresh
    return run


def _historical_report(raw, digest, secrets):
    if not isinstance(digest, str) or not _HASH.fullmatch(digest) or hashlib.sha256(raw).hexdigest() != digest:
        raise ProjectError("历史报告摘要不一致。")
    value = _json(raw, MAX_REPORT_BYTES, secrets)
    _keys(value, {"schema_version", "report_id", "created_at", "project", "notes", "normalization",
                 "runs", "comparison", "comparison_charts", "ai", "limitations", "next_verification", "privacy"})
    if value["schema_version"] != REPORT_VERSION:
        raise ProjectError("历史报告格式版本不受当前程序支持。")
    if not isinstance(value["report_id"], str) or not re.fullmatch(r"report-[a-f0-9]{32}", value["report_id"]):
        raise ProjectError("历史报告标识格式不正确。")
    for key in ("created_at", "project", "normalization"):
        _text(value[key], 2000)
    _text(value["notes"], 4000, empty=True)
    if not isinstance(value["runs"], list) or not 1 <= len(value["runs"]) <= MAX_SAVED_EXPERIMENTS + 1:
        raise ProjectError("历史报告实验数量无效。")
    for run in value["runs"]:
        required_run_keys = {"result_id", "experiment_id", "name", "notes", "source_label", "conditions",
                   "source", "mapping", "quality", "performance", "evidence", "operations", "parser",
                   "configuration_id", "charts", "chart_policy"}
        if isinstance(run, dict) and "evidence_provenance" in run:
            _text(run["evidence_provenance"], 2000)
            required_run_keys.add("evidence_provenance")
        _keys(run, required_run_keys)
        MappingConfig.model_validate(run["mapping"])
        if run["performance"] is not None:
            PerformanceResult.model_validate(run["performance"])
        if run["evidence"] is not None:
            EvidenceReport.model_validate(run["evidence"])
        if not isinstance(run["charts"], list):
            raise ProjectError("历史报告图表结构无效。")
    if value["comparison"] is not None:
        ComparisonResult.model_validate(value["comparison"])
    if not isinstance(value["ai"], dict) or not isinstance(value["comparison_charts"], list):
        raise ProjectError("历史报告分区结构无效。")
    # 不恢复 AI 身份、不喂给报告 HTML 生成器：仅供历史 JSON 下载。
    # 此处校验只声明结构/摘要一致，不声明外部报告内数值真实或重新计算。
    return ReportSnapshot(raw.decode("utf-8"), digest)


def _export(project, secrets):
    if not isinstance(project, ProjectData):
        raise ProjectError("项目对象类型错误。")
    name = _text(project.name, 200)
    notes = _text(project.notes, 4000, empty=True)
    if len(project.saved_experiments) > MAX_SAVED_EXPERIMENTS:
        raise ProjectError("项目已保存实验快照超过本机数量上限。")
    if not set(project.saved_event_configs) <= set(project.saved_experiments):
        raise ProjectError("项目异常配置引用了不存在的实验快照。")
    blobs = {}
    current = _run_export(project.current, blobs, secrets) if project.current is not None else None
    saved_values = {}
    for identifier, saved in project.saved_experiments.items():
        if not isinstance(saved, SavedExperiment) or identifier != saved.snapshot_id:
            raise ProjectError("项目实验快照标识不匹配。")
        run = ProjectRun(saved.prepared.parsed, {
            "source_kind": saved.prepared.experiment.source.kind.value,
            "source_label": saved.performance.source_label}, _saved_options(saved.prepared.parsed),
            saved.prepared, saved.quality, saved.performance)
        captured = capture_experiment(saved.prepared, saved.quality, saved.performance, saved.name, saved.conditions)
        if captured.snapshot_id != identifier:
            raise ProjectError("项目实验快照名称、条件或结果已变化。")
        saved_values[identifier] = {"snapshot_id": identifier, "name": saved.name,
            "run": _run_export(run, blobs, secrets), "conditions": saved.conditions.model_dump(mode="json")}
    if current is None and not saved_values and project.historical_report is None:
        raise ProjectError("项目没有可保存的实验或历史报告。")
    report_raw = None
    report_meta = None
    if project.historical_report is not None:
        report_raw = project.historical_report.json_text.encode("utf-8")
        _historical_report(report_raw, project.historical_report.sha256, secrets)
        report_meta = {"filename": "report.json", "sha256": project.historical_report.sha256}
    manifest = {"schema_version": PROJECT_VERSION, "algorithms": _ALGORITHMS,
        "name": name, "notes": notes, "current": current, "saved_experiments": saved_values,
        "saved_event_configs": {key: EventConfig.model_validate(val).model_dump(mode="json") for key, val in project.saved_event_configs.items()},
        "comparison": project.comparison.model_dump(mode="json") if project.comparison is not None else None,
        "historical_report": report_meta}
    encoded = _encode(manifest)
    # 严格 JSON 校验也应用于本地导出，秘密不会因存于未知字段被忽略。
    _json(encoded, MAX_MANIFEST_BYTES, secrets)
    _verify_comparison(manifest["comparison"], project.saved_experiments)
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_STORED, allowZip64=False) as archive:
        archive.writestr("manifest.json", encoded)
        for digest, raw in sorted(blobs.items()):
            archive.writestr("blobs/" + digest + ".csv", raw)
        if report_raw is not None:
            archive.writestr("report.json", report_raw)
    raw = output.getvalue()
    if len(raw) > MAX_PACKAGE_BYTES:
        raise ProjectError("项目包超过本机允许的大小上限。")
    return raw


def _verify_comparison(value, saved):
    if value is None:
        return None
    old = ComparisonResult.model_validate(value)
    a, b = saved.get(old.a.snapshot_id), saved.get(old.b.snapshot_id)
    if a is None or b is None:
        raise ProjectError("项目对比引用了未包含的实验快照。")
    fresh = compare_experiments(a, b, old.config, old.a.conditions, old.b.conditions)
    if fresh.model_dump(mode="json") != value:
        raise ProjectError("项目对比的条件、窗口或数值与重算结果不一致。")
    return fresh


def _zip_members(raw):
    # 本格式只生成无注释、无 ZIP64 的普通 ZIP。先看固定 EOCD，
    # 避免 ZipFile 在数量校验前创建攻击者指定的大量中央目录对象。
    if len(raw) < 22 or raw[-22:-18] != b"PK\x05\x06":
        raise ProjectError("项目包不是支持的无注释 ZIP 格式。")
    _, disk, directory_disk, disk_count, count, size, offset, comment = struct.unpack("<4s4H2LH", raw[-22:])
    if (disk or directory_disk or disk_count != count or comment or not 1 <= count <= MAX_SAVED_EXPERIMENTS + 3
        or size > count * 120 or offset + size != len(raw) - 22):
        raise ProjectError("项目 ZIP 目录、成员数量或磁盘格式不受支持。")
    # 每条中央目录固定 46 字节，最长允许的 ASCII 名为 74 字节；
    # EOCD 中的数量也不可信，分配 ZipInfo 对象前逐条核对数量和总长度。
    cursor = offset
    for _ in range(count):
        if raw[cursor:cursor+4] != b"PK\x01\x02" or cursor + 46 > offset + size:
            raise ProjectError("项目 ZIP 中央目录条目不完整。")
        name_length, extra_length, comment_length = struct.unpack_from("<3H", raw, cursor + 28)
        if extra_length or comment_length or name_length > 74:
            raise ProjectError("项目 ZIP 中央目录名称或额外属性不受支持。")
        cursor += 46 + name_length
    if cursor != offset + size:
        raise ProjectError("项目 ZIP 中央目录实际条目数不一致。")
    archive = zipfile.ZipFile(io.BytesIO(raw), "r", allowZip64=False)
    infos = archive.infolist()
    if len(infos) != count or len({info.filename for info in infos}) != count:
        raise ProjectError("项目 ZIP 含重复成员或目录数量不一致。")
    total_raw = 0
    expected_offset = 0
    for info in infos:
        name = info.filename
        is_blob = re.fullmatch(r"blobs/[a-f0-9]{64}\.csv", name) is not None
        if not is_blob and name not in ("manifest.json", "report.json"):
            raise ProjectError("项目 ZIP 含路径、目录或不允许的成员。")
        mode = info.external_attr >> 16
        if (info.compress_type != zipfile.ZIP_STORED or info.compress_size != info.file_size
            or info.flag_bits != 0 or info.extra or info.comment or stat.S_ISLNK(mode)
            or (stat.S_IFMT(mode) not in (0, stat.S_IFREG))):
            raise ProjectError("项目 ZIP 不允许压缩、加密、链接或额外属性。")
        limit = MAX_CSV_BYTES if is_blob else MAX_MANIFEST_BYTES if name == "manifest.json" else MAX_REPORT_BYTES
        if info.file_size > limit:
            raise ProjectError("项目 ZIP 成员大小超过本机上限。")
        if is_blob:
            total_raw += info.file_size
            if total_raw > MAX_RAW_BYTES:
                raise ProjectError("项目中唯一原始 CSV 总大小超过本机上限。")
        # 校验本地目录也无额外头、隐藏字节、前后缀或不一致名称。
        position = info.header_offset
        if position != expected_offset or raw[position:position+4] != b"PK\x03\x04":
            raise ProjectError("项目 ZIP 含隐藏内容或不一致的成员偏移。")
        header = struct.unpack("<4s5H3L2H", raw[position:position+30])
        _, _, flags, compression, _, _, crc, compressed_size, file_size, name_length, extra_length = header
        actual_name = raw[position+30:position+30+name_length]
        if (actual_name != name.encode("ascii") or extra_length or flags or compression
            or crc != info.CRC or compressed_size != info.compress_size or file_size != info.file_size):
            raise ProjectError("项目 ZIP 本地头与中央目录不一致。")
        expected_offset = position + 30 + name_length + file_size
    if expected_offset != offset:
        raise ProjectError("项目 ZIP 含不属于允许成员的额外数据。")
    return archive, infos


def _load(raw, secrets):
    archive, infos = _zip_members(raw)
    members = {}
    with archive:
        for info in infos:
            with archive.open(info, "r") as stream:
                members[info.filename] = read_limited(stream, max(1, info.file_size))
    if "manifest.json" not in members:
        raise ProjectError("项目包缺少 manifest.json。")
    manifest = _json(members["manifest.json"], MAX_MANIFEST_BYTES, secrets)
    _keys(manifest, _MANIFEST_KEYS)
    if manifest["schema_version"] != PROJECT_VERSION or manifest["algorithms"] != _ALGORITHMS:
        raise ProjectError("项目格式或算法版本与当前程序不兼容；请使用原版本核对后重新导出。")
    name, notes = _text(manifest["name"], 200), _text(manifest["notes"], 4000, empty=True)
    saved_values = manifest["saved_experiments"]
    if not isinstance(saved_values, dict) or len(saved_values) > MAX_SAVED_EXPERIMENTS:
        raise ProjectError("项目实验快照数量或类型无效。")
    if not isinstance(manifest["saved_event_configs"], dict) or not set(manifest["saved_event_configs"]) <= set(saved_values):
        raise ProjectError("项目异常配置引用了未包含的实验快照。")
    referenced = []
    if manifest["current"] is not None:
        referenced.append(manifest["current"])
    for identifier, value in saved_values.items():
        _keys(value, {"snapshot_id", "name", "run", "conditions"})
        if identifier != value["snapshot_id"]:
            raise ProjectError("项目实验快照字典标识不一致。")
        referenced.append(value["run"])
    expected = {"manifest.json"}
    for value in referenced:
        _keys(value, _RUN_KEYS)
        digest = value["raw_sha256"]
        if not isinstance(digest, str) or not _HASH.fullmatch(digest):
            raise ProjectError("项目原始 CSV 摘要格式无效。")
        expected.add("blobs/" + digest + ".csv")
    history = manifest["historical_report"]
    if history is not None:
        _keys(history, {"filename", "sha256"})
        if history["filename"] != "report.json":
            raise ProjectError("项目历史报告文件名不受支持。")
        expected.add("report.json")
    if set(members) != expected:
        raise ProjectError("项目 ZIP 含缺失或未引用成员。")
    blobs = {}
    for path in expected - {"manifest.json", "report.json"}:
        digest = path[6:-4]
        if hashlib.sha256(members[path]).hexdigest() != digest:
            raise ProjectError("项目原始 CSV 字节摘要不一致。")
        _scan_raw(members[path], secrets)
        blobs[digest] = members[path]
    current = _run_load(manifest["current"], blobs, secrets) if manifest["current"] is not None else None
    saved = {}
    for identifier, value in saved_values.items():
        run = _run_load(value["run"], blobs, secrets)
        if run.prepared is None or run.quality is None or run.performance is None:
            raise ProjectError("项目快照缺少已确认的单次阶跃分析。")
        item = capture_experiment(run.prepared, run.quality, run.performance,
            _text(value["name"], 200), ExperimentConditions.model_validate(value["conditions"]))
        if item.snapshot_id != identifier:
            raise ProjectError("项目快照身份与来源、条件或确认结果不一致。")
        saved[identifier] = item
    event_configs = {key: EventConfig.model_validate(val) for key, val in manifest["saved_event_configs"].items()}
    comparison = _verify_comparison(manifest["comparison"], saved)
    report = _historical_report(members["report.json"], history["sha256"], secrets) if history is not None else None
    if current is None and not saved and report is None:
        raise ProjectError("项目包没有实验或历史报告。")
    return ProjectData(name, notes, current, saved, event_configs, comparison, report)
