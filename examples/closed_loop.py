"""同一一阶对象上的离散 PI A/B 闭环仿真；不是实测数据。"""

import argparse
import csv
from dataclasses import dataclass
import hashlib
import io
import json
import math
from pathlib import Path


SOURCE_LABEL = "闭环仿真数据，非实测"
GENERATOR_ID = "zhitiao-lab.closed-loop"
GENERATOR_VERSION = "closed-loop-pi-v4.0"
DATA_DIR = Path(__file__).resolve().parent / "closed_loop_data"
GENERATE_COMMAND = r".\.venv\Scripts\python.exe examples/closed_loop.py"


@dataclass(frozen=True)
class _Variant:
    example_id: str
    title: str
    filename: str
    kp: float
    ki: float


_VARIANTS = (
    _Variant("closed_loop_pi_a", "闭环 PI A", "closed_loop_pi_a.csv", 0.6, 0.4),
    _Variant("closed_loop_pi_b", "闭环 PI B", "closed_loop_pi_b.csv", 2.0, 2.0),
)


def _common_conditions() -> dict:
    """每次构造独立条件，不保存可变全局数据、轨迹或字节。"""
    return {
        "plant": {"equation": "dy/dt=(-y+u)/tau", "tau_s": 1.0, "initial_actual": 0.0},
        "load": {"external_load": 0.0, "disturbance": "none"},
        "sampling": {"start_s": 0.0, "end_s": 21.0, "h_s": 0.02, "row_count": 1051},
        "target": {"step_time_s": 1.0, "before": 0.0, "after": 1.0},
        "output_limits": {"lower": -2.5, "upper": 2.5},
        "noise": {"mode": "none", "seed": 20260906, "seed_usage": "无噪声，记录种子但不调用随机数发生器。"},
        "controller_algorithm": {
            "type": "discrete positional PI",
            "initial_i_term": 0.0,
            "update_order": [
                "at t[k], read actual y[k] and target r[k]",
                "e[k]=r[k]-y[k]",
                "p_term[k]=kp*e[k]",
                "i_term[k]=i_term[k-1]+ki*h*e[k] (update before output)",
                "control[k]=clip(p_term[k]+i_term[k], lower, upper)",
                "log time,target,actual,control,p_term,i_term,source_label",
                "hold control[k] over [t[k],t[k+1]); update plant by exact ZOH",
            ],
            "anti_windup": "none; integral continues updating when output is clipped",
            "derivative": "none",
            "measurement": "actual equals plant state; no measurement delay or noise",
        },
        "plant_update": "y[k+1]=exp(-h/tau)*y[k]+(1-exp(-h/tau))*control[k]",
        "final_sample": "最后一行记录该时刻计算的控制输出；不再推进到记录终点之后。",
        "quantity": "响应量", "unit": "1", "time_unit": "s",
        "optional_units": {"control": "1", "p_term": "1", "i_term": "1"},
        "parameter_units": {"kp": "1", "ki": "1/s"},
    }


def _metadata(variant: _Variant) -> dict:
    return {
        "example_id": variant.example_id,
        "title": variant.title,
        "filename": variant.filename,
        "description": f"{SOURCE_LABEL}；相同对象和条件，仅 PI 参数不同，优劣由程序指标计算。",
        "quantity": "响应量", "unit": "1", "time_unit": "s",
        "control_unit": "1", "p_term_unit": "1", "i_term_unit": "1",
        "optional_units": {"control": "1", "p_term": "1", "i_term": "1"},
        "source_label": SOURCE_LABEL, "source_kind": "simulated",
        "parameters": {"kp": variant.kp, "ki": variant.ki},
        "output_limits": {"lower": -2.5, "upper": 2.5},
        "conditions": {
            "plant": "一阶对象 dy/dt=(-y+u)/tau；tau=1 s；初始 y=0。",
            "load": "外部负载=0，无外加扰动。",
            "sampling": "h=0.02 s；t=0..21 s；t=1 s 时目标从0阶跃到1；精确ZOH对象更新。",
            "environment": "确定性、无测量延迟、无噪声；种子20260906已记录但未调用随机数。",
            "controller": f"离散位置式PI：kp={variant.kp:g}，ki={variant.ki:g} 1/s；先更新积分再输出；无抗积分饱和。",
        },
    }


def list_closed_loop_examples() -> list[dict]:
    """目录元信息均为新副本；未加载或计算任何实验数据。"""
    return [_metadata(variant) for variant in _VARIANTS]


def _simulate(variant: _Variant, common: dict) -> bytes:
    plant, sampling = common["plant"], common["sampling"]
    limits, target_rule = common["output_limits"], common["target"]
    h = sampling["h_s"]
    decay = math.exp(-h / plant["tau_s"])
    actual = plant["initial_actual"]
    i_term = common["controller_algorithm"]["initial_i_term"]
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(["time", "target", "actual", "control", "p_term", "i_term", "source_label"])
    for index in range(sampling["row_count"]):
        time_s = sampling["start_s"] + index * h
        target = target_rule["before"] if time_s < target_rule["step_time_s"] else target_rule["after"]
        error = target - actual
        p_term = variant.kp * error
        i_term += variant.ki * h * error
        control = min(limits["upper"], max(limits["lower"], p_term + i_term))
        writer.writerow([
            f"{time_s:.8f}", *(format(value, ".17g") for value in (target, actual, control, p_term, i_term)),
            SOURCE_LABEL,
        ])
        if index + 1 < sampling["row_count"]:
            actual = decay * actual + (1.0 - decay) * control
    return output.getvalue().encode("utf-8-sig")


