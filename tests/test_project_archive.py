"""项目包往返、独立对象、重算拒绝和历史 JSON；全离线、原始数学夹具。"""
from copy import deepcopy
from dataclasses import asdict
import io
import json
import zipfile

import pytest

from core.comparison import capture_experiment, compare_experiments
from core.comparison_models import ComparisonConfig
from core.event_models import EventConfig, OutputLimits
from core.events import detect_events
from core.import_models import MappingConfig, ParserOptions
from core.parser import parse_csv
from core.performance import analyze_performance
from core.quality import check_quality, prepare_experiment
from core.models import SourceKind
from projects.archive import ProjectData, ProjectError, ProjectRun, export_project, load_project, project_filename
from reports.snapshot import capture_run, create_report, ReportSnapshot
from test_stage4_regressions import prepared_fixture, saved_fixture


def project_fixture():
    saved = saved_fixture([-1, 0, 1, 2, 3, 4, 5], name="中文实验")
    p = saved.prepared
    evidence = detect_events(p, saved.quality, EventConfig())
    current = ProjectRun(p.parsed, {"source_kind": "simulated", "source_label": "解析函数合成数据",
        "quantity": p.mapping.quantity, "unit": p.mapping.unit, "time_unit": "s"},
        ParserOptions(), p, saved.quality, saved.performance, evidence)
    return ProjectData("中文项目", "原始数据只读；来源未经鉴定", current), saved


def mutate_package(raw, mutation):
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        members = {info.filename: archive.read(info) for info in archive.infolist()}
    manifest = json.loads(members["manifest.json"])
    mutation(manifest)
    members["manifest.json"] = json.dumps(manifest, ensure_ascii=False, allow_nan=False).encode()
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_STORED) as archive:
        for name, content in members.items():
            archive.writestr(name, content)
    return output.getvalue()


def test_confirmed_current_roundtrip_preserves_raw_results_configs_and_independent_objects():
    project, _ = project_fixture()
    raw = export_project(project)
    loaded = load_project(io.BytesIO(raw))
    before, after = project.current, loaded.current
    assert loaded.name == project.name and loaded.notes == project.notes
    assert after.parsed.raw_bytes == before.parsed.raw_bytes
    assert after.parsed.sha256 == before.parsed.sha256
    assert after.parsed.source_lines == before.parsed.source_lines
    assert after.parsed.frame.equals(before.parsed.frame)
    assert after.prepared.frame.equals(before.prepared.frame)
    assert after.prepared.operations == before.prepared.operations
    assert after.quality == before.quality
    assert after.performance == before.performance
    assert after.evidence == before.evidence
    assert after.parser_options == before.parser_options
    after.prepared.frame.loc[0, "actual"] = 123
    after.performance.config.tail_fraction = 0.7
    assert before.prepared.frame.loc[0, "actual"] != 123
    assert before.performance.config.tail_fraction == 0.1
    assert not hasattr(loaded, "session_id") and not hasattr(loaded, "agent_panel")
    assert project_filename(raw).endswith(".labproj")


@pytest.mark.parametrize("encoding,delimiter", [("utf-8-sig", ";"), ("utf-16", "\t"), ("gb18030", ",")])
def test_parse_only_roundtrip_preserves_chinese_format_and_custom_import_limits(encoding, delimiter):
    text = delimiter.join(["时间", "目标", "实测"]) + "\n" + delimiter.join(["0", "1", "0"]) + "\n"
    options = ParserOptions(encoding=encoding, delimiter=delimiter, max_bytes=4096, max_rows=20)
    parsed = parse_csv(text.encode(encoding), "不同编码.csv", options)
    loaded = load_project(export_project(ProjectData("未确认", current=ProjectRun(parsed, {}, options))))
    assert loaded.current.parser_options == options
    assert loaded.current.parsed.raw_bytes == text.encode(encoding)
    assert loaded.current.parsed.operations == parsed.operations
    assert loaded.current.prepared is None and loaded.current.performance is None


