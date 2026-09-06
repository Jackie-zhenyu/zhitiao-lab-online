"""第八阶段独立对抗验收：外部项目包不获得文件、身份或可信结果权限。"""

from io import BytesIO
import json
import struct
from zipfile import ZIP_DEFLATED, ZIP_STORED, ZipFile, ZipInfo

import pytest

from core.import_models import ParserOptions
from core.parser import parse_csv
from projects import archive
from projects.archive import ProjectData, ProjectError, ProjectRun, export_project, load_project


def raw_project(*, name="安全验收", notes="", filename="数学夹具.csv", raw=None, encoding="utf-8"):
    if raw is None:
        raw = "time,target,actual\n0,1,0\n1,1,0.5\n2,1,0.8\n".encode(encoding)
    options = ParserOptions(encoding=encoding, delimiter=",")
    parsed = parse_csv(raw, filename, options)
    return ProjectData(name=name, notes=notes, current=ProjectRun(
        parsed=parsed, metadata={"source_kind": "simulated", "source_label": "解析函数合成数据"},
        parser_options=options,
    ))


@pytest.fixture
def good_archive():
    return export_project(raw_project())


def entries(raw):
    with ZipFile(BytesIO(raw)) as package:
        return [(info.filename, package.read(info)) for info in package.infolist()]


def pack(items, compression=ZIP_STORED):
    target = BytesIO()
    with ZipFile(target, "w", compression=compression) as package:
        for name, content in items:
            package.writestr(name, content)
    return target.getvalue()


def change_manifest(raw, change):
    contents = entries(raw)
    manifest = json.loads(dict(contents)["manifest.json"])
    change(manifest)
    encoded = json.dumps(manifest, ensure_ascii=False, allow_nan=False).encode("utf-8")
    return pack([(name, encoded if name == "manifest.json" else content) for name, content in contents])


@pytest.mark.parametrize("name", [
    "../outside.csv", "/absolute.csv", "C:/outside.csv", "C:\\outside.csv",
    "blobs/../../outside.csv", "blobs\\outside.csv", "./manifest.json", "extra.py", ".env",
])
def test_unlisted_or_path_like_members_are_rejected_without_extraction(good_archive, monkeypatch, name):
    extracted = []
    def forbidden(*args, **kwargs):
        extracted.append(True)
        raise AssertionError("恢复项目包不得解压写盘")
    monkeypatch.setattr(ZipFile, "extract", forbidden)
    monkeypatch.setattr(ZipFile, "extractall", forbidden)
    with pytest.raises(ProjectError):
        load_project(pack(entries(good_archive) + [(name, b"untrusted")]))
    assert not extracted


def test_duplicate_manifest_member_is_rejected(good_archive):
    members = entries(good_archive)
    with pytest.warns(UserWarning, match="Duplicate name"):
        bad = pack(members + [("manifest.json", dict(members)["manifest.json"])])
    with pytest.raises(ProjectError):
        load_project(bad)


def test_compressed_member_is_rejected_even_when_small_and_valid(good_archive):
    # 格式只允许 STORED，拒绝压缩本身；不需要真的创建巨大解压文件。
    with pytest.raises(ProjectError):
        load_project(pack(entries(good_archive), ZIP_DEFLATED))


def test_symlink_member_cannot_reuse_an_allowed_blob_name(good_archive):
    members = entries(good_archive)
    blob_name = next(name for name, _ in members if name.startswith("blobs/"))
    link = ZipInfo(blob_name)
    link.create_system = 3
    link.external_attr = (0o120777 << 16)
    with pytest.raises(ProjectError):
        load_project(pack([(link if name == blob_name else name, content) for name, content in members]))


def test_encrypted_flag_is_rejected_as_controlled_error(good_archive):
    bad = bytearray(good_archive)
    # 本地头与中央目录中 manifest 的 general-purpose bit 0。
    for signature, flag_offset in ((b"PK\x03\x04", 6), (b"PK\x01\x02", 8)):
        position = bad.index(signature)
        old = struct.unpack_from("<H", bad, position + flag_offset)[0]
        struct.pack_into("<H", bad, position + flag_offset, old | 1)
    with pytest.raises(ProjectError):
        load_project(bytes(bad))


def test_forged_small_directory_count_is_bounded_before_zipfile_allocates_members(good_archive, monkeypatch):
    # ZipFile 根据 directory size 读实际条目，不以 EOCD 声明 count 限制对象数。
    # 即便攻击包谎称只有一项，仍应先限制中央目录字节数。
    bad = bytearray(good_archive)
    eocd = len(bad) - 22
    struct.pack_into("<H", bad, eocd + 8, 1)
    struct.pack_into("<H", bad, eocd + 10, 1)
    used_zipfile = []
    original = archive.zipfile.ZipFile

    def watched(*args, **kwargs):
        used_zipfile.append(True)
        return original(*args, **kwargs)
    monkeypatch.setattr(archive.zipfile, "ZipFile", watched)
    with pytest.raises(ProjectError):
        load_project(bytes(bad))
    assert not used_zipfile, "中央目录分配前必须核对目录字节数与最大允许成员数"


