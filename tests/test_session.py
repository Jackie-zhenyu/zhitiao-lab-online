"""会话隔离及失效管理；输入仅为测试 CSV。"""

import pytest

from core.import_models import ParserOptions
from core.parser import CSVImportError
from ui.session import LabSession, fingerprint

RAW_A = b"time,target,actual\n0,1,0\n1,1,0.5\n"
RAW_B = b"time,target,actual\n0,1,0\n1,1,0.8\n"


def loaded(raw=RAW_A):
    state = LabSession()
    state.set_source(raw, "same.csv", {"source_kind": "unknown"})
    state.ensure_parsed(ParserOptions())
    state.confirmed_key = "confirmation"
    state.traceability = {"used_interval": [1, 2]}
    return state


def test_same_filename_different_bytes_invalidates_everything():
    state = loaded()
    first_hash = state.parsed.sha256
    state.set_source(RAW_B, "same.csv", {"source_kind": "unknown"})
    assert state.confirmed_key == ""
    assert state.parsed is None and state.traceability is None
    assert state.ensure_parsed(ParserOptions()).sha256 != first_hash


def test_same_source_keeps_confirmation_but_changed_draft_discards_it():
    state = loaded()
    original_parsed = state.parsed
    state.set_source(RAW_A, "same.csv", {"source_kind": "unknown"})
    assert state.ensure_parsed(ParserOptions()) is original_parsed
    assert state.confirmed_key == "confirmation"
    state.invalidate_if_changed("changed-mapping-or-unit-or-quality-config")
    assert state.confirmed_key == "" and state.traceability is None


def test_changed_parse_options_and_failed_parse_invalidate_confirmation():
    state = loaded()
    state.ensure_parsed(ParserOptions(encoding="utf-8"))
    assert not state.confirmed_key
    state.confirmed_key = "new-confirmation"
    with pytest.raises(CSVImportError):
        state.ensure_parsed(ParserOptions(max_rows=1))
    assert state.parsed is None and not state.confirmed_key


def test_two_sessions_do_not_share_mutable_data():
    first, second = loaded(), loaded()
    first.parsed.frame.iloc[0, 1] = "edited-only-in-test"
    first.traceability["changed"] = True
    assert second.parsed.frame.iloc[0, 1] == "1"
    assert "changed" not in second.traceability
    assert first.parsed.raw_bytes == second.parsed.raw_bytes == RAW_A


def test_clear_removes_source_and_all_derived_state():
    state = loaded()
    state.clear()
    assert state.raw_bytes is None and state.parsed is None
    assert state.traceability is None and not state.confirmed_key


def test_fingerprint_is_order_independent_and_includes_configuration():
    assert fingerprint({"time": "s", "unit": "rad"}) == fingerprint({"unit": "rad", "time": "s"})
    assert fingerprint({"time": "s"}) != fingerprint({"time": "ms"})
