"""从原始 CSV 到映射/质量的集成边界回归。"""

from core.import_models import MappingConfig
from core.parser import parse_csv
from core.quality import check_quality, prepare_experiment


def test_raw_column_whitespace_is_preserved_and_never_switches_signal():
    raw = b"time,target,actual, actual\n0,1,100,0\n1,1,200,0.5\n"
    parsed = parse_csv(raw, "fixture.csv")
    mapping = MappingConfig(fields={"time": "time", "target": "target", "actual": " actual"}, time_unit="s", quantity="test", unit="1", confirmed=True, units_consistent=True)
    prepared = prepare_experiment(parsed, mapping)
    assert prepared.frame["actual"].tolist() == [0.0, 0.5]
    assert mapping.fields["actual"] == " actual"
    assert set(prepared.experiment.raw_fields) == {"time", "target", "actual", " actual"}
    assert prepared.experiment.raw_fields["actual"] is None
    assert check_quality(prepared).issues == []