@pytest.mark.parametrize("raw", [b"", b"not a project", b"PK\x03\x04", b"\x80\x04cos\nsystem\n"])
def test_non_zip_or_truncated_input_never_executes_or_leaks_parser_errors(raw):
    with pytest.raises(ProjectError):
        load_project(raw)


@pytest.mark.parametrize("field,value", [
    ("session_id", "other-session"), ("agent_panel_state", {"evidence": {"forged": 42}}),
    ("consent", True), ("LLM_API_KEY", "foreign-test-credential"),
])
def test_session_identity_authorization_and_server_configuration_are_not_import_fields(good_archive, field, value):
    bad = change_manifest(good_archive, lambda manifest: manifest.update({field: value}))
    with pytest.raises(ProjectError):
        load_project(bad)


def test_future_project_version_requires_explicit_migration(good_archive):
    bad = change_manifest(good_archive, lambda manifest: manifest.update(schema_version="lab-project-v999.0"))
    with pytest.raises(ProjectError):
        load_project(bad)


def test_changed_algorithm_marker_is_not_silently_accepted(good_archive):
    def change(manifest):
        key = next(iter(manifest["algorithms"]))
        manifest["algorithms"][key] = "unverified-future-algorithm"
    with pytest.raises(ProjectError):
        load_project(change_manifest(good_archive, change))


def test_duplicate_json_keys_are_rejected_instead_of_last_value_winning(good_archive):
    members = entries(good_archive)
    manifest = dict(members)["manifest.json"]
    duplicate = b'{"name":"attacker",' + manifest.lstrip()[1:]
    with pytest.raises(ProjectError):
        load_project(pack([(name, duplicate if name == "manifest.json" else raw) for name, raw in members]))


@pytest.mark.parametrize("constant", [b"NaN", b"Infinity", b"-Infinity"])
def test_nonfinite_json_numbers_are_rejected(good_archive, constant):
    members = entries(good_archive)
    manifest = dict(members)["manifest.json"]
    invalid = b'{"unknown_nonfinite":' + constant + b"," + manifest.lstrip()[1:]
    with pytest.raises(ProjectError):
        load_project(pack([(name, invalid if name == "manifest.json" else raw) for name, raw in members]))


def test_deep_json_is_rejected_without_uncaught_recursion(good_archive):
    members = entries(good_archive)
    manifest = dict(members)["manifest.json"]
    invalid = b'{"deep":' + b"[" * 2000 + b"0" + b"]" * 2000 + b"," + manifest.lstrip()[1:]
    with pytest.raises(ProjectError):
        load_project(pack([(name, invalid if name == "manifest.json" else raw) for name, raw in members]))


def test_changed_raw_content_cannot_keep_old_sha256(good_archive):
    members = entries(good_archive)
    bad = pack([(name, raw.replace(b"0.5", b"0.6") if name.startswith("blobs/") else raw) for name, raw in members])
    with pytest.raises(ProjectError):
        load_project(bad)


def test_missing_blob_is_rejected_before_any_session_exists(good_archive):
    with pytest.raises(ProjectError):
        load_project(pack([(name, raw) for name, raw in entries(good_archive) if not name.startswith("blobs/")]))


def test_archive_limit_is_applied_to_stream_reads(good_archive, monkeypatch):
    # 缩小同一生产边界来验证实际读取，而非在测试中分配百MiB字节串。
    limit = len(good_archive) - 7
    monkeypatch.setattr(archive, "MAX_PACKAGE_BYTES", limit)

    class BoundedStream(BytesIO):
        requested = []

        def read(self, size=-1):
            self.requested.append(size)
            return super().read(size)

    source = BoundedStream(good_archive)
    with pytest.raises(ProjectError):
        load_project(source)
    assert source.requested and all(size >= 0 for size in source.requested), "不得先无界读取再检查大小"
    assert source.tell() <= limit + 1


@pytest.mark.parametrize("limit_name,member_prefix", [
    ("MAX_MANIFEST_BYTES", "manifest.json"), ("MAX_CSV_BYTES", "blobs/"),
    ("MAX_RAW_BYTES", "blobs/"),
])
def test_member_and_total_raw_limits_are_independent(good_archive, monkeypatch, limit_name, member_prefix):
    size = len(next(raw for name, raw in entries(good_archive) if name.startswith(member_prefix)))
    monkeypatch.setattr(archive, limit_name, size - 1)
    with pytest.raises(ProjectError):
        load_project(good_archive)


