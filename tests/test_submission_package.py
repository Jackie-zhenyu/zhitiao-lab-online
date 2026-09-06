"""Offline public-source delivery tests; these never install or call a model."""

from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path, PurePosixPath
import stat
from zipfile import ZipFile

import pytest

from scripts import package_submission as packaging


@pytest.fixture
def public_project(tmp_path):
    source_root = Path(__file__).resolve().parents[1]
    for name in packaging.REQUIRED_FILES:
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"# public fixture, no network or private data\n")
    (tmp_path / ".env.example").write_bytes(b"LLM_PROVIDER=qwen\nLLM_API_KEY=\n")
    for name in packaging.PUBLIC_CSV_SHA256:
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((source_root / name).read_bytes())
    return tmp_path


def test_actual_public_sources_and_five_examples_form_safe_complete_archive():
    root = Path(__file__).resolve().parents[1]
    bundle = packaging.build_submission(root, secrets=())
    manifest = json.loads(bundle.manifest_bytes)
    assert manifest["archive_sha256"] == sha256(bundle.archive_bytes).hexdigest()
    assert manifest["public_csv_count"] == 5
    with ZipFile(BytesIO(bundle.archive_bytes)) as archive:
        names = archive.namelist()
        assert len(names) == len(set(names)) == manifest["file_count"]
        assert sum(name.endswith(".csv") for name in names) == 5
        for info in archive.infolist():
            relative = PurePosixPath(info.filename)
            assert not relative.is_absolute()
            assert relative.parts[0] == "zhitiao-lab"
            assert ".." not in relative.parts and "\\" not in info.filename
            assert stat.S_ISREG(info.external_attr >> 16)
            assert info.date_time == (1980, 1, 1, 0, 0, 0)
        for item in manifest["files"]:
            raw = archive.read(packaging.PREFIX + item["path"])
            assert len(raw) == item["bytes"]
            assert sha256(raw).hexdigest() == item["sha256"]
        for name in ("scripts/install.cmd", "scripts/start.cmd", "scripts/package_submission.py",
                     "requirements.lock.txt", "docs/RUNNABLE_SUBMISSION.md", "projects/archive.py"):
            assert archive.read(packaging.PREFIX + name) == (root / name).read_bytes()
        assert archive.read(packaging.PREFIX + ".env.example").decode("utf-8-sig").find("LLM_API_KEY=\n") >= 0


def test_private_adjacent_files_caches_env_and_outputs_never_enter_zip(public_project):
    private = (".env", ".streamlit/secrets.toml", "examples/data/private.csv", "core/private.py",
               "docs/private-note.md", "tests/private.csv", "session.labproj", ".venv/token.txt",
               "work/private.log", "outputs/submission/old.zip", "outputs/private.csv", ".git/config",
               "ui/__pycache__/private.pyc", "outputs/submission/应用方案.pdf", "outputs/submission/演示.mp4")
    for name in private:
        path = public_project / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"private-content-must-not-ship")
    bundle = packaging.build_submission(public_project, secrets=())
    with ZipFile(BytesIO(bundle.archive_bytes)) as archive:
        names = archive.namelist()
        assert not any(packaging.PREFIX + name in names for name in private)
        assert all(b"private-content-must-not-ship" not in archive.read(name) for name in names)


def test_build_is_reproducible_and_leaves_source_bytes_unchanged(public_project):
    selected = packaging.REQUIRED_FILES + tuple(packaging.PUBLIC_CSV_SHA256)
    before = {name: (public_project / name).read_bytes() for name in selected}
    first = packaging.build_submission(public_project, secrets=())
    second = packaging.build_submission(public_project, secrets=())
    assert first == second
    assert before == {name: (public_project / name).read_bytes() for name in selected}
    assert not (public_project / "outputs").exists()


def test_checksum_list_verifies_every_original_entry(public_project):
    bundle = packaging.build_submission(public_project, secrets=())
    with ZipFile(BytesIO(bundle.archive_bytes)) as archive:
        lines = archive.read(packaging.PREFIX + "SHA256SUMS.txt").decode("utf-8").splitlines()
        assert len(lines) == len(archive.namelist()) - 1
        for line in lines:
            digest, name = line.split("  ", 1)
            assert sha256(archive.read(packaging.PREFIX + name)).hexdigest() == digest


@pytest.mark.parametrize("encoding", ["utf-8", "utf-16-le", "utf-16-be", "gb18030"])
def test_configured_secret_in_public_source_is_rejected_without_echo(public_project, encoding):
    secret = "configured-credential-请勿输出"
    original = secret.encode(encoding)
    (public_project / "README.md").write_bytes(original)
    with pytest.raises(packaging.SubmissionError) as error:
        packaging.build_submission(public_project, secrets=(secret,))
    assert secret not in str(error.value)
    assert "凭据" in str(error.value)
    assert (public_project / "README.md").read_bytes() == original
    assert not (public_project / "outputs").exists()


