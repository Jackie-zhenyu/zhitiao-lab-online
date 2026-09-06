"""只读示例目录；返回 CSV 原始字节，由 core.parser 统一解析。"""

from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path

SOURCE_LABEL = "解析函数合成数据"
GENERATOR_ID = "zhitiao-lab.examples"
GENERATOR_VERSION = "2.0.0"
GENERATE_COMMAND = r".\.venv\Scripts\python.exe examples/generate.py"
DATA_DIR = Path(__file__).resolve().parent / "data"


@dataclass(frozen=True)
class _ExampleInfo:
    example_id: str
    title: str
    filename: str
    description: str
    quantity: str = "响应量"
    unit: str = "1"
    time_unit: str = "s"
    control_unit: str = "1"
    source_label: str = SOURCE_LABEL
    source_kind: str = "simulated"


_EXAMPLES = (
    _ExampleInfo(
        "first_order_step", "一阶解析阶跃响应", "first_order_step.csv",
        "0–6 秒，1 秒前为零基线；时间常数 0.7 秒。不是实测或实际闭环仿真。",
    ),
    _ExampleInfo(
        "irregular_sampling", "非等间隔采样", "irregular_sampling.csv",
        "固定随机种子，采样间隔轻微变化，并含一个名义间隔 4 倍的间隔。",
    ),
    _ExampleInfo(
        "quality_issues", "数据质量问题", "quality_issues.csv",
        "人为注入缺失、非数字、无穷、重复时间与时间重置，供质量检查使用。",
    ),
)


def list_examples() -> list[dict]:
    """返回独立元数据副本；没有进程级可变数据或 CSV 字节缓存。"""
    return [asdict(item) for item in _EXAMPLES]


def _manifest_digest(body: dict) -> str:
    encoded = json.dumps(
        body, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _read_manifest(directory: Path) -> dict:
    path = directory / "manifest.json"
    if not path.is_file():
        raise FileNotFoundError(f"示例清单缺失，请运行：{GENERATE_COMMAND}")
    try:
        with path.open("rb") as stream:
            raw_manifest = stream.read(1024 * 1024 + 1)
        if len(raw_manifest) > 1024 * 1024:
            raise ValueError("示例清单超过 1 MiB 上限")
        manifest = json.loads(raw_manifest.decode("utf-8"))
        body = {key: value for key, value in manifest.items() if key != "manifest_sha256"}
        if (
            manifest.get("generator_id") != GENERATOR_ID
            or manifest.get("manifest_sha256") != _manifest_digest(body)
            or not isinstance(manifest.get("examples"), list)
        ):
            raise ValueError("清单标识或内容摘要不匹配")
    except (OSError, UnicodeError, ValueError, AttributeError) as error:
        raise ValueError(f"示例清单无法验证，保留现有文件：{error}") from error
    return manifest


def load_example(example_id: str, *, max_bytes: int = 20 * 1024 * 1024) -> tuple[str, bytes, dict]:
    """有界读取已生成且摘要匹配的示例；缺失时提示命令，不自动生成。"""
    from core.parser import read_limited

    info = next((item for item in _EXAMPLES if item.example_id == example_id), None)
    if info is None:
        raise ValueError(f"未知示例 ID：{example_id}")
    path = DATA_DIR / info.filename
    if not path.is_file():
        raise FileNotFoundError(f"示例文件缺失：{info.filename}。请运行：{GENERATE_COMMAND}")
    manifest = _read_manifest(DATA_DIR)
    entries = [item for item in manifest["examples"] if isinstance(item, dict)]
    entry = next((item for item in entries if item.get("example_id") == example_id), None)
    with path.open("rb") as stream:
        payload = read_limited(stream, max_bytes)
    if (
        entry is None
        or entry.get("filename") != info.filename
        or entry.get("sha256") != hashlib.sha256(payload).hexdigest()
    ):
        raise ValueError(f"示例 {info.filename} 与生成清单不匹配；保留该文件，可另行上传检查。")
    metadata = asdict(info)
    metadata.update(
        sha256=entry["sha256"],
        row_count=entry.get("row_count"),
        generator_version=manifest.get("generator_version"),
        parameters=entry.get("parameters", {}),
    )
    return info.filename, payload, metadata