def test_parser_options_cannot_override_effective_row_cap(good_archive, monkeypatch):
    monkeypatch.setattr(archive, "MAX_CSV_ROWS", 2)
    # 当前包确有三行；其合法旧 parser_options.max_rows 不得扩大当前恢复上限。
    with pytest.raises(ProjectError):
        load_project(good_archive)


@pytest.mark.parametrize("where", ["name", "notes", "filename", "metadata"])
def test_known_credentials_in_text_reject_export_without_mutation(where):
    secret = "PROJECT-EXPORT-TEST-CREDENTIAL-NOT-REAL"
    project = raw_project()
    if where in {"name", "notes"}:
        setattr(project, where, secret)
    elif where == "filename":
        project.current.parsed.filename = secret + ".csv"
    else:
        project.current.metadata["source_label"] = "备注 " + secret
    original = project.current.parsed.raw_bytes
    with pytest.raises(ProjectError) as error:
        export_project(project, secrets=(secret,))
    assert secret not in str(error.value)
    assert project.current.parsed.raw_bytes == original


@pytest.mark.parametrize("encoding", ["utf-8", "utf-16", "gb18030"])
def test_credentials_in_raw_csv_refuse_save_and_restore_without_rewriting(encoding):
    secret = "PROJECT-RAW-TEST-CREDENTIAL-NOT-REAL"
    raw = ("time,target,actual,notes\n0,1,0," + secret + "\n1,1,0.5,plain\n").encode(encoding)
    project = raw_project(raw=raw, encoding=encoding)
    # 未配置该Key时生成测试输入；以当前服务已配置Key导入必须再次检查。
    unsafe_for_this_server = export_project(project)
    for operation in (
        lambda: export_project(project, secrets=(secret,)),
        lambda: load_project(unsafe_for_this_server, secrets=(secret,)),
    ):
        with pytest.raises(ProjectError) as error:
            operation()
        assert secret not in str(error.value)
    assert project.current.parsed.raw_bytes == raw


def test_malicious_filename_stays_text_and_never_changes_package_paths():
    project = raw_project(filename="../../<script>alert(1)</script>.csv", notes="=1+1")
    raw = export_project(project)
    names = [name for name, _ in entries(raw)]
    assert set(names) == {"manifest.json", "blobs/" + project.current.parsed.sha256 + ".csv"}
    assert load_project(raw).current.parsed.filename == project.current.parsed.filename
    assert archive.project_filename(raw).startswith("lab-project-")
    assert "/" not in archive.project_filename(raw) and "\\" not in archive.project_filename(raw)


def test_restored_historical_report_is_not_rendered_as_trusted_html(monkeypatch):
    from test_reports import simple_report
    from reports import generator

    snapshot, _ = simple_report(notes="</script><script>untrusted()</script>")
    project = raw_project()
    project.historical_report = snapshot
    original_report = snapshot.json_text

    def forbidden(*args, **kwargs):
        raise AssertionError("导入的历史JSON只能归档，不能自动生成可信HTML")
    monkeypatch.setattr(generator, "render_html", forbidden)
    monkeypatch.setattr(generator, "build_exports", forbidden)
    restored = load_project(export_project(project))
    assert restored.historical_report.json_text == original_report
    assert not hasattr(restored, "agent_panel_state")
    assert not hasattr(restored, "report_exports")


def test_modified_historical_report_cannot_retain_its_old_digest():
    from test_reports import simple_report

    project = raw_project()
    project.historical_report, _ = simple_report(project="原报告")
    raw = export_project(project)
    tampered = pack([(name, content.replace("原报告".encode(), "假报告".encode())
                     if name == "report.json" else content) for name, content in entries(raw)])
    with pytest.raises(ProjectError):
        load_project(tampered)


def test_historical_report_has_separate_size_cap(monkeypatch):
    from test_reports import simple_report

    project = raw_project()
    project.historical_report, _ = simple_report()
    raw = export_project(project)
    monkeypatch.setattr(archive, "MAX_REPORT_BYTES", 32)
    with pytest.raises(ProjectError):
        load_project(raw)