def test_environment_and_overridden_local_secret_both_checked(public_project, monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "environment-only-secret")
    (public_project / ".env").write_text('LLM_API_KEY="local-overridden-secret"\n', encoding="utf-8")
    assert set(packaging.configured_secret_values(public_project)) == {"environment-only-secret", "local-overridden-secret"}
    (public_project / "README.md").write_text("local-overridden-secret", encoding="utf-8")
    with pytest.raises(packaging.SubmissionError, match="凭据"):
        packaging.build_submission(public_project)


def test_template_requires_empty_key_even_without_a_configured_secret(public_project):
    (public_project / ".env.example").write_text("LLM_API_KEY=private-in-template\n", encoding="utf-8")
    with pytest.raises(packaging.SubmissionError) as error:
        packaging.build_submission(public_project, secrets=())
    assert "private-in-template" not in str(error.value)
    assert ".env.example" in str(error.value)


def test_modified_public_csv_is_preserved_and_not_shipped(public_project):
    path = public_project / "examples/data/first_order_step.csv"
    altered = path.read_bytes() + b"user modification\n"
    path.write_bytes(altered)
    with pytest.raises(packaging.SubmissionError, match="公开示例CSV"):
        packaging.build_submission(public_project, secrets=())
    assert path.read_bytes() == altered


@pytest.mark.parametrize("unsafe", ["../private.txt", "/private.txt", "C:/private.txt", "core/../private.txt", "core\\private.txt"])
def test_allowlist_cannot_create_traversal_member(public_project, monkeypatch, unsafe):
    monkeypatch.setattr(packaging, "REQUIRED_FILES", (unsafe,))
    with pytest.raises(packaging.SubmissionError, match="不安全路径"):
        packaging.build_submission(public_project, secrets=())


def test_redirected_source_rejected_before_read(public_project, monkeypatch):
    original_check = packaging._not_redirected
    monkeypatch.setattr(packaging, "_not_redirected", lambda path: False if path == public_project / "README.md" else original_check(path))
    with pytest.raises(packaging.SubmissionError, match="重定向"):
        packaging.build_submission(public_project, secrets=())


def test_missing_required_file_fails_instead_of_shipping_partial_project(public_project):
    (public_project / "app.py").unlink()
    with pytest.raises(packaging.SubmissionError, match="缺失"):
        packaging.build_submission(public_project, secrets=())


def test_total_size_limit_applies_while_collecting_files(public_project, monkeypatch):
    monkeypatch.setattr(packaging, "MAX_SOURCE_BYTES", 10)
    with pytest.raises(packaging.SubmissionError, match="总大小"):
        packaging.build_submission(public_project, secrets=())


def test_output_is_fixed_bounded_and_existing_file_requires_explicit_overwrite(public_project):
    bundle = packaging.build_submission(public_project, secrets=())
    output = public_project / "outputs/submission"
    output.mkdir(parents=True)
    keep = output / "用户视频.mp4"
    keep.write_bytes(b"preserve-user-video")
    paths = packaging.write_submission(bundle, public_project)
    assert all(path.parent == output for path in paths)
    assert paths[0].name == packaging.ARCHIVE_NAME
    with pytest.raises(packaging.SubmissionError, match="已存在"):
        packaging.write_submission(bundle, public_project)
    packaging.write_submission(bundle, public_project, overwrite=True)
    assert paths[0].read_bytes() == bundle.archive_bytes
    assert keep.read_bytes() == b"preserve-user-video"
    assert not list(output.glob(".submission-*.tmp"))


def test_redirected_output_directory_is_rejected(public_project, monkeypatch):
    bundle = packaging.build_submission(public_project, secrets=())
    (public_project / "outputs").mkdir()
    original_check = packaging._not_redirected
    monkeypatch.setattr(packaging, "_not_redirected", lambda path: False if path == public_project / "outputs" else original_check(path))
    with pytest.raises(packaging.SubmissionError, match="输出目录不安全"):
        packaging.write_submission(bundle, public_project)
    assert not (public_project / "outputs/submission").exists()


def test_cli_does_not_print_sensitive_os_errors(monkeypatch, capsys):
    monkeypatch.setattr(packaging.sys, "argv", ["package_submission.py"])
    def blocked():
        raise OSError("sensitive-private-path-or-secret")
    monkeypatch.setattr(packaging, "build_submission", blocked)
    assert packaging.main() == 1
    output = capsys.readouterr()
    assert "sensitive-private" not in output.out + output.err
    assert "ERROR:" in output.err
