"""有界、只读 CSV 导入；保留原始字符串和记录所在物理行。"""

import csv
import hashlib
import io
import re
from typing import BinaryIO, Iterable

import pandas as pd

from core.import_models import ParsedCSV, ParserOptions

_CHUNK_BYTES = 64 * 1024
_DELIMITERS = (",", ";", "\t")


class CSVImportError(ValueError):
    """输入无法在用户确认的格式和限制下解析。"""


def read_limited(stream: BinaryIO, max_bytes: int) -> bytes:
    """最多读取 max_bytes + 1 字节，额外 1 字节仅用于识别超限。"""
    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes < 1:
        raise ValueError("max_bytes 必须是正整数")
    chunks: list[bytes] = []
    total = 0
    while total <= max_bytes:
        request = min(_CHUNK_BYTES, max_bytes + 1 - total)
        chunk = stream.read(request)
        if not isinstance(chunk, bytes):
            raise CSVImportError("CSV 输入流必须返回 bytes；请使用二进制文件流")
        if len(chunk) > request:
            raise CSVImportError("输入流未遵守有界读取约定，已停止导入")
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise CSVImportError(f"CSV 文件超过字节上限 {max_bytes} bytes，请调整限制或缩小文件")
        chunks.append(chunk)
    return b"".join(chunks)


def _decode(raw: bytes, encoding: str) -> tuple[str, str]:
    actual = encoding
    if encoding in ("auto", "utf-8", "utf-8-sig"):
        actual = "utf-8-sig" if raw.startswith(b"\xef\xbb\xbf") else "utf-8"
    try:
        return raw.decode(actual, errors="strict"), actual
    except UnicodeError as exc:
        offset = getattr(exc, "start", 0)
        raise CSVImportError(
            f"无法按 {actual} 严格解码（字节位置 {offset}）；请手动选择正确编码。"
            "不会忽略或替换无法解码的字节。"
        ) from exc


def _reader(text: str, delimiter: str):
    return csv.reader(io.StringIO(text, newline=""), delimiter=delimiter, strict=True)


def _detect_delimiter(text: str) -> str:
    # 只检查第一条完整 CSV 记录，避免识别阶段越过配置的行数限制。
    # 表头同时支持多种解释时要求手选，而不是强行猜测。
    candidates: list[str] = []
    for delimiter in _DELIMITERS:
        try:
            header = next(_reader(text, delimiter), [])
        except csv.Error:
            continue
        if len(header) > 1:
            candidates.append(delimiter)
    if len(candidates) != 1:
        raise CSVImportError(
            "无法可靠自动识别分隔符：表头没有明确分隔符、格式异常或存在多种解释。"
            "请手动选择逗号、分号或制表符。"
        )
    return candidates[0]


def parse_csv(
    source: bytes | BinaryIO,
    filename: str,
    options: ParserOptions | None = None,
) -> ParsedCSV:
    """逐条解析记录并在第 max_rows + 1 条立即拒绝，不预建无界 DataFrame。"""
    opts = ParserOptions() if options is None else ParserOptions.model_validate(options)
    if isinstance(source, bytes):
        if len(source) > opts.max_bytes:
            raise CSVImportError(f"CSV 文件超过字节上限 {opts.max_bytes} bytes，请调整限制或缩小文件")
        raw = source
    else:
        raw = read_limited(source, opts.max_bytes)
    if not raw:
        raise CSVImportError("CSV 文件为空")
    decoded, encoding = _decode(raw, opts.encoding)
    if not decoded or not decoded.strip():
        raise CSVImportError("CSV 文件为空或只包含空白字符")
    delimiter = _detect_delimiter(decoded) if opts.delimiter == "auto" else opts.delimiter
    reader = _reader(decoded, delimiter)
    records: list[list[str]] = []
    source_lines: list[int] = []
    try:
        header = next(reader, [])
        if not header or any(not field.strip() for field in header):
            raise CSVImportError("CSV 表头包含空列名；请修正原文件或选择正确分隔符")
        if len(set(header)) != len(header):
            raise CSVImportError("CSV 表头包含重复列名，无法明确映射字段")
        while True:
            start_line = reader.line_num + 1
            row = next(reader, None)
            if row is None:
                break
            if len(records) >= opts.max_rows:
                raise CSVImportError(
                    f"CSV 数据行超过上限 {opts.max_rows}；已在第 {start_line} 个物理行停止解析"
                )
            if len(row) != len(header):
                raise CSVImportError(
                    f"CSV 第 {start_line} 个物理行起始的记录有 {len(row)} 列，"
                    f"表头有 {len(header)} 列；不会跳过空行或丢弃不齐的记录"
                )
            records.append(row)
            source_lines.append(start_line)
    except csv.Error as exc:
        raise CSVImportError(
            f"CSV 第 {reader.line_num} 个物理行附近格式错误：{exc}。"
            "请检查引号、分隔符及字段长度；未跳过问题记录。"
        ) from exc
    if not records:
        raise CSVImportError("CSV 只有表头，没有数据记录")
    return ParsedCSV(
        raw_bytes=raw,
        sha256=hashlib.sha256(raw).hexdigest(),
        filename=filename,
        frame=pd.DataFrame(records, columns=header, dtype=object),
        source_lines=source_lines,
        encoding=encoding,
        delimiter=delimiter,
        operations=[
            f"保留原始 {len(raw)} 字节，使用 SHA-256 完整内容摘要标识来源",
            f"按 {encoding} 严格解码；只移除编码对应的 BOM，不替换坏字节",
            f"CSV 分隔符为 {repr(delimiter)}；表头和字段保留原始字符串及记录顺序",
            "CSV 引号/转义按格式解码；未排序、删行、填补、平滑或插值",
            f"导入限制：{opts.max_bytes} bytes / {opts.max_rows} 数据记录",
        ],
    )


def _normalize_name(value: str) -> str:
    return re.sub(r"[\s_\-()\[\]（）]", "", value).casefold()


def recommend_mapping(columns: Iterable[str]) -> dict[str, str]:
    """仅做无歧义的列名推荐，不确认映射或推断单位、比例和编码器分辨率。"""
    aliases = {
        "time": ("time", "timestamp", "t", "time_s", "time_ms", "timestamp_ms", "timestamp_s", "时间", "时间戳", "时间秒", "时间毫秒", "时间s", "时间ms"),
        "target": ("target", "setpoint", "set_point", "reference", "ref", "desired", "目标", "目标值", "设定", "设定值", "指令值"),
        "actual": ("actual", "measured", "measurement", "feedback", "实测", "实测值", "实际值", "测量值", "反馈值"),
        "control": ("control", "control_output", "output", "u", "控制", "控制量", "控制输出"),
        "p_term": ("p_term", "p", "比例项"),
        "i_term": ("i_term", "i", "积分项"),
        "d_term": ("d_term", "d", "微分项"),
    }
    available = list(columns)
    result: dict[str, str] = {}
    for field, names in aliases.items():
        normalized = {_normalize_name(name) for name in names}
        candidates = [column for column in available if _normalize_name(column) in normalized]
        if len(candidates) == 1:
            result[field] = candidates[0]
    return result
