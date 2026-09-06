"""验证合成来源、重复生成与用户文件保护，不把夹具当实测数据。"""

import csv
import hashlib
import io
import json
import math

import pytest

from examples import catalog
from examples.generate import generate_examples


def _rows(payload):
    return list(csv.DictReader(io.StringIO(payload.decode("utf-8-sig"))))


def test_generation_is_deterministic_and_manifest_matches(tmp_path):
    first = tmp_path / "first"
    second = tmp_path / "second"
    generate_examples(first)
    generate_examples(second)
    original = {path.name: path.read_bytes() for path in first.iterdir()}
    generate_examples(first)
    assert original == {path.name: path.read_bytes() for path in first.iterdir()}
    assert original == {path.name: path.read_bytes() for path in second.iterdir()}
    manifest = json.loads(original["manifest.json"])
    assert "不是实测数据" in manifest["disclaimer"]
    assert "不是实际闭环仿真" in manifest["disclaimer"]
    for entry in manifest["examples"]:
        payload = original[entry["filename"]]
        assert payload.startswith(b"\xef\xbb\xbf")
        assert hashlib.sha256(payload).hexdigest() == entry["sha256"]
        rows = _rows(payload)
        assert len(rows) == entry["row_count"]
        assert all(row["source_label"] == "解析函数合成数据" for row in rows)


def test_analytic_response_includes_pre_step_baseline(tmp_path):
    generate_examples(tmp_path)
    rows = _rows((tmp_path / "first_order_step.csv").read_bytes())
    assert float(rows[0]["time"]) == 0
    assert float(rows[-1]["time"]) == 6
    baseline = [row for row in rows if float(row["time"]) < 1]
    assert len(baseline) == 20
    assert all(float(row["target"]) == float(row["actual"]) == 0 for row in baseline)
    for row in rows:
        time_s = float(row["time"])
        expected = 0 if time_s < 1 else 1 - math.exp(-(time_s - 1) / 0.7)
        assert float(row["actual"]) == pytest.approx(expected, abs=1e-9)
        assert float(row["control"]) == pytest.approx(0.4 * float(row["target"]))


def test_nonuniform_example_has_explicit_large_interval(tmp_path):
    generate_examples(tmp_path)
    rows = _rows((tmp_path / "irregular_sampling.csv").read_bytes())
    times = [float(row["time"]) for row in rows]
    intervals = [later - earlier for earlier, later in zip(times, times[1:])]
    assert all(value > 0 for value in intervals)
    assert intervals[59] == pytest.approx(0.2)
    assert len({round(value, 6) for value in intervals}) > 100
    assert all(0.044999999 <= value <= 0.055000001 for index, value in enumerate(intervals) if index != 59)


def test_quality_example_contains_documented_defects(tmp_path):
    generate_examples(tmp_path)
    rows = _rows((tmp_path / "quality_issues.csv").read_bytes())
    assert rows[13]["actual"] == ""
    with pytest.raises(ValueError):
        float(rows[20]["target"])
    assert math.isinf(float(rows[27]["control"]))
    assert rows[33]["time"] == ""
    assert rows[39]["time"] == rows[38]["time"]
    assert float(rows[48]["time"]) == 0 < float(rows[47]["time"])


def test_regeneration_rejects_user_edits_before_writing_any_file(tmp_path):
    generate_examples(tmp_path)
    changed = tmp_path / "quality_issues.csv"
    changed.write_bytes(changed.read_bytes() + b"user edit\n")
    snapshot = {path.name: path.read_bytes() for path in tmp_path.iterdir()}
    with pytest.raises(FileExistsError, match="拒绝覆盖"):
        generate_examples(tmp_path)
    assert snapshot == {path.name: path.read_bytes() for path in tmp_path.iterdir()}


def test_unowned_files_and_edited_manifest_are_protected(tmp_path):
    unowned = tmp_path / "unowned"
    unowned.mkdir()
    user_file = unowned / "first_order_step.csv"
    user_file.write_bytes(b"user file")
    with pytest.raises(FileExistsError):
        generate_examples(unowned)
    assert user_file.read_bytes() == b"user file"
    assert not (unowned / "manifest.json").exists()

    owned = tmp_path / "owned"
    generate_examples(owned)
    manifest_path = owned / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["disclaimer"] = "user edit"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    snapshot = manifest_path.read_bytes()
    with pytest.raises(ValueError, match="无法验证"):
        generate_examples(owned)
    assert manifest_path.read_bytes() == snapshot


def test_unrelated_user_file_is_preserved(tmp_path):
    path = tmp_path / "notes.txt"
    path.write_bytes(b"user notes")
    generate_examples(tmp_path)
    generate_examples(tmp_path)
    assert path.read_bytes() == b"user notes"


def test_catalog_returns_fresh_metadata_and_missing_files_do_not_generate(tmp_path, monkeypatch):
    monkeypatch.setattr(catalog, "DATA_DIR", tmp_path)
    first = catalog.list_examples()
    first[0]["title"] = "modified"
    assert catalog.list_examples()[0]["title"] != "modified"
    with pytest.raises(FileNotFoundError, match="examples/generate.py"):
        catalog.load_example("first_order_step")
    assert not list(tmp_path.iterdir())
    with pytest.raises(ValueError, match="未知"):
        catalog.load_example("unknown")


@pytest.mark.parametrize("example_id", ["first_order_step", "irregular_sampling", "quality_issues"])
def test_each_example_loads_through_shared_parser(tmp_path, monkeypatch, example_id):
    from core.parser import parse_csv

    generate_examples(tmp_path)
    monkeypatch.setattr(catalog, "DATA_DIR", tmp_path)
    filename, payload, metadata = catalog.load_example(example_id)
    parsed = parse_csv(payload, filename)
    assert len(parsed.frame) == metadata["row_count"]
    assert list(parsed.frame.columns) == ["time", "target", "actual", "control", "source_label"]
    assert parsed.raw_bytes == payload
    assert parsed.sha256 == metadata["sha256"]
    assert metadata["quantity"] == "响应量"
    assert metadata["unit"] == metadata["control_unit"] == "1"
    assert metadata["time_unit"] == "s"
    assert metadata["source_label"] == "解析函数合成数据"
    assert metadata["source_kind"] == "simulated"


def test_catalog_rejects_changed_generated_file(tmp_path, monkeypatch):
    generate_examples(tmp_path)
    monkeypatch.setattr(catalog, "DATA_DIR", tmp_path)
    path = tmp_path / "first_order_step.csv"
    path.write_bytes(path.read_bytes() + b"user edit\n")
    with pytest.raises(ValueError, match="不匹配"):
        catalog.load_example("first_order_step")


def test_catalog_respects_byte_limit(tmp_path, monkeypatch):
    from core.parser import CSVImportError

    generate_examples(tmp_path)
    monkeypatch.setattr(catalog, "DATA_DIR", tmp_path)
    with pytest.raises(CSVImportError):
        catalog.load_example("first_order_step", max_bytes=10)
