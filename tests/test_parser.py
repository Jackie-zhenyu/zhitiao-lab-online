"""CSV 读取上限、格式保留和来源标识的回归检查。"""

import csv
import hashlib
import io

import pytest

from core.import_models import MappingConfig, ParserOptions
from core.parser import CSVImportError, parse_csv, read_limited, recommend_mapping


NORMAL = b"time,target,actual\n0,1,0\n1,1,0.5\n"


def test_normal_import_preserves_original_values_and_bytes():
    raw = b" time,target,actual\r\n 0 ,01,\r\n1,1,0.50\r\n"
    result = parse_csv(raw, "experiment.csv")
    assert result.raw_bytes == raw
    assert result.sha256 == hashlib.sha256(raw).hexdigest()
    assert result.filename == "experiment.csv"
    assert result.frame.columns.tolist() == [" time", "target", "actual"]
    assert result.frame.values.tolist() == [[" 0 ", "01", ""], ["1", "1", "0.50"]]
    assert result.source_lines == [2, 3]
    assert result.encoding == "utf-8"
    assert result.delimiter == ","
    assert result.operations


@pytest.mark.parametrize("encoding", ["utf-8", "utf-8-sig", "gb18030", "utf-16", "cp1252"])
def test_supported_explicit_encodings(encoding):
    text = "time,target,actual\n0,1,0\n"
    result = parse_csv(text.encode(encoding), "file.csv", ParserOptions(encoding=encoding))
    assert result.frame.columns.tolist() == ["time", "target", "actual"]
    assert result.frame.iloc[0].tolist() == ["0", "1", "0"]


@pytest.mark.parametrize("encoding", ["utf-8", "utf-8-sig"])
def test_utf8_chinese_and_bom_are_automatic(encoding):
    raw = "时间,目标值,实测值\n0,1,0\n".encode(encoding)
    result = parse_csv(raw, "中文.csv")
    assert result.encoding == encoding
    assert result.frame.columns.tolist() == ["时间", "目标值", "实测值"]


def test_manual_gb18030_and_invalid_automatic_encoding():
    raw = "时间,目标值,实测值\n0,1,0\n".encode("gb18030")
    with pytest.raises(CSVImportError, match="手动选择正确编码"):
        parse_csv(raw, "中文.csv")
    parsed = parse_csv(raw, "中文.csv", ParserOptions(encoding="gb18030"))
    assert parsed.frame.columns[0] == "时间"
    assert parsed.raw_bytes == raw


@pytest.mark.parametrize("encoding", ["auto", "utf-8", "utf-8-sig"])
def test_corrupt_utf8_is_not_replaced(encoding):
    with pytest.raises(CSVImportError, match="不会忽略或替换"):
        parse_csv(NORMAL + b"2,1,\xff\n", "bad.csv", ParserOptions(encoding=encoding))


@pytest.mark.parametrize("delimiter", [",", ";", "\t"])
@pytest.mark.parametrize("automatic", [True, False])
def test_supported_delimiters(delimiter, automatic):
    raw = (f"time{delimiter}target{delimiter}actual\n0{delimiter}1{delimiter}0\n").encode()
    result = parse_csv(raw, "data.csv", ParserOptions(delimiter="auto" if automatic else delimiter))
    assert result.delimiter == delimiter
    assert result.frame.shape == (1, 3)


def test_ambiguous_delimiter_requires_manual_selection():
    raw = b"time,target;name,actual\n0,1,0\n"
    with pytest.raises(CSVImportError, match="手动选择"):
        parse_csv(raw, "ambiguous.csv")
    result = parse_csv(raw, "ambiguous.csv", ParserOptions(delimiter=","))
    assert result.frame.columns.tolist() == ["time", "target;name", "actual"]


def test_quoted_delimiter_does_not_confuse_automatic_detection():
    raw = b'"time","target;name","actual"\n0,1,0\n'
    result = parse_csv(raw, "quoted.csv")
    assert result.delimiter == ","
    assert result.frame.columns[1] == "target;name"


def test_multiline_csv_records_report_starting_physical_lines():
    raw = b'time,target,actual,note\r\n0,1,0,"first\r\nsecond"\r\n1,1,0.5,"last"\r\n'
    result = parse_csv(raw, "multiline.csv")
    assert result.source_lines == [2, 4]
    assert result.frame.iloc[0]["note"] == "first\r\nsecond"


@pytest.mark.parametrize("raw, message", [
    (b"", "为空"),
    (b"\xef\xbb\xbf", "为空"),
    (b" \r\n\t", "空白"),
    (b"time,target,actual\n", "只有表头"),
    (b"time,target,time\n0,1,0\n", "重复列名"),
    (b"time, ,actual\n0,1,0\n", "空列名"),
    (b"time,target,actual\n0,1\n", "第 2 个物理行"),
    (b"time,target,actual\n0,1,0,extra\n", "有 4 列"),
    (b"time,target,actual\n0,1,0\n\n1,1,1\n", "第 3 个物理行"),
    (b'time,target,actual\n0,1,"unterminated\n', "格式错误"),
])
def test_bad_structure_has_explicit_error(raw, message):
    with pytest.raises(CSVImportError, match=message):
        parse_csv(raw, "bad.csv")


