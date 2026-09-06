"""生成解析函数合成 CSV；不运行控制器，也不模拟真实闭环系统。"""

import argparse
import csv
import hashlib
import io
import json
import math
from pathlib import Path
import random

if __package__:
    from .catalog import (
        DATA_DIR, GENERATOR_ID, GENERATOR_VERSION, SOURCE_LABEL,
        _manifest_digest, _read_manifest, list_examples,
    )
else:  # 支持直接执行 python examples/generate.py。
    from catalog import (
        DATA_DIR, GENERATOR_ID, GENERATOR_VERSION, SOURCE_LABEL,
        _manifest_digest, _read_manifest, list_examples,
    )


def _response_row(time_s: float) -> list[str]:
    target = 0.0 if time_s < 1.0 else 1.0
    actual = 0.0 if time_s < 1.0 else 1.0 - math.exp(-(time_s - 1.0) / 0.7)
    # control 是解析给定输入，不是 PID 输出，也没有用于求解 actual。
    return [
        f"{time_s:.9f}", f"{target:.9f}", f"{actual:.9f}",
        f"{0.4 * target:.9f}", SOURCE_LABEL,
    ]


def _csv_bytes(rows: list[list[str]]) -> bytes:
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(["time", "target", "actual", "control", "source_label"])
    writer.writerows(rows)
    return buffer.getvalue().encode("utf-8-sig")


def _build_files() -> tuple[dict[str, bytes], dict]:
    regular = [_response_row(index * 0.05) for index in range(121)]
    random_source = random.Random(20260905)
    times = [0.0]
    for index in range(1, 121):
        interval = 0.2 if index == 60 else random_source.uniform(0.045, 0.055)
        times.append(times[-1] + interval)
    irregular = [_response_row(value) for value in times]
    issues = [_response_row(index * 0.1) for index in range(61)]
    issues[13][2] = ""                 # 第 14 数据行：actual 缺失。
    issues[20][1] = "not-a-number"     # 第 21 数据行：target 非数字。
    issues[27][3] = "inf"              # 第 28 数据行：control 无穷。
    issues[33][0] = ""                 # 第 34 数据行：time 缺失。
    issues[39][0] = issues[38][0]       # 第 40 数据行：重复时间。
    for index in range(48, 61):
        issues[index][0] = f"{(index - 48) * 0.1:.9f}"  # 第 49 行重置至 0。

    datasets = {
        "first_order_step": (regular, {"start_s": 0, "end_s": 6, "interval_s": 0.05}),
        "irregular_sampling": (irregular, {
            "seed": 20260905, "interval_range_s": [0.045, 0.055],
            "nominal_interval_s": 0.05, "large_gap_s": 0.2,
            "large_gap_between_data_rows": [60, 61],
        }),
        "quality_issues": (issues, {
            "original_interval_s": 0.1,
            "injections": [
                {"data_row": 14, "field": "actual", "issue": "missing"},
                {"data_row": 21, "field": "target", "issue": "non_numeric"},
                {"data_row": 28, "field": "control", "issue": "infinity"},
                {"data_row": 34, "field": "time", "issue": "missing"},
                {"data_row": 40, "field": "time", "issue": "duplicate"},
                {"data_row": 49, "field": "time", "issue": "reset_to_zero"},
            ],
            "row_numbering": "1-based data rows, excluding header",
        }),
    }
    files = {}
    records = []
    for info in list_examples():
        rows, parameters = datasets[info["example_id"]]
        payload = _csv_bytes(rows)
        files[info["filename"]] = payload
        records.append({
            **info, "row_count": len(rows),
            "sha256": hashlib.sha256(payload).hexdigest(), "parameters": parameters,
        })
    manifest = {
        "generator_id": GENERATOR_ID,
        "generator_version": GENERATOR_VERSION,
        "source_label": SOURCE_LABEL,
        "disclaimer": "不是实测数据，也不是实际闭环仿真。control 为解析给定输入，不是控制器输出。",
        "encoding": "utf-8-sig",
        "delimiter": ",",
        "formula": {
            "step_time_s": 1.0, "time_constant_s": 0.7,
            "target": "0 if t < 1 else 1",
            "actual": "0 if t < 1 else 1-exp(-(t-1)/0.7)",
            "control": "0.4*target; given analytically, not a closed-loop simulation",
        },
        "examples": records,
    }
    manifest["manifest_sha256"] = _manifest_digest(manifest)
    return files, manifest


def generate_examples(output: str | Path = DATA_DIR) -> list[Path]:
    """先验证全部冲突；仅覆盖由未改动清单识别、摘要相符的生成文件。"""
    directory = Path(output).resolve()
    files, manifest = _build_files()
    manifest_path = directory / "manifest.json"
    expected_names = set(files)
    old_hashes = {}
    if manifest_path.is_symlink():
        raise FileExistsError("拒绝覆盖符号链接形式的示例清单")
    if manifest_path.exists():
        previous = _read_manifest(directory)
        records = previous["examples"]
        if (
            len(records) != len(expected_names)
            or any(not isinstance(item, dict) for item in records)
            or {item.get("filename") for item in records} != expected_names
        ):
            raise FileExistsError("已有清单不属于本生成器的完整文件集合；保留所有文件")
        old_hashes = {item["filename"]: item.get("sha256") for item in records}
    for filename in files:
        path = directory / filename
        if path.is_symlink() or (path.exists() and not path.is_file()):
            raise FileExistsError(f"拒绝覆盖非普通生成文件：{path}")
        if path.is_file():
            with path.open("rb") as stream:
                actual_hash = hashlib.file_digest(stream, "sha256").hexdigest()
            if old_hashes.get(filename) != actual_hash:
                raise FileExistsError(f"已有文件未被清单识别或已被修改，拒绝覆盖：{path}")
    directory.mkdir(parents=True, exist_ok=True)
    for filename, payload in files.items():
        (directory / filename).write_bytes(payload)
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    return [directory / filename for filename in files] + [manifest_path]


def main() -> None:
    parser = argparse.ArgumentParser(description="生成明确标注的解析函数合成示例 CSV")
    parser.add_argument("--output", type=Path, default=DATA_DIR, help="输出目录，默认 examples/data")
    arguments = parser.parse_args()
    try:
        paths = generate_examples(arguments.output)
    except (OSError, ValueError) as error:
        parser.exit(1, f"生成失败：{error}\n")
    print(f"已生成 {len(paths) - 1} 份{SOURCE_LABEL}及清单：{paths[-1].parent}")
    print("不是实测数据，也不是实际闭环仿真。")


if __name__ == "__main__":
    main()
