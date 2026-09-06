"""解析函数/文本测试夹具，不代表实测或闭环仿真。"""

import hashlib
import math

import numpy as np
import pandas as pd
import pytest
from pydantic import ValidationError

from core.import_models import MappingConfig, ParsedCSV, QualityConfig
from core.models import SourceKind
from core.quality import check_quality, prepare_experiment, select_interval


def parsed_fixture(times=(0, 1, 2, 3), actual=None, target=None, **extra):
    count = len(times)
    values = {
        "t": times,
        "reference": [1] * count if target is None else target,
        "feedback": [0.5] * count if actual is None else actual,
        **extra,
    }
    frame = pd.DataFrame(values).map(lambda value: "" if value is None else str(value))
    raw = frame.to_csv(index=False).encode("utf-8")
    return ParsedCSV(
        raw_bytes=raw, sha256=hashlib.sha256(raw).hexdigest(),
        filename="fixture.csv", frame=frame,
        source_lines=list(range(2, count + 2)), encoding="utf-8", delimiter=",",
        operations=["按 CSV 记录保留原始字段字符串。"],
    )


def mapping_fixture(**overrides):
    values = dict(
        fields={"time": "t", "target": "reference", "actual": "feedback"},
        time_unit="s", quantity="角速度", unit="rad/s",
        confirmed=True, units_consistent=True,
    )
    values.update(overrides)
    return MappingConfig(**values)


def prepared_fixture(**values):
    return prepare_experiment(parsed_fixture(**values), mapping_fixture())


def codes(report):
    return {issue.code for issue in report.issues}


def bounds(report):
    return [(part.start_row, part.end_row) for part in report.valid_intervals]


def test_normal_mapping_quality_and_error_are_computed_without_modifying_raw():
    parsed = parsed_fixture(actual=[0, 0.3, 0.7, 0.9])
    original = parsed.frame.copy(deep=True)
    original_bytes = parsed.raw_bytes
    prepared = prepare_experiment(parsed, mapping_fixture())
    report = check_quality(prepared)
    assert report.row_count == 4
    assert report.duration_s == 3
    assert report.interval_stats == dict(min_s=1, median_s=1, max_s=1, mean_s=1, std_s=0, count=3)
    assert not report.blocking and not report.issues
    assert bounds(report) == [(1, 4)]
    np.testing.assert_allclose(prepared.frame["error"], [1, 0.7, 0.3, 0.1])
    pd.testing.assert_frame_equal(parsed.frame, original)
    assert parsed.raw_bytes == original_bytes


def test_milliseconds_conversion_and_raw_unit_metadata_are_explicit():
    parsed = parsed_fixture(times=[500, 600, 700], control=[0, 0.5, 0.8])
    mapping = mapping_fixture(
        fields={"time": "t", "target": "reference", "actual": "feedback", "control": "control"},
        time_unit="ms", optional_units={"control": "V"},
    )
    prepared = prepare_experiment(parsed, mapping, SourceKind.SIMULATED)
    np.testing.assert_allclose(prepared.frame["time"], [0.5, 0.6, 0.7])
    assert prepared.experiment.raw_fields == {"t": "ms", "reference": "rad/s", "feedback": "rad/s", "control": "V"}
    assert any("除以 1000" in operation for operation in prepared.operations)
    assert any("target - actual" in operation for operation in prepared.operations)
    assert any('"control": "V"' in operation for operation in prepared.operations)
    assert prepared.experiment.source.kind is SourceKind.SIMULATED
    assert parsed.frame["t"].tolist() == ["500", "600", "700"]


@pytest.mark.parametrize("token", ["", " ", "NA", "n/a", "NaN", "NULL", "none", "<NA>", None])
def test_missing_values_are_blocking_and_do_not_bridge_rows(token):
    prepared = prepared_fixture(times=[0, 1, 2, 3, 4], actual=[1, 1, token, 1, 1])
    report = check_quality(prepared)
    assert report.blocking and codes(report) == {"missing_value"}
    assert report.issues[0].row == 3 and report.issues[0].csv_line == 4
    assert report.issues[0].field == "actual"
    assert bounds(report) == [(1, 2), (4, 5)]
    assert math.isnan(prepared.frame.loc[2, "actual"])


@pytest.mark.parametrize("token", ["fault", "1,2", "--1"])
def test_non_numeric_is_distinguished_from_missing(token):
    report = check_quality(prepared_fixture(actual=[0, token, 0, 0]))
    assert codes(report) == {"non_numeric"}
    assert report.issues[0].row == 2


@pytest.mark.parametrize("token", ["inf", "-Infinity", "1e400"])
def test_infinite_values_and_numeric_overflow_are_explicit(token):
    report = check_quality(prepared_fixture(actual=[0, token, 0, 0]))
    assert codes(report) == {"infinite_value"}
    assert bounds(report) == [(3, 4)]