def test_parser_does_not_reject_numeric_quality_problems_or_mutate_them():
    raw = b"time,target,actual\n2,NaN,inf\n1,,oops\n1,1,-inf\n"
    result = parse_csv(raw, "quality.csv")
    assert result.frame.values.tolist() == [["2", "NaN", "inf"], ["1", "", "oops"], ["1", "1", "-inf"]]


def test_missing_required_field_is_detected_at_explicit_mapping():
    result = parse_csv(b"time,target\n0,1\n", "missing.csv")
    recommendation = recommend_mapping(result.frame.columns)
    assert recommendation == {"time": "time", "target": "target"}
    with pytest.raises(ValueError, match="必须映射"):
        MappingConfig(fields=recommendation, time_unit="s", quantity="速度", unit="rpm", confirmed=True, units_consistent=True)


@pytest.mark.parametrize("stream", [False, True])
def test_file_size_boundary(stream):
    source = io.BytesIO(NORMAL) if stream else NORMAL
    assert len(parse_csv(source, "boundary.csv", ParserOptions(max_bytes=len(NORMAL))).frame) == 2
    source = io.BytesIO(NORMAL) if stream else NORMAL
    with pytest.raises(CSVImportError, match="字节上限"):
        parse_csv(source, "boundary.csv", ParserOptions(max_bytes=len(NORMAL) - 1))


class TrackingStream(io.BytesIO):
    def __init__(self, data):
        super().__init__(data)
        self.requests = []

    def read(self, size=-1):
        assert size >= 0, "must not use unbounded read"
        self.requests.append(size)
        return super().read(size)


def test_stream_never_reads_beyond_limit_plus_one_or_unbounded():
    stream = TrackingStream(b"x" * 300000)
    with pytest.raises(CSVImportError, match="字节上限"):
        read_limited(stream, 100000)
    assert stream.tell() == 100001
    assert max(stream.requests) <= 64 * 1024
    assert sum(stream.requests) == 100001


def test_stream_short_reads_are_supported():
    class ShortStream(TrackingStream):
        def read(self, size=-1):
            return super().read(min(3, size))

    stream = ShortStream(NORMAL)
    assert parse_csv(stream, "short.csv").raw_bytes == NORMAL


def test_stream_requires_binary_data():
    with pytest.raises(CSVImportError, match="二进制"):
        read_limited(io.StringIO("time,target,actual"), 100)


def test_row_limit_accepts_boundary_and_rejects_next_record():
    assert len(parse_csv(NORMAL, "rows.csv", ParserOptions(max_rows=2)).frame) == 2
    with pytest.raises(CSVImportError, match="数据行超过上限 1"):
        parse_csv(NORMAL, "rows.csv", ParserOptions(max_rows=1))


def test_row_limit_stops_before_later_malformed_record():
    raw = NORMAL + b'2,1,"unterminated\n'
    with pytest.raises(CSVImportError, match="数据行超过上限 1"):
        parse_csv(raw, "rows.csv", ParserOptions(max_rows=1))


def test_multiline_field_counts_as_one_data_record():
    raw = b'time,target,actual,note\n0,1,0,"first\nsecond"\n'
    assert len(parse_csv(raw, "multiline.csv", ParserOptions(max_rows=1)).frame) == 1


def test_same_name_different_content_has_different_fingerprint():
    first = parse_csv(NORMAL, "experiment.csv")
    second = parse_csv(NORMAL.replace(b"0.5", b"0.6"), "experiment.csv")
    assert first.filename == second.filename
    assert first.sha256 != second.sha256
    assert len(first.sha256) == 64
    assert parse_csv(NORMAL, "other-name.csv").sha256 == first.sha256


def test_field_size_limit_not_changed_globally():
    original = csv.field_size_limit()
    parse_csv(NORMAL, "normal.csv")
    raw = b"time,target,actual\n0,1," + b"x" * (original + 1) + b"\n"
    with pytest.raises(CSVImportError, match="字段长度"):
        parse_csv(raw, "long.csv")
    assert csv.field_size_limit() == original


def test_recommendations_are_aliases_only_and_preserve_original_column_names():
    result = recommend_mapping(["Time (ms)", "目标值", "Actual", "control_output", "P-Term", "积分项", "d_term", "note"])
    assert result == {"time": "Time (ms)", "target": "目标值", "actual": "Actual", "control": "control_output", "p_term": "P-Term", "i_term": "积分项", "d_term": "d_term"}


def test_ambiguous_recommendations_are_left_to_the_user():
    assert "target" not in recommend_mapping(["time", "target", "reference", "actual"])


def test_default_limits_are_documented_values():
    options = ParserOptions()
    assert options.max_bytes == 20 * 1024 * 1024
    assert options.max_rows == 200000