def test_quality_only_ms_and_missing_values_are_restored_without_fabricated_analysis():
    raw = b"ms,target,actual\n0,1,0\n1000,1,\n2000,1,.5\n"
    parsed = parse_csv(raw, "missing.csv")
    mapping = MappingConfig(fields={"time": "ms", "target": "target", "actual": "actual"},
        time_unit="ms", quantity="响应", unit="1", confirmed=True, units_consistent=True)
    prepared = prepare_experiment(parsed, mapping, SourceKind.SIMULATED)
    quality = check_quality(prepared)
    run = ProjectRun(parsed, {"source_kind": "simulated"}, ParserOptions(), prepared, quality)
    restored = load_project(export_project(ProjectData("质量问题", current=run))).current
    assert restored.prepared.mapping.time_unit == "ms"
    assert restored.prepared.frame.time.tolist() == [0.0, 1.0, 2.0]
    assert restored.quality == quality and restored.quality.blocking
    assert restored.performance is None
    assert restored.parsed.raw_bytes == raw


def test_saved_ab_shared_raw_dedup_event_rules_and_common_window_roundtrip():
    project, a = project_fixture()
    b = capture_experiment(a.prepared, a.quality, a.performance, "B", a.conditions)
    project.saved_experiments = {item.snapshot_id: item for item in (a, b)}
    cfg = EventConfig(control_min_duration_s=0.8, output_limits=OutputLimits(lower=-2., upper=2., confirmed=True))
    project.saved_event_configs = {a.snapshot_id: cfg}
    project.comparison = compare_experiments(a, b, ComparisonConfig(common_duration_s=3., confirmed=True))
    raw = export_project(project)
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        assert len([name for name in archive.namelist() if name.startswith("blobs/")]) == 1
    restored = load_project(raw)
    assert restored.comparison == project.comparison
    assert restored.comparison.config.common_duration_s == 3
    assert all(item.delta == 0 for item in restored.comparison.differences if item.delta is not None)
    assert restored.saved_event_configs[a.snapshot_id] == cfg
    assert set(restored.saved_experiments) == set(project.saved_experiments)
    original = restored.current.prepared.frame.loc[0, "actual"]
    restored.saved_experiments[a.snapshot_id].prepared.frame.loc[0, "actual"] = 999
    assert restored.current.prepared.frame.loc[0, "actual"] == original
    assert restored.saved_experiments[b.snapshot_id].prepared.frame.loc[0, "actual"] == original


@pytest.mark.parametrize("field", ["value", "config", "interval", "events", "quality", "mapping", "operations"])
def test_tampered_result_or_confirmation_rejected_after_raw_recomputation(field):
    project, _ = project_fixture()
    def mutation(manifest):
        current = manifest["current"]
        if field == "value": current["performance"]["tracking"]["metrics"]["rmse_t"]["value"] = 88
        elif field == "config": current["performance"]["config"]["tail_fraction"] = .7
        elif field == "interval": current["performance"]["end_row"] -= 1
        elif field == "events": current["evidence"]["checks"][0]["reason"] = "伪造正常结论"
        elif field == "quality": current["quality"]["duration_s"] += 7
        elif field == "mapping": current["prepared"]["mapping"]["unit"] = "rad"
        elif field == "operations": current["prepared"]["operations"].append("伪造已执行转换")
    changed = mutate_package(export_project(project), mutation)
    with pytest.raises(ProjectError):
        load_project(changed)
    assert project.current.performance.tracking.metrics["rmse_t"].value != 88


@pytest.mark.parametrize("field", ["parsed_frame", "prepared_frame", "source_lines", "raw", "quality", "result"])
def test_export_rejects_mutated_in_memory_source_tables_or_stale_results(field):
    project, _ = project_fixture()
    current = project.current
    if field == "parsed_frame": current.parsed.frame.loc[0, "actual"] = "999"
    elif field == "prepared_frame": current.prepared.frame.loc[0, "actual"] = 999
    elif field == "source_lines": current.parsed.source_lines[0] += 1
    elif field == "raw": current.parsed.raw_bytes += b"6,1,1\n"
    elif field == "quality": current.quality.duration_s += 1
    elif field == "result": current.performance.tracking.metrics["iae"].value = 999
    with pytest.raises(ProjectError):
        export_project(project)


def test_same_name_different_content_keeps_distinct_raw_and_experiment_id():
    first, _ = project_fixture()
    second, _ = project_fixture()
    p = second.current
    replacement = p.parsed.raw_bytes.replace(b"5.0,1.0,0.0", b"5.0,1.0,0.1")
    assert replacement != p.parsed.raw_bytes
    parsed = parse_csv(replacement, p.parsed.filename)
    second.current = ProjectRun(parsed, {}, ParserOptions())
    a, b = load_project(export_project(first)), load_project(export_project(second))
    assert a.current.parsed.filename == b.current.parsed.filename
    assert a.current.parsed.sha256 != b.current.parsed.sha256
    assert b.current.performance is None


