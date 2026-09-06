"""Cloud preparation must preserve computation and never pick up local secrets."""
from hashlib import sha256
from pathlib import Path
import shutil
import subprocess
import tomllib

import pytest

from scripts import package_submission as public
from scripts import prepare_cloud_deployment as cloud

ROOT = Path(__file__).resolve().parents[1]


def test_cloud_bundle_preserves_algorithms_and_complete_dependency_lock():
    before = (ROOT / ".streamlit/config.toml").read_bytes()
    files = cloud.cloud_files(ROOT, secrets=())
    assert tomllib.loads(files[".streamlit/config.toml"].decode())["server"]["address"] == "0.0.0.0"
    assert (ROOT / ".streamlit/config.toml").read_bytes() == before
    assert files["requirements.txt"].decode().splitlines()[-1] == "-r requirements.lock.txt"
    assert files["requirements.lock.txt"] == (ROOT / "requirements.lock.txt").read_bytes()
    for name, raw in files.items():
        if name.startswith(("core/", "agent/", "reports/", "projects/")):
            assert raw == (ROOT / name).read_bytes()
    assert "cloud_app.py" in files and "docs/STREAMLIT_CLOUD.md" in files
    assert ".env" not in files and ".streamlit/secrets.toml" not in files
    assert {name for name in files if name.endswith(".csv")} == set(public.PUBLIC_CSV_SHA256)
    for line in files["SHA256SUMS.txt"].decode().splitlines():
        digest, name = line.split("  ", 1)
        assert sha256(files[name]).hexdigest() == digest


def test_added_cloud_file_cannot_disclose_a_configured_secret(monkeypatch):
    original = public._source_file
    marker = "test-only-cloud-secret-marker-2c14"

    def read(root, name):
        raw = original(root, name)
        return raw + marker.encode() if name == "cloud_app.py" else raw

    monkeypatch.setattr(public, "_source_file", read)
    with pytest.raises(public.SubmissionError, match="凭据") as error:
        cloud.cloud_files(ROOT, secrets=(marker,))
    assert marker not in str(error.value)


def test_preparation_refuses_existing_output_without_changing_it(tmp_path):
    target = tmp_path / "outputs" / "already-here"
    target.mkdir(parents=True)
    existing = target / "user.txt"
    existing.write_text("preserve")
    with pytest.raises(public.SubmissionError, match="已存在"):
        cloud.write_cloud_bundle(target, tmp_path)
    assert existing.read_text() == "preserve"


def test_preparation_refuses_output_outside_project_outputs(tmp_path):
    with pytest.raises(public.SubmissionError, match="outputs"):
        cloud.write_cloud_bundle(tmp_path / "unrelated", tmp_path)
    assert not (tmp_path / "unrelated").exists()


def test_cloud_git_upload_preserves_crlf_bytes_even_with_autocrlf_enabled(tmp_path):
    git = shutil.which("git")
    if not git:
        pytest.skip("Git is needed to check checkout byte preservation")
    files = cloud.cloud_files(ROOT, secrets=())
    payload = b"verified original\r\nsecond line\r\n"
    (tmp_path / ".gitattributes").write_bytes(files[".gitattributes"])
    (tmp_path / "sample.txt").write_bytes(payload)
    subprocess.run([git, "init", "--quiet"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run([git, "-c", "core.autocrlf=true", "add", "--", ".gitattributes", "sample.txt"],
                   cwd=tmp_path, check=True, capture_output=True)
    staged = subprocess.run([git, "show", ":sample.txt"], cwd=tmp_path, check=True, capture_output=True)
    assert staged.stdout == payload