@pytest.mark.parametrize("field", ["control", "p_term", "i_term", "d_term"])
def test_optional_fields_also_participate_in_quality_checks(field):
    parsed = parsed_fixture(**{field: [1, "bad", 1, 1]})
    mapping = mapping_fixture(
        fields={"time": "t", "target": "reference", "actual": "feedback", field: field},
        optional_units={field: "%"},
    )
    report = check_quality(prepare_experiment(parsed, mapping))
    assert report.issues[0].field == field
    assert bounds(report) == [(3, 4)]


def test_unmapped_metadata_column_is_preserved_and_not_treated_as_a_signal():
    parsed = parsed_fixture(note=["启动", "正常", "", "停止"])
    prepared = prepare_experiment(parsed, mapping_fixture())
    assert not check_quality(prepared).blocking
    assert prepared.experiment.raw_fields["note"] is None
    assert "note" not in prepared.frame.columns
    assert parsed.frame["note"].tolist() == ["启动", "正常", "", "停止"]


def test_missing_required_mapping_is_rejected():
    with pytest.raises(ValidationError, match="必须映射"):
        mapping_fixture(fields={"time": "t", "target": "reference"})


def test_mapping_to_nonexistent_column_is_rejected():
    with pytest.raises(ValueError, match="不存在"):
        prepare_experiment(parsed_fixture(), mapping_fixture(fields={"time": "t", "target": "reference", "actual": "wrong"}))


def test_mapping_requires_confirmation_and_optional_unit():
    with pytest.raises(ValidationError):
        mapping_fixture(confirmed=False)
    with pytest.raises(ValidationError):
        mapping_fixture(units_consistent=False)
    with pytest.raises(ValidationError, match="单位"):
        mapping_fixture(fields={"time": "t", "target": "reference", "actual": "feedback", "control": "ctrl"})


def test_duplicate_timestamp_is_a_boundary_without_deleting_rows():
    prepared = prepared_fixture(times=[0, 1, 1, 2, 3])
    report = check_quality(prepared)
    assert codes(report) == {"duplicate_time"}
    assert report.duration_s == 3
    assert bounds(report) == [(1, 2), (3, 5)]
    assert len(prepared.frame) == 5
    assert report.issues[0].row == 3
    assert "数据行 2" in report.issues[0].message


def test_time_reversal_without_reset_is_reported_and_duration_unavailable():
    prepared = prepared_fixture(times=[0, 1, 2, 1.5, 2.5])
    report = check_quality(prepared)
    assert codes(report) == {"time_reversal"}
    assert report.duration_s is None
    assert bounds(report) == [(1, 3), (4, 5)]


def test_reset_nonadjacent_duplicates_still_allow_each_monotonic_segment():
    prepared = prepared_fixture(times=[0, 1, 2, 0, 1, 2])
    report = check_quality(prepared)
    assert codes(report) == {"time_reversal", "time_reset", "duplicate_time"}
    assert report.duration_s is None
    assert bounds(report) == [(1, 3), (4, 6)]
    reset = [issue for issue in report.issues if issue.code == "time_reset"][0]
    assert reset.row == 4 and "启发式" in reset.message
    np.testing.assert_array_equal(select_interval(prepared, report, 4, 6)["time"], [0, 1, 2])


def test_missing_time_never_creates_a_cross_gap_sampling_interval():
    report = check_quality(prepared_fixture(times=[0, 1, "", 50, 51]))
    assert report.duration_s is None
    assert report.interval_stats["count"] == 2
    assert report.interval_stats["max_s"] == 1
    assert bounds(report) == [(1, 2), (4, 5)]


def test_irregular_sampling_threshold_can_be_changed_explicitly():
    prepared = prepared_fixture(times=[0, 1, 2, 10, 11])
    strict = check_quality(prepared)
    assert codes(strict) == {"interval_anomaly"}
    assert bounds(strict) == [(1, 3), (4, 5)]
    assert strict.interval_stats["median_s"] == 1
    assert strict.interval_stats["max_s"] == 8
    assert strict.issues[0].row == 4
    assert "不等于设备故障" in strict.issues[0].message
    relaxed = check_quality(prepared, QualityConfig(max_interval_ratio=8))
    assert not relaxed.blocking
    assert bounds(relaxed) == [(1, 5)]


def test_submedian_short_interval_is_also_a_boundary():
    report = check_quality(prepared_fixture(times=[0, 1, 1.1, 2.1, 3.1]))
    assert "interval_anomaly" in codes(report)
    assert bounds(report) == [(1, 2), (3, 5)]


@pytest.mark.parametrize("start,end", [(1, 5), (2, 4), (0, 2), (4, 6), (1, 1), (5, 4), (True, 2)])
def test_selection_rejects_crossing_missing_data_invalid_bounds_and_single_points(start, end):
    prepared = prepared_fixture(times=[0, 1, 2, 3, 4], actual=[0, 0, "", 0, 0])
    with pytest.raises(ValueError):
        select_interval(prepared, check_quality(prepared), start, end)


