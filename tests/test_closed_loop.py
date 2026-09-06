"""闭环示例的方程、可重复性和文件保护；不预设PI性能优劣。"""

import csv
import hashlib
import io
import json
import math

import numpy as np
import pytest

from examples import closed_loop


def _read_rows(path):
    return list(csv.DictReader(io.StringIO(path.read_bytes().decode("utf-8-sig"))))


def test_repeat_generation_has_identical_bytes_and_verified_manifest(tmp_path):
    first, second = tmp_path / "first", tmp_path / "second"
    closed_loop.generate_closed_loop_examples(first)
    snapshot = {path.name: path.read_bytes() for path in first.iterdir()}
    closed_loop.generate_closed_loop_examples(first)
    closed_loop.generate_closed_loop_examples(second)
    assert snapshot == {path.name: path.read_bytes() for path in first.iterdir()}
    assert snapshot == {path.name: path.read_bytes() for path in second.iterdir()}
    manifest = json.loads(snapshot["manifest.json"])
    assert manifest["source_label"] == "闭环仿真数据，非实测"
    assert manifest["common_conditions"]["noise"]["seed"] == 20260906
    assert manifest["common_conditions"]["noise"]["mode"] == "none"
    assert manifest["common_conditions"]["plant"]["equation"] == "dy/dt=(-y+u)/tau"
    assert manifest["parameter_source"]
    for item in manifest["examples"]:
        payload = snapshot[item["filename"]]
        assert payload.startswith(b"\xef\xbb\xbf")
        assert hashlib.sha256(payload).hexdigest() == item["sha256"]


def test_pair_changes_only_pi_gains_under_shared_experimental_conditions(tmp_path):
    closed_loop.generate_closed_loop_examples(tmp_path)
    first, second = closed_loop.list_closed_loop_examples()
    assert first["parameters"] == {"kp": 0.6, "ki": 0.4}
    assert second["parameters"] == {"kp": 2.0, "ki": 2.0}
    for key in ("plant", "load", "sampling", "environment"):
        assert first["conditions"][key] == second["conditions"][key]
    assert first["output_limits"] == second["output_limits"] == {"lower": -2.5, "upper": 2.5}
    assert first["conditions"]["controller"] != second["conditions"]["controller"]
    rows_a = _read_rows(tmp_path / first["filename"])
    rows_b = _read_rows(tmp_path / second["filename"])
    assert [(row["time"], row["target"]) for row in rows_a] == [(row["time"], row["target"]) for row in rows_b]
    assert [row["actual"] for row in rows_a] != [row["actual"] for row in rows_b]


@pytest.mark.parametrize("example_id,kp,ki", [("closed_loop_pi_a", 0.6, 0.4), ("closed_loop_pi_b", 2.0, 2.0)])
def test_logged_signals_satisfy_pi_and_exact_zoh_plant_equations(tmp_path, example_id, kp, ki):
    closed_loop.generate_closed_loop_examples(tmp_path)
    rows = _read_rows(tmp_path / f"{example_id}.csv")
    assert len(rows) == 1051
    assert all(row["source_label"] == "闭环仿真数据，非实测" for row in rows)
    time = np.array([float(row["time"]) for row in rows])
    target = np.array([float(row["target"]) for row in rows])
    actual = np.array([float(row["actual"]) for row in rows])
    control = np.array([float(row["control"]) for row in rows])
    proportional = np.array([float(row["p_term"]) for row in rows])
    integral = np.array([float(row["i_term"]) for row in rows])
    assert time[0] == 0.0 and time[-1] == 21.0
    np.testing.assert_allclose(np.diff(time), 0.02, rtol=0, atol=4e-15)
    np.testing.assert_array_equal(target, np.where(time < 1.0, 0.0, 1.0))
    assert np.all(actual[time < 1.0] == 0.0)
    assert np.all(control[time < 1.0] == 0.0)
    assert np.all((-2.5 <= control) & (control <= 2.5))
    error = target - actual
    np.testing.assert_allclose(proportional, kp * error, rtol=0, atol=1e-14)
    np.testing.assert_allclose(integral - np.r_[0.0, integral[:-1]], ki * 0.02 * error, rtol=0, atol=1e-14)
    np.testing.assert_allclose(control, np.clip(proportional + integral, -2.5, 2.5), rtol=0, atol=1e-14)
    decay = math.exp(-0.02)
    np.testing.assert_allclose(actual[1:], decay * actual[:-1] + (1 - decay) * control[:-1], rtol=0, atol=1e-14)
    # 独立检查阶跃当刻先积分再输出，而不是先推进对象或偷用未来反馈。
    assert actual[50] == 0.0
    assert control[50] == pytest.approx(kp + ki * 0.02, rel=0, abs=1e-15)
    assert actual[51] == pytest.approx((1 - math.exp(-0.02)) * (kp + ki * 0.02), rel=0, abs=1e-15)


def test_existing_user_csv_is_protected_before_any_write(tmp_path):
    closed_loop.generate_closed_loop_examples(tmp_path)
    changed = tmp_path / "closed_loop_pi_b.csv"
    changed.write_bytes(changed.read_bytes() + b"user edit\n")
    snapshot = {path.name: path.read_bytes() for path in tmp_path.iterdir()}
    with pytest.raises(FileExistsError, match="拒绝覆盖"):
        closed_loop.generate_closed_loop_examples(tmp_path)
    assert snapshot == {path.name: path.read_bytes() for path in tmp_path.iterdir()}


