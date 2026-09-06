"""Build the public runnable submission from an exact, reviewed file allowlist.

No directory recursion, raw experiment discovery, model requests or environment
installation is performed. Run with the project virtual environment.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from hashlib import sha256
from io import BytesIO, StringIO
import json
import os
from pathlib import Path, PurePosixPath
import stat
import sys
import tempfile
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo


ROOT = Path(__file__).resolve().parents[1]
ARCHIVE_NAME = "智调Lab-可运行源码.zip"
MANIFEST_NAME = "智调Lab-源码清单.json"
NOTICE_NAME = "智调Lab-源码说明.txt"
PREFIX = "zhitiao-lab/"
MAX_FILE_BYTES = 16 * 1024 * 1024
MAX_SOURCE_BYTES = 64 * 1024 * 1024

# Exact public files, not glob patterns. Adding private files next to these does
# not put them in a submission. Public example contents are pinned separately.
REQUIRED_FILES = tuple("""
app.py
AGENTS.md
README.md
.gitignore
.dockerignore
.env.example
.streamlit/config.toml
Dockerfile
pytest.ini
requirements.txt
requirements-dev.txt
requirements.lock.txt
core/__init__.py
core/comparison.py
core/comparison_models.py
core/event_models.py
core/events.py
core/import_models.py
core/metric_models.py
core/metrics.py
core/models.py
core/parser.py
core/performance.py
core/quality.py
core/steps.py
ui/__init__.py
ui/agent_context.py
ui/agent_panel.py
ui/charts.py
ui/comparison.py
ui/components.py
ui/evidence.py
ui/import_page.py
ui/pages.py
ui/performance.py
ui/project_panel.py
ui/project_state.py
ui/report_page.py
ui/session.py
agent/__init__.py
agent/adapters.py
agent/config.py
agent/models.py
agent/presentation.py
agent/privacy.py
agent/provider.py
agent/runner.py
agent/tools.py
agent/workflow.py
projects/__init__.py
projects/archive.py
reports/__init__.py
reports/charts.py
reports/generator.py
reports/snapshot.py
examples/__init__.py
examples/catalog.py
examples/closed_loop.py
examples/generate.py
examples/README.md
examples/CLOSED_LOOP.md
examples/data/manifest.json
examples/closed_loop_data/manifest.json
scripts/install.cmd
scripts/start.cmd
scripts/manage.py
scripts/check_environment.py
scripts/package_submission.py
docs/ACCEPTANCE.md
docs/AGENT.md
docs/COMPARISON.md
docs/CSV_IMPORT.md
docs/DATA_CONTRACT.md
docs/DEMO.md
docs/DEPLOYMENT.md
docs/EVIDENCE.md
docs/LLM_CONFIGURATION.md
docs/METRICS.md
docs/PROGRESS.md
docs/PROJECTS.md
docs/QUALITY.md
docs/REPORT_FORMAT.md
docs/RUNNABLE_SUBMISSION.md
docs/SPEC.md
docs/acceptance/stage7-summary.json
docs/acceptance/stage8-summary.json
docs/acceptance/submission-summary.json
docs/acceptance/live-ai-summary.json
docs/acceptance/live-delivery-summary.json
tests/conftest.py
tests/agent_fixtures.py
tests/test_agent_runner.py
tests/test_agent_tools.py
tests/test_agent_ui.py
tests/test_app.py
tests/test_charts.py
tests/test_closed_loop.py
tests/test_comparison.py
tests/test_delivery_scripts.py
tests/test_events.py
tests/test_evidence_review.py
tests/test_examples.py
tests/test_final_evidence.py
tests/test_final_workflow.py
tests/test_import_integration.py
tests/test_llm_provider.py
tests/test_metrics_edges.py
tests/test_metrics_numerics.py
tests/test_models.py
tests/test_parser.py
tests/test_performance_multistep.py
tests/test_performance_ui.py
tests/test_project_archive.py
tests/test_project_security.py
tests/test_project_session.py
tests/test_project_ui.py
tests/test_quality.py
tests/test_reports.py
tests/test_report_ui.py
tests/test_session.py
tests/test_stage4_regressions.py
tests/test_stage4_ui.py
tests/test_steps.py
tests/test_submission_package.py
""".split())

# Material being authored in the submission stage is included only by exact name.
# PDF/MP4 and their screenshots/frames are distributed separately, never recursed.
OPTIONAL_FILES = (
    "ui/demo_guide.py", "tests/test_demo_guide.py",
    "docs/SUBMISSION.md", "docs/SUBMISSION_CONTENT.md", "docs/SUBMISSION_SCRIPT.md",
    "scripts/build_submission_pdf.py", "scripts/build_submission_video.py",
)

PUBLIC_CSV_SHA256 = {
    "examples/data/first_order_step.csv": "b125afdefaa0910df7db2b0526ce974024af590dfda6e08857fa04118d0ffadd",
    "examples/data/irregular_sampling.csv": "06a712dae05dce4a3529c5d8fa25fa23fe9f40e1e7fc164a3a743d09266a48a7",
    "examples/data/quality_issues.csv": "863583fdec4e295c6141fbb164dfe7b8c97514b7a2a418db6d652425dc84059e",
    "examples/closed_loop_data/closed_loop_pi_a.csv": "15bccb267450ad1e8deefa94cbb7909aa8430f1b0542eff1cc20906cfdf2d1e3",
    "examples/closed_loop_data/closed_loop_pi_b.csv": "b5754fad42db05d74082fb2876cda3235fe0e13ebc70e82df881938884799bc3",
}

DELIVERY_NOTICE = """智调 Lab · 可运行源码交付

