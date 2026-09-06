"""Prepare a separate, secret-free Streamlit Community Cloud source bundle.

Does not create a repository, publish an app or open a port. The ordinary local
configuration and earlier competition artifacts stay unchanged.
"""
from __future__ import annotations

import argparse
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
import sys
import tomllib
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scripts import package_submission as public

EXTRA_FILES = (
    "cloud_app.py", "tests/test_cloud_app.py", "tests/test_cloud_deployment.py",
    "scripts/prepare_cloud_deployment.py", "docs/STREAMLIT_CLOUD.md",
)
ARCHIVE_NAME = "智调Lab-在线部署源码.zip"


def cloud_files(root: Path = ROOT, *, secrets: tuple[str, ...] | None = None) -> dict[str, bytes]:
    """Re-use the reviewed source whitelist and credential checks."""
    root = root.resolve(strict=True)
    secrets = public.configured_secret_values(root) if secrets is None else secrets
    base = public.build_submission(root, secrets=secrets)
    with ZipFile(BytesIO(base.archive_bytes)) as archive:
        files = {item.filename.removeprefix(public.PREFIX): archive.read(item)
                 for item in archive.infolist()}
    files.pop("SHA256SUMS.txt")
    for name in EXTRA_FILES:
        raw = public._source_file(root, name)
        public._reject_secrets(raw, secrets)
        files[name] = raw
    config_name = ".streamlit/config.toml"
    original = files[config_name].decode("utf-8-sig")
    if tomllib.loads(original)["server"].get("address") != "127.0.0.1":
        raise public.SubmissionError("本地监听配置与预期不符；请先人工核对。")
    marker = 'address = "127.0.0.1"'
    if original.count(marker) != 1:
        raise public.SubmissionError("监听配置不能可靠转换；未生成部署包。")
    changed = original.replace(marker, 'address = "0.0.0.0"')
    files[config_name] = changed.encode("utf-8")
    files["requirements.txt"] = b"# Cloud uses the complete tested dependency lock.\n-r requirements.lock.txt\n"
    files["CLOUD_DEPLOYMENT.txt"] = (
        "智调 Lab · 在线部署候选（尚未部署）\n"
        "把本文件所在目录的内容上传到独立GitHub仓库，入口 cloud_app.py。\n"
        "在 Streamlit Community Cloud 的 Advanced settings 选择 Python 3.12。\n"
        "首次部署 Secrets 留空，不迁移本机 .env；先验证公开示例与报告。\n"
        "此副本的 .streamlit/config.toml 监听0.0.0.0，仅用于云容器。\n"
        "本机仍应通过 scripts/start.cmd 启动，脚本限制127.0.0.1。\n"
        "详细步骤见 docs/STREAMLIT_CLOUD.md。没有真实URL前不可填写占位地址。\n"
    ).encode("utf-8")
    for raw in files.values():
        public._reject_secrets(raw, secrets)
    files["SHA256SUMS.txt"] = ("\n".join(
        f"{sha256(raw).hexdigest()}  {name}" for name, raw in sorted(files.items())
    ) + "\n").encode("utf-8")
    return files


def write_cloud_bundle(destination: Path, root: Path = ROOT) -> dict:
    """Fail rather than overwrite an existing delivery or follow redirected paths."""
    root = root.resolve(strict=True)
    destination = Path(destination)
    if not destination.is_absolute():
        destination = root / destination
    for parent in (destination, *destination.parents):
        if parent.exists() and not public._not_redirected(parent):
            raise public.SubmissionError("输出路径发生重定向；未写入部署文件。")
    destination = destination.resolve()
    if destination == root or not destination.is_relative_to(root / "outputs"):
        raise public.SubmissionError("部署输出必须位于本项目outputs目录内。")
    if destination.exists():
        raise public.SubmissionError("部署输出目录已存在；保留已有文件，请指定新目录。")
    files = cloud_files(root)
    compressed = BytesIO()
    with ZipFile(compressed, "w", compression=ZIP_DEFLATED, compresslevel=6) as archive:
        for name, raw in sorted(files.items()):
            info = ZipInfo(public.PREFIX + name, date_time=(2026, 9, 6, 0, 0, 0))
            info.compress_type = ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, raw)
    archive_bytes = compressed.getvalue()
    with ZipFile(BytesIO(archive_bytes)) as archive:
        if archive.testzip() is not None:
            raise public.SubmissionError("部署包完整性校验失败。")
    destination.mkdir(parents=True, exist_ok=False)
    for name, raw in files.items():
        target = destination / "zhitiao-lab" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("xb") as stream:
            stream.write(raw)
    with (destination / ARCHIVE_NAME).open("xb") as stream:
        stream.write(archive_bytes)
    manifest = {
        "status": "prepared_not_deployed", "public_url": None,
        "entrypoint": "cloud_app.py", "python_version_to_select": "3.12",
        "source_files": len(files), "archive_bytes": len(archive_bytes),
        "archive_sha256": sha256(archive_bytes).hexdigest(),
        "local_configuration_modified": False, "credentials_included": False,
        "files": [{"file": name, "bytes": len(raw), "sha256": sha256(raw).hexdigest()}
                  for name, raw in sorted(files.items())],
    }
    with (destination / "准备状态.json").open("x", encoding="utf-8") as stream:
        json.dump(manifest, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("outputs/cloud-deployment"))
    args = parser.parse_args()
    try:
        result = write_cloud_bundle(args.output)
    except (public.SubmissionError, OSError):
        print("准备失败：检查输出目录是否已存在、路径权限、源文件或凭据安全检查；未覆盖已有交付。", file=sys.stderr)
        return 1
    print(json.dumps({k: v for k, v in result.items() if k != "files"}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