def test_output_clipping_does_not_implicitly_add_anti_windup():
    # 仅为数值规则边界夹具，不加入示例目录或人工改动A/B数据。
    variant = closed_loop._Variant("test-only", "测试夹具", "unused.csv", 100.0, 100.0)
    payload = closed_loop._simulate(variant, closed_loop._common_conditions())
    rows = list(csv.DictReader(io.StringIO(payload.decode("utf-8-sig"))))
    assert float(rows[50]["p_term"]) == 100.0
    assert float(rows[50]["i_term"]) == 2.0
    assert float(rows[50]["control"]) == 2.5
    # 初次饱和之后积分仍按正误差增加，证明没有未声明的冻结/回算策略。
    assert float(rows[51]["i_term"]) > 2.0
    assert all(-2.5 <= float(row["control"]) <= 2.5 for row in rows)


def test_unowned_file_and_modified_manifest_are_not_overwritten(tmp_path):
    unowned = tmp_path / "unowned"
    unowned.mkdir()
    user_csv = unowned / "closed_loop_pi_a.csv"
    user_csv.write_bytes(b"user content")
    with pytest.raises(FileExistsError):
        closed_loop.generate_closed_loop_examples(unowned)
    assert user_csv.read_bytes() == b"user content"
    assert not (unowned / "manifest.json").exists()
    owned = tmp_path / "owned"
    closed_loop.generate_closed_loop_examples(owned)
    path = owned / "manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest["common_conditions"]["plant"]["tau_s"] = 2.0
    path.write_text(json.dumps(manifest), encoding="utf-8")
    original = path.read_bytes()
    with pytest.raises(ValueError, match="无法验证"):
        closed_loop.generate_closed_loop_examples(owned)
    assert path.read_bytes() == original


def test_unrelated_files_are_preserved(tmp_path):
    notes = tmp_path / "notes.txt"
    notes.write_bytes(b"keep this")
    closed_loop.generate_closed_loop_examples(tmp_path)
    assert notes.read_bytes() == b"keep this"


def test_missing_files_do_not_generate_and_catalog_metadata_is_independent(tmp_path, monkeypatch):
    monkeypatch.setattr(closed_loop, "DATA_DIR", tmp_path)
    metadata = closed_loop.list_closed_loop_examples()
    metadata[0]["conditions"]["plant"] = "changed"
    metadata[0]["output_limits"]["upper"] = 999.0
    fresh = closed_loop.list_closed_loop_examples()[0]
    assert fresh["conditions"]["plant"] != "changed"
    assert fresh["output_limits"]["upper"] == 2.5
    with pytest.raises(FileNotFoundError, match="examples/closed_loop.py"):
        closed_loop.load_closed_loop_example("closed_loop_pi_a")
    assert not list(tmp_path.iterdir())
    with pytest.raises(ValueError, match="未知"):
        closed_loop.load_closed_loop_example("unknown")


@pytest.mark.parametrize("example_id", ["closed_loop_pi_a", "closed_loop_pi_b"])
def test_load_uses_shared_parser_mapping_and_quality_pipeline(tmp_path, monkeypatch, example_id):
    from core.import_models import MappingConfig
    from core.models import SourceKind
    from core.parser import parse_csv
    from core.quality import check_quality, prepare_experiment

    closed_loop.generate_closed_loop_examples(tmp_path)
    monkeypatch.setattr(closed_loop, "DATA_DIR", tmp_path)
    filename, payload, metadata = closed_loop.load_closed_loop_example(example_id)
    parsed = parse_csv(payload, filename)
    assert parsed.sha256 == metadata["sha256"]
    assert parsed.raw_bytes == payload
    assert len(parsed.frame) == metadata["row_count"] == 1051
    assert set(parsed.frame["source_label"]) == {"闭环仿真数据，非实测"}
    mapping = MappingConfig(
        fields={name: name for name in ("time", "target", "actual", "control", "p_term", "i_term")},
        quantity=metadata["quantity"], unit=metadata["unit"], time_unit=metadata["time_unit"],
        optional_units=metadata["optional_units"], confirmed=True, units_consistent=True,
    )
    prepared = prepare_experiment(parsed, mapping, SourceKind.SIMULATED)
    quality = check_quality(prepared)
    assert not quality.blocking
    assert not quality.issues
    assert [(part.start_row, part.end_row) for part in quality.valid_intervals] == [(1, 1051)]


def test_loader_enforces_size_and_checksum_without_modifying_files(tmp_path, monkeypatch):
    from core.parser import CSVImportError

    closed_loop.generate_closed_loop_examples(tmp_path)
    monkeypatch.setattr(closed_loop, "DATA_DIR", tmp_path)
    with pytest.raises(CSVImportError):
        closed_loop.load_closed_loop_example("closed_loop_pi_a", max_bytes=10)
    path = tmp_path / "closed_loop_pi_a.csv"
    path.write_bytes(path.read_bytes() + b"user edit\n")
    snapshot = path.read_bytes()
    with pytest.raises(ValueError, match="不匹配"):
        closed_loop.load_closed_loop_example("closed_loop_pi_a")
    assert path.read_bytes() == snapshot