def test_actual_ab_report_with_evidence_provenance_can_be_archived():
    from core.comparison import compare_experiments
    from core.comparison_models import ComparisonConfig
    from core.event_models import EventConfig
    from test_stage4_regressions import saved_fixture
    from ui.report_page import build_session_report
    from ui.session import LabSession

    a = saved_fixture([-1, 0, 1, 2, 3, 4, 5], name="A")
    b = saved_fixture([-1, 0, 1, 2, 3, 4, 5], name="B")
    state = LabSession(saved_experiments={item.snapshot_id: item for item in (a, b)},
                       saved_event_configs={item.snapshot_id: EventConfig() for item in (a, b)})
    state.comparison = compare_experiments(a, b, ComparisonConfig(common_duration_s=3, confirmed=True))
    snapshot = build_session_report(state, "comparison", project="A/B归档", name="对比", notes="")
    assert all(run.get("evidence_provenance") for run in snapshot.data()["runs"])
    project = ProjectData(name="实际对比报告", saved_experiments=state.saved_experiments,
                          saved_event_configs=state.saved_event_configs, comparison=state.comparison,
                          historical_report=snapshot)
    restored = load_project(export_project(project))
    assert restored.historical_report.json_text == snapshot.json_text
    assert restored.comparison == state.comparison


def test_removing_selected_history_revokes_ai_evidence_and_marks_old_package(monkeypatch):
    from agent_fixtures import answer_latest, tool_reply
    from test_agent_ui import configured, consent, submit
    from test_app import confirm_example, load_example_app
    from test_performance_ui import confirm_step

    provider, _ = configured(monkeypatch)
    app = confirm_step(confirm_example(load_example_app()))
    app.button(key="save_current_experiment").click().run()
    saved_id = next(iter(app.session_state["lab_session"].saved_experiments))
    app.selectbox(key="ai_select_a").set_value(saved_id).run()
    panel = app.session_state["agent_panel_state"]
    provider.steps.extend([tool_reply(panel.context), answer_latest])
    turn = submit(app, "分析实验A")
    assert turn.status == "success"
    old_key = panel.context.context_key
    app.chat_input[0].set_value("继续分析").run()
    consent(app).check().run()
    app.button(key="project_export").click().run()
    frozen = app.session_state["project_package"]
    app.button(key="project_remove_history").click().run()
    assert not app.exception
    assert saved_id not in app.session_state["lab_session"].saved_experiments
    assert panel.context.context_key != old_key
    assert not any(identifier in panel.context.evidence for identifier in turn.result_ids)
    assert not consent(app).value and app.button(key="send_question").disabled
    assert any("旧结果已失效" in message.value for message in app.warning)
    assert any("页面已变化" in message.value for message in app.warning)
    assert app.session_state["project_package"] == frozen
    assert len(provider.requests) == 2


def test_invalid_project_upload_preserves_current_ai_evidence_and_pending_consent(monkeypatch):
    from agent_fixtures import answer_latest, tool_reply
    from test_agent_ui import configured, consent, submit
    from test_app import confirm_example, load_example_app
    from test_project_ui import install_upload

    install_upload(monkeypatch, b"invalid archive")
    provider, _ = configured(monkeypatch)
    app = confirm_example(load_example_app())
    panel = app.session_state["agent_panel_state"]
    provider.steps.extend([tool_reply(panel.context), answer_latest])
    turn = submit(app, "分析实验A")
    context = panel.context
    old_values = app.session_state["lab_session"].performance.model_dump(mode="json")
    app.chat_input[0].set_value("继续分析").run()
    consent(app).check().run()
    app.button(key="project_validate").click().run()
    assert not app.exception and app.error
    assert app.session_state["lab_session"].performance.model_dump(mode="json") == old_values
    assert panel.context is context
    assert all(identifier in context.evidence for identifier in turn.result_ids)
    assert consent(app).value and not app.button(key="send_question").disabled
    assert len(provider.requests) == 2
    assert "project_pending_state" not in app.session_state


def test_new_same_name_upload_cannot_reuse_previous_restore_confirmation(monkeypatch):
    from streamlit.testing.v1 import AppTest
    from test_app import APP_PATH
    from test_project_ui import install_upload

    first = export_project(raw_project(name="第一个项目"))
    second = export_project(raw_project(name="同名文件内的新项目"))
    selected, upload = install_upload(monkeypatch, first)
    app = AppTest.from_file(str(APP_PATH), default_timeout=20).run()
    app.button(key="project_validate").click().run()
    app.checkbox(key="project_restore_ack").check().run()
    assert not app.button(key="project_restore").disabled
    selected["file"] = upload(second)
    app.run()
    assert not app.exception and "project_candidate" not in app.session_state
    assert not any(button.key == "project_restore" for button in app.button)
    app.button(key="project_validate").click().run()
    assert not app.exception and not app.error
    assert app.session_state["project_candidate"][1].name == "同名文件内的新项目"
    assert not app.checkbox(key="project_restore_ack").value
    assert app.button(key="project_restore").disabled
    assert app.session_state["lab_session"].raw_bytes is None