解压后进入 zhitiao-lab 文件夹，先安装 Python 3.12。
Windows：scripts\\install.cmd，然后 scripts\\start.cmd。
Linux/macOS：python3.12 -m venv .venv
  .venv/bin/python -m pip install --only-binary=:all: -r requirements.lock.txt
  .venv/bin/python -m pip check
  .venv/bin/python scripts/manage.py start
打开 http://127.0.0.1:8501/ 。Ctrl+C 停止。

测试：Windows .venv\\Scripts\\python.exe -m pytest
Linux/macOS .venv/bin/python -m pytest
安装依赖通常需要网络；安装完成后全部本地分析不需要 API Key。
AI 必须由用户自行配置和逐轮授权，无配置时明确显示“AI 未配置”。

包内仅有公开源码、测试、文档、锁定依赖清单和五份公开示例CSV。
三份示例为解析函数合成数据，两份为闭环仿真数据，均非实测。
不含虚拟环境、.env、任何真实密钥、私人实验、项目包、日志、缓存、
PDF/MP4成品；应用方案和演示视频作为独立交付文件提供。
依赖包本体不包含在ZIP中，首次安装不是离线安装。

完整操作与验证边界见 README.md、docs/RUNNABLE_SUBMISSION.md、
docs/ACCEPTANCE.md；实际测试以验收文档中的记录为准。
SHA256SUMS.txt 校验包内原始条目字节；摘要证明完整性，不认证作者。
下载项目包才能在关闭或重启后恢复，当前应用没有自动保存。
真实 API 联调、Docker 和其他系统的实机验收状态以验收文档为准。
"""


class SubmissionError(ValueError):
    """Controlled error. Never include source contents or secrets in messages."""


@dataclass(frozen=True)
class SubmissionBundle:
    archive_bytes: bytes
    manifest_bytes: bytes
    notice_bytes: bytes
    sha256: str
    file_count: int


def _safe_name(name: str) -> None:
    path = PurePosixPath(name)
    if (not name or path.is_absolute() or "\\" in name or ":" in name
            or any(part in ("", ".", "..") for part in name.split("/"))
            or any(ord(char) < 32 for char in name)):
        raise SubmissionError("公开文件清单包含不安全路径。")


def _not_redirected(path: Path) -> bool:
    attributes = getattr(path.lstat(), "st_file_attributes", 0)
    return not (path.is_symlink() or attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))


def _source_file(root: Path, name: str) -> bytes:
    _safe_name(name)
    current = root
    for part in PurePosixPath(name).parts:
        current = current / part
        if not current.exists() or not _not_redirected(current):
            raise SubmissionError("公开清单文件缺失或路径发生重定向；未生成交付包。")
    if not current.is_file() or not current.resolve().is_relative_to(root):
        raise SubmissionError("公开清单文件不是项目内普通文件。")
    with current.open("rb") as stream:
        data = stream.read(MAX_FILE_BYTES + 1)
    if len(data) > MAX_FILE_BYTES:
        raise SubmissionError("公开清单中单个文件超过打包大小上限。")
    return data


def configured_secret_values(root: Path) -> tuple[str, ...]:
    """Read both environment and local-file values only for in-memory scanning.

    Even an overridden local-file key is checked. No model is initialized and no
    value, length, fingerprint or parser exception is logged.
    """
    from dotenv import dotenv_values

    values = [os.environ.get("LLM_API_KEY", "")]
    path = root / ".env"
    if path.exists():
        if not _not_redirected(path):
            raise SubmissionError("本地配置检查失败；未生成交付包。")
        try:
            with path.open("rb") as stream:
                raw = stream.read(65537)
            if len(raw) > 65536:
                raise SubmissionError("本地配置检查失败；未生成交付包。")
            env = dotenv_values(stream=StringIO(raw.decode("utf-8-sig")), interpolate=False)
            values.append(env.get("LLM_API_KEY") or "")
        except (OSError, UnicodeError, ValueError):
            raise SubmissionError("本地配置检查失败；未生成交付包。") from None
    return tuple(dict.fromkeys(value for value in values if value))


def _reject_secrets(payload: bytes, secrets: tuple[str, ...]) -> None:
    for secret in secrets:
        # Source is normally UTF-8. Check other usual encodings to catch accidental
        # secret copies without modifying any original or delivered source bytes.
        for encoding in ("utf-8", "utf-16-le", "utf-16-be", "gb18030"):
            try:
                marker = secret.encode(encoding)
            except UnicodeError:
                continue
            if marker and marker in payload:
                raise SubmissionError("公开交付文件中发现已配置凭据；打包已停止，未输出其内容。")


def build_submission(root: Path = ROOT, *, secrets: tuple[str, ...] | None = None) -> SubmissionBundle:
    """Read each selected file once, verify it and build a deterministic ZIP."""
    root = root.resolve(strict=True)
    if secrets is None:
        secrets = configured_secret_values(root)
    files = list(REQUIRED_FILES) + list(PUBLIC_CSV_SHA256)
    files += [name for name in OPTIONAL_FILES if (root / name).exists()]
    if len(set(files)) != len(files):
        raise SubmissionError("公开交付清单有重复项。")
    entries: dict[str, bytes] = {}
    total = 0
    for name in sorted(files):
        data = _source_file(root, name)
        total += len(data)
        if total > MAX_SOURCE_BYTES:
            raise SubmissionError("公开源文件总大小超过打包上限。")
        _reject_secrets(data, secrets)
        if name in PUBLIC_CSV_SHA256 and sha256(data).hexdigest() != PUBLIC_CSV_SHA256[name]:
            raise SubmissionError("公开示例CSV与已确认摘要不一致；保留原文件，请先检查用户修改。")
        entries[name] = data
    # The distributable template must never be used as a real credential store.
    from dotenv import dotenv_values
    template = dotenv_values(stream=StringIO(entries[".env.example"].decode("utf-8-sig")), interpolate=False)
    if template.get("LLM_API_KEY"):
        raise SubmissionError(".env.example 中的密钥必须为空；未生成交付包。")
    entries["DELIVERY.txt"] = DELIVERY_NOTICE.encode("utf-8-sig")
    checksums = "".join(f"{sha256(data).hexdigest()}  {name}\n" for name, data in sorted(entries.items()))
    entries["SHA256SUMS.txt"] = checksums.encode("utf-8")
    file_info = [{"path": name, "bytes": len(data), "sha256": sha256(data).hexdigest()}
                 for name, data in sorted(entries.items())]
    buffer = BytesIO()
    with ZipFile(buffer, "w", compression=ZIP_DEFLATED, compresslevel=9) as archive:
        for name, data in sorted(entries.items()):
            entry = ZipInfo(PREFIX + name, date_time=(1980, 1, 1, 0, 0, 0))
            entry.compress_type = ZIP_DEFLATED
            entry.create_system = 3
            entry.external_attr = (stat.S_IFREG | 0o644) << 16
            archive.writestr(entry, data, compress_type=ZIP_DEFLATED, compresslevel=9)
    raw = buffer.getvalue()
    digest = sha256(raw).hexdigest()
    manifest = {
        "format": "zhitiao-lab-public-submission-v1",
        "archive": ARCHIVE_NAME, "archive_sha256": digest,
        "archive_bytes": len(raw), "file_count": len(entries),
        "public_csv_count": len(PUBLIC_CSV_SHA256),
        "integrity_notice": "SHA-256用于完整性核对，不证明作者身份或实测真实性。",
        "files": file_info,
    }
    manifest_raw = (json.dumps(manifest, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")
    return SubmissionBundle(raw, manifest_raw, DELIVERY_NOTICE.encode("utf-8-sig"), digest, len(entries))


def write_submission(bundle: SubmissionBundle, root: Path = ROOT, *, overwrite: bool = False) -> tuple[Path, ...]:
    """Write only three fixed generated filenames below root/outputs/submission."""
    root = root.resolve(strict=True)
    destination = root / "outputs" / "submission"
    current = root
    for part in ("outputs", "submission"):
        current = current / part
        if current.exists() and (not current.is_dir() or not _not_redirected(current)):
            raise SubmissionError("交付输出目录不安全；没有覆盖任何文件。")
    paths = tuple(destination / name for name in (ARCHIVE_NAME, MANIFEST_NAME, NOTICE_NAME))
    for path in paths:
        if path.exists() and (not overwrite or not path.is_file() or not _not_redirected(path)):
            raise SubmissionError("交付文件已存在；请检查后使用 --overwrite 更新本脚本生成的三个文件。")
    destination.mkdir(parents=True, exist_ok=True)
    for path, data in zip(paths, (bundle.archive_bytes, bundle.manifest_bytes, bundle.notice_bytes), strict=True):
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(dir=destination, prefix=".submission-", suffix=".tmp", delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(data)
            if overwrite:
                os.replace(temporary, path)
            else:
                # Windows rename refuses an existing target. POSIX uses a hard
                # link for the same no-overwrite guarantee; no source is moved.
                os.link(temporary, path)
                temporary.unlink()
            temporary = None
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
    return paths


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--overwrite", action="store_true", help="Replace only this script's three generated submission files.")
    args = parser.parse_args()
    try:
        bundle = build_submission()
        write_submission(bundle, overwrite=args.overwrite)
    except (SubmissionError, OSError, UnicodeError, ImportError) as error:
        message = str(error) if isinstance(error, SubmissionError) else "环境、文件读取或输出权限检查失败；未确认交付包成功。"
        print(f"ERROR: {message}", file=sys.stderr)
        return 1
    print(f"Public source ZIP created: {ARCHIVE_NAME}")
    print(f"Files: {bundle.file_count}; bytes: {len(bundle.archive_bytes)}; SHA-256: {bundle.sha256}")
    print("Output: outputs/submission/ . No model request, install, source rewrite, or deployment was performed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