def test_history_json_exact_bytes_retained_without_restoring_live_ai_or_report_exports():
    project, saved = project_fixture()
    captured = capture_run(saved.prepared, saved.quality, saved.performance, None,
        name=saved.name, source_label="解析函数合成数据")
    report = create_report([captured], notes="</script><script>不可信历史文字</script>")
    project.historical_report = report
    restored = load_project(export_project(project))
    assert isinstance(restored.historical_report, ReportSnapshot)
    assert restored.historical_report.json_text == report.json_text
    assert restored.historical_report.sha256 == report.sha256
    assert restored.historical_report.data()["ai"]["status"] == "not_participated"
    assert not hasattr(restored, "report_exports") and not hasattr(restored, "agent_panel")


def test_unknown_version_in_history_rejected_even_with_updated_hash():
    project, saved = project_fixture()
    report = create_report([capture_run(saved.prepared, saved.quality, saved.performance, None,
        name=saved.name, source_label="解析函数合成数据")])
    import hashlib
    data = report.data()
    data["schema_version"] = "unsupported-next-version"
    text = json.dumps(data, ensure_ascii=False)
    project.historical_report = ReportSnapshot(text, hashlib.sha256(text.encode()).hexdigest())
    with pytest.raises(ProjectError):
        export_project(project)


@pytest.mark.parametrize("location", ["snapshot", "comparison", "event_rules"])
def test_saved_identity_comparison_orphan_and_rule_orphan_rejected(location):
    project, saved = project_fixture()
    project.saved_experiments = {saved.snapshot_id: saved}
    if location == "snapshot": saved.name = "新名字但旧身份"
    elif location == "comparison":
        other = saved_fixture([-1, 0, 1, 2, 3, 4, 5], name="未加入项目的B")
        project.comparison = compare_experiments(saved, other, ComparisonConfig(common_duration_s=3., confirmed=True))
    else: project.saved_event_configs["missing-snapshot"] = EventConfig()
    with pytest.raises(ProjectError):
        export_project(project)


def test_conflicting_continuity_configuration_is_rejected_even_when_values_recomputed():
    project, _ = project_fixture()
    run = project.current
    config = run.performance.config.model_copy(update={"min_interval_ratio": .7})
    # 底层独立函数允许直接传参数；项目恢复必须另外核对质量确认上下文。
    run.performance = analyze_performance(run.prepared, run.quality, 1, len(run.parsed.frame),
        config, source_label=run.performance.source_label)
    with pytest.raises(ProjectError, match="连续性配置"):
        export_project(project)


def test_csv_size_limit_is_checked_before_decoding_or_secret_scanning(monkeypatch):
    import projects.archive as archive
    project, _ = project_fixture()
    monkeypatch.setattr(archive, "MAX_CSV_BYTES", 8)
    project.current.parser_options = ParserOptions(max_bytes=8)
    def forbidden(*args):
        raise AssertionError("超限原始文件不能进入解码或秘密扫描")
    monkeypatch.setattr(archive, "_scan_raw", forbidden)
    with pytest.raises(ProjectError, match="单文件大小"):
        export_project(project)


@pytest.mark.parametrize("location", ["delta", "window", "conditions"])
def test_comparison_snapshot_recomputation_rejects_changed_summary(location):
    project, a = project_fixture()
    b = capture_experiment(a.prepared, a.quality, a.performance, "B", a.conditions)
    project.saved_experiments = {saved.snapshot_id: saved for saved in (a, b)}
    project.comparison = compare_experiments(a, b, ComparisonConfig(common_duration_s=3., confirmed=True))
    def mutation(manifest):
        comparison = manifest["comparison"]
        if location == "delta": comparison["differences"][0]["delta"] = 98
        elif location == "window": comparison["config"]["common_duration_s"] = 2.
        else: comparison["a"]["conditions"]["plant"] = "未经记录的新对象"
    with pytest.raises(ProjectError):
        load_project(mutate_package(export_project(project), mutation))