def test_selection_returns_independent_copy_of_contiguous_original_rows():
    prepared = prepared_fixture(times=[0, 1, 2, 3, 4])
    report = check_quality(prepared)
    selected = select_interval(prepared, report, 2, 4)
    assert selected.index.tolist() == [1, 2, 3]
    assert selected["time"].tolist() == [1, 2, 3]
    selected.loc[1, "actual"] = -200
    assert prepared.frame.loc[1, "actual"] == 0.5


def test_report_is_bound_to_experiment_identity_even_for_same_number_of_rows():
    original = prepared_fixture(times=[0, 1, 2, 3])
    changed = prepared_fixture(times=[0, 1, 20, 21])
    report = check_quality(original)
    assert report.experiment_id == original.experiment.experiment_id
    assert len(original.frame) == len(changed.frame)
    with pytest.raises(ValueError, match="不匹配"):
        select_interval(changed, report, 1, 4)


def test_report_cannot_be_reused_after_mapping_unit_changes():
    parsed = parsed_fixture()
    original = prepare_experiment(parsed, mapping_fixture())
    changed = prepare_experiment(parsed, mapping_fixture(time_unit="ms"))
    with pytest.raises(ValueError, match="不匹配"):
        select_interval(changed, check_quality(original), 1, 4)


@pytest.mark.parametrize("times", [[0, 1, 20, 21], [0, 1, 1.1, 3]])
def test_selection_rechecks_interval_threshold_when_prepared_times_are_mutated(times):
    prepared = prepared_fixture(times=[0, 1, 2, 3])
    report = check_quality(prepared)
    prepared.frame["time"] = times
    with pytest.raises(ValueError, match="采样间隔已超出"):
        select_interval(prepared, report, 1, 4)


def test_same_filename_changed_bytes_is_new_source_and_experiment():
    first = prepared_fixture(actual=[0, 1, 2, 3])
    second = prepared_fixture(actual=[0, 1, 2, 4])
    assert first.parsed.filename == second.parsed.filename
    assert first.experiment.source.source_id != second.experiment.source.source_id
    assert first.experiment.experiment_id != second.experiment.experiment_id


def test_mapping_unit_changes_change_experiment_identity_but_preserve_source_identity():
    parsed = parsed_fixture()
    first = prepare_experiment(parsed, mapping_fixture())
    repeat = prepare_experiment(parsed, mapping_fixture())
    changed = prepare_experiment(parsed, mapping_fixture(time_unit="ms"))
    changed_unit = prepare_experiment(parsed, mapping_fixture(unit="pulse/s"))
    assert first.experiment.experiment_id == repeat.experiment.experiment_id
    assert len({first.experiment.experiment_id, changed.experiment.experiment_id, changed_unit.experiment.experiment_id}) == 3
    assert first.experiment.source.source_id == changed.experiment.source.source_id


def test_mismatched_digest_is_rejected():
    parsed = parsed_fixture()
    parsed.sha256 = "0" * 64
    with pytest.raises(ValueError, match="摘要"):
        prepare_experiment(parsed, mapping_fixture())


def test_error_overflow_is_blocking_even_when_both_inputs_are_finite():
    report = check_quality(prepared_fixture(target=[1e308, 1, 1, 1], actual=[-1e308, 1, 1, 1]))
    assert codes(report) == {"error_overflow"}
    assert bounds(report) == [(2, 4)]


def test_no_positive_interval_reports_none_statistics_and_no_valid_interval():
    report = check_quality(prepared_fixture(times=[1, 1, 1]))
    assert report.interval_stats == dict(min_s=None, median_s=None, max_s=None, mean_s=None, std_s=None, count=0)
    assert report.valid_intervals == [] and report.blocking


def test_single_point_is_explicitly_insufficient():
    report = check_quality(prepared_fixture(times=[1]))
    assert report.duration_s == 0
    assert codes(report) == {"insufficient_data"}
    assert report.valid_intervals == []


def test_large_finite_intervals_do_not_overflow_statistics():
    report = check_quality(prepared_fixture(times=[-1e308, 0, 1e308]))
    assert report.interval_stats["median_s"] == 1e308
    assert report.interval_stats["mean_s"] == 1e308
    assert report.interval_stats["std_s"] == 0
    assert report.duration_s is None
    assert "duration_overflow" in codes(report)


def test_csv_physical_line_mapping_is_preserved_in_issue():
    parsed = parsed_fixture(actual=[0, "bad", 0, 0])
    parsed.source_lines = [2, 5, 6, 7]
    report = check_quality(prepare_experiment(parsed, mapping_fixture()))
    assert report.issues[0].row == 2
    assert report.issues[0].csv_line == 5


@pytest.mark.parametrize("options", [dict(min_interval_ratio=0), dict(min_interval_ratio=1), dict(max_interval_ratio=1), dict(max_interval_ratio=float("inf"))])
def test_invalid_quality_configuration_is_rejected(options):
    with pytest.raises(ValidationError):
        QualityConfig(**options)
