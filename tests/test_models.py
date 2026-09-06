"""只验证数据契约；构造数据为测试夹具，不是实验分析结果。"""

import math

import pytest
from pydantic import ValidationError

from core.models import (
    AnalysisConfig,
    AnalysisInterval,
    AnalysisResult,
    DataSource,
    EventResult,
    Experiment,
    MetricResult,
    ResultStatus,
    SourceKind,
)


def make_source(experiment_id="fixture-exp"):
    return DataSource(
        source_id="fixture-source",
        experiment_id=experiment_id,
        kind=SourceKind.SIMULATED,
        filename="test-fixture.csv",
        sha256="a" * 64,
    )


def make_result(**overrides):
    values = {
        "experiment_id": "fixture-exp",
        "source": make_source(),
        "config": AnalysisConfig(interval=AnalysisInterval(start_s=0, end_s=10)),
        "algorithm_version": "test-contract-v1",
        "status": ResultStatus.NOT_IMPLEMENTED,
        "reason": "第一阶段尚未实现分析",
    }
    values.update(overrides)
    return AnalysisResult(**values)


def test_round_trip_preserves_traceability_and_unavailable_value():
    result = make_result(
        metrics=[
            MetricResult(
                name="settling_time",
                status=ResultStatus.NOT_COMPUTABLE,
                unit="s",
                reason="缺少参考值",
            )
        ]
    )
    restored = AnalysisResult.model_validate_json(result.model_dump_json())
    assert restored == result
    assert restored.metrics[0].value is None
    assert restored.source.kind == SourceKind.SIMULATED
    assert restored.config.interval.end_s == 10
    assert restored.algorithm_version == "test-contract-v1"


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf, True, "1.2"])
def test_metric_rejects_non_finite_and_non_numeric_values(value):
    with pytest.raises(ValidationError):
        MetricResult(name="error", status="success", value=value, unit="rad")


@pytest.mark.parametrize(
    "overrides",
    [
        {"value": None},
        {"unit": None},
        {"unit": " "},
        {"name": " "},
        {"unexpected": "typo"},
    ],
)
def test_success_requires_value_unit_name_and_known_fields(overrides):
    values = {"name": "error", "status": "success", "value": 0.0, "unit": "rad"}
    values.update(overrides)
    with pytest.raises(ValidationError):
        MetricResult(**values)


def test_zero_is_allowed_only_as_an_actual_successful_value():
    assert MetricResult(name="error", status="success", value=0, unit="rad").value == 0
    with pytest.raises(ValidationError, match="None"):
        MetricResult(name="error", status="not_computable", value=0, reason="缺少字段")


@pytest.mark.parametrize("reason", [None, " "])
def test_non_success_requires_reason(reason):
    with pytest.raises(ValidationError):
        MetricResult(name="error", status="not_computable", reason=reason)
    with pytest.raises(ValidationError):
        make_result(reason=reason)


@pytest.mark.parametrize("start,end", [(2, 1), (math.nan, 2), (1, math.inf)])
def test_interval_rejects_reversed_or_non_finite_bounds(start, end):
    with pytest.raises(ValidationError):
        AnalysisInterval(start_s=start, end_s=end)


def test_single_point_interval_is_valid():
    assert AnalysisInterval(start_s=1, end_s=1).start_s == 1


def test_source_and_result_experiment_ids_must_match():
    wrong_source = make_source("different-exp")
    with pytest.raises(ValidationError, match="experiment_id"):
        Experiment(experiment_id="fixture-exp", name="测试夹具", source=wrong_source)
    with pytest.raises(ValidationError, match="experiment_id"):
        make_result(source=wrong_source)


def test_source_requires_explicit_kind_and_valid_fingerprint():
    with pytest.raises(ValidationError):
        DataSource(source_id="fixture", experiment_id="fixture-exp")
    with pytest.raises(ValidationError):
        DataSource(source_id="fixture", experiment_id="fixture-exp", kind="unknown", sha256="bad")


def test_unknown_raw_unit_and_explicit_processing_rules_are_preserved():
    experiment = Experiment(
        experiment_id="fixture-exp",
        name="模拟测试夹具",
        source=make_source(),
        raw_fields={"time": "s", "channel_1": None},
    )
    config = AnalysisConfig(
        interval=AnalysisInterval(start_s=0, end_s=10),
        field_mapping={"time": "time", "response": "channel_1"},
        confirmed_units={"time": "s", "response": "rad"},
        processing_rules=["测试约定：不做插值"],
    )
    assert experiment.raw_fields["channel_1"] is None
    assert config.processing_rules == ["测试约定：不做插值"]
    with pytest.raises(ValidationError, match="已映射"):
        AnalysisConfig(interval=config.interval, confirmed_units={"missing": "s"})


def test_successful_event_requires_interval_and_finite_evidence():
    event = EventResult(
        name="peak_error",
        status="success",
        interval=AnalysisInterval(start_s=2, end_s=3),
        value=0.5,
        unit="rad",
    )
    assert make_result(status="success", reason=None, events=[event]).events == [event]
    with pytest.raises(ValidationError, match="发生区间"):
        EventResult(name="peak_error", status="success", value=0.5, unit="rad")
    with pytest.raises(ValidationError):
        EventResult(name="peak_error", status="success", interval=event.interval, value=math.inf, unit="rad")


def test_event_must_be_inside_analysis_interval():
    event = EventResult(
        name="peak_error",
        status="success",
        interval=AnalysisInterval(start_s=9, end_s=11),
        value=0.5,
        unit="rad",
    )
    with pytest.raises(ValidationError, match="分析区间内"):
        make_result(status="success", reason=None, events=[event])


def test_unknown_nested_fields_are_rejected():
    payload = make_result().model_dump()
    payload["config"]["silent_option"] = True
    with pytest.raises(ValidationError):
        AnalysisResult.model_validate(payload)


def test_modified_nested_instance_is_revalidated():
    result = make_result(
        status="success",
        reason=None,
        metrics=[MetricResult(name="error", status="success", value=0, unit="rad")],
    )
    # 普通对象可以被修改；显式校验和新容器接收实例时均必须重新校验。
    result.metrics[0].value = math.nan
    with pytest.raises(ValidationError):
        AnalysisResult.model_validate(result)
    with pytest.raises(ValidationError):
        make_result(status="success", reason=None, metrics=result.metrics)