def _manifest_digest(body: dict) -> str:
    canonical = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _read_manifest(directory: Path) -> dict:
    path = directory / "manifest.json"
    if not path.is_file():
        raise FileNotFoundError(f"闭环示例清单缺失，请运行：{GENERATE_COMMAND}")
    try:
        with path.open("rb") as stream:
            payload = stream.read(1024 * 1024 + 1)
        if len(payload) > 1024 * 1024:
            raise ValueError("清单超过 1 MiB 上限")
        manifest = json.loads(payload.decode("utf-8"))
        body = {key: value for key, value in manifest.items() if key != "manifest_sha256"}
        if (
            manifest.get("generator_id") != GENERATOR_ID
            or manifest.get("manifest_sha256") != _manifest_digest(body)
            or not isinstance(manifest.get("examples"), list)
        ):
            raise ValueError("清单归属或正文摘要不匹配")
    except (OSError, UnicodeError, ValueError, AttributeError) as error:
        raise ValueError(f"闭环示例清单无法验证，保留现有文件：{error}") from error
    return manifest


def _build_files() -> tuple[dict[str, bytes], dict]:
    common = _common_conditions()
    files, records = {}, []
    for variant in _VARIANTS:
        payload = _simulate(variant, common)
        files[variant.filename] = payload
        records.append({
            **_metadata(variant), "row_count": common["sampling"]["row_count"],
            "sha256": hashlib.sha256(payload).hexdigest(),
        })
    manifest = {
        "generator_id": GENERATOR_ID, "generator_version": GENERATOR_VERSION,
        "source_label": SOURCE_LABEL,
        "disclaimer": "这是离散PI与一阶对象共同计算产生的闭环仿真数据，不是实测。未人工调整响应曲线或预设性能优劣。",
        "parameter_source": "教学对照用手工选定PI参数：A kp=0.6,ki=0.4；B kp=2,ki=2。其他仿真条件完全相同，未根据结果反向拟合曲线。",
        "encoding": "utf-8-sig", "delimiter": ",",
        "common_conditions": common, "examples": records,
    }
    manifest["manifest_sha256"] = _manifest_digest(manifest)
    return files, manifest


def generate_closed_loop_examples(output: str | Path = DATA_DIR) -> list[Path]:
    """验证所有冲突后写入，只覆盖清单识别且摘要未变的本生成器文件。"""
    directory = Path(output).resolve()
    files, manifest = _build_files()
    manifest_path = directory / "manifest.json"
    if manifest_path.is_symlink():
        raise FileExistsError("拒绝覆盖符号链接形式的闭环示例清单")
    old_hashes = {}
    if manifest_path.exists():
        previous = _read_manifest(directory)
        records = previous["examples"]
        if (
            len(records) != len(files)
            or any(not isinstance(item, dict) for item in records)
            or {item.get("filename") for item in records} != set(files)
        ):
            raise FileExistsError("已有清单不是本闭环生成器的完整文件集合；保留所有文件")
        old_hashes = {item["filename"]: item.get("sha256") for item in records}
    for filename in files:
        path = directory / filename
        if path.is_symlink() or (path.exists() and not path.is_file()):
            raise FileExistsError(f"拒绝覆盖非普通生成文件：{path}")
        if path.is_file():
            with path.open("rb") as stream:
                current_hash = hashlib.file_digest(stream, "sha256").hexdigest()
            if old_hashes.get(filename) != current_hash:
                raise FileExistsError(f"文件已修改或未被清单识别，拒绝覆盖：{path}")
    directory.mkdir(parents=True, exist_ok=True)
    for filename, payload in files.items():
        (directory / filename).write_bytes(payload)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    return [directory / filename for filename in files] + [manifest_path]


def load_closed_loop_example(example_id: str, *, max_bytes: int = 20 * 1024 * 1024) -> tuple[str, bytes, dict]:
    """有界读入且验证原始字节，交由统一 CSV 解析流程；不自动生成。"""
    from core.parser import read_limited

    variant = next((item for item in _VARIANTS if item.example_id == example_id), None)
    if variant is None:
        raise ValueError(f"未知闭环示例 ID：{example_id}")
    path = DATA_DIR / variant.filename
    if not path.is_file():
        raise FileNotFoundError(f"闭环示例缺失：{variant.filename}。请运行：{GENERATE_COMMAND}")
    manifest = _read_manifest(DATA_DIR)
    entry = next((item for item in manifest["examples"] if isinstance(item, dict) and item.get("example_id") == example_id), None)
    with path.open("rb") as stream:
        payload = read_limited(stream, max_bytes)
    if (
        entry is None or entry.get("filename") != variant.filename
        or entry.get("sha256") != hashlib.sha256(payload).hexdigest()
    ):
        raise ValueError(f"闭环示例 {variant.filename} 与清单不匹配；保留文件，可另行上传检查。")
    metadata = dict(entry)
    metadata["generator_version"] = manifest["generator_version"]
    metadata["common_conditions"] = manifest["common_conditions"]
    return variant.filename, payload, metadata


def main() -> None:
    parser = argparse.ArgumentParser(description="生成同一对象和条件下的PI A/B闭环仿真示例")
    parser.add_argument("--output", type=Path, default=DATA_DIR, help="输出目录，默认 examples/closed_loop_data")
    arguments = parser.parse_args()
    try:
        paths = generate_closed_loop_examples(arguments.output)
    except (OSError, ValueError) as error:
        parser.exit(1, f"生成失败：{error}\n")
    print(f"已生成2份{SOURCE_LABEL}及清单：{paths[-1].parent}")


if __name__ == "__main__":
    main()
