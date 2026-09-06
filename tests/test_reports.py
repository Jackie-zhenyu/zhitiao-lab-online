"""独立校验快照一致性、中文、不可用结果、离线资源与导出注入边界。"""
from copy import deepcopy
import base64
import csv
import hashlib
from html.parser import HTMLParser
import io
import json
import re

import pytest

from core.event_models import EventConfig
from core.events import detect_events
from core.comparison_models import ComparisonConfig
from core.comparison import compare_experiments
from core.performance import analyze_performance
from core.metric_models import MetricConfig
from reports.snapshot import capture_run,create_report,metric_rows,ReportSnapshot,capture_ai
from reports.generator import build_exports,render_html,export_csv,export_filename,safe_csv_cell,json_script
from test_stage4_regressions import saved_fixture,prepared_fixture
from agent_fixtures import context,ScriptedProvider,tool_reply,answer_latest
from test_agent_runner import run as agent_run
from ui.agent_panel import AgentPanelState


def run_from(saved,**kwargs):
    evidence = detect_events(saved.prepared,saved.quality,EventConfig())
    return capture_run(saved.prepared,saved.quality,saved.performance,evidence,
        name=kwargs.pop('name',saved.name),source_label='解析函数合成数据',**kwargs)


def simple_report(**kwargs):
    saved=saved_fixture([-1,0,1,2,3,4,5],name='中文实验')
    return create_report([run_from(saved)],**kwargs),saved


class Tags(HTMLParser):
    def __init__(self): super().__init__(); self.tags=[]
    def handle_starttag(self,tag,attrs): self.tags.append((tag,dict(attrs)))


def test_snapshot_exactly_preserves_values_configs_and_source_and_is_independent():
    snapshot,saved=simple_report()
    original=saved.performance.model_dump(mode='json')
    data=snapshot.data()
    assert data['runs'][0]['performance']==original
    assert data['runs'][0]['source']['sha256']==saved.prepared.parsed.sha256
    saved.performance.config.tail_fraction=.2
    saved.prepared.frame.loc[0,'actual']=99
    data['runs'][0]['performance']['config']['tail_fraction']=.9
    assert snapshot.data()['runs'][0]['performance']==original
    assert 'raw_bytes' not in snapshot.json_text


def test_chinese_no_ai_missing_metrics_and_negative_values_export_normally():
    p,q=prepared_fixture([0,1,2],[2,2,2],target=1,filename='=恶意文件名.csv')
    perf=analyze_performance(p,q,1,3,MetricConfig(),source_label='解析函数合成数据')
    captured=capture_run(p,q,perf,None,name='=HYPERLINK("bad")',source_label='解析函数合成数据')
    snapshot=create_report([captured])
    bundle=build_exports(snapshot)
    assert 'AI 未参与' in bundle.html and '单次阶跃尚未确认' in bundle.html
    csv_rows=list(csv.DictReader(io.StringIO(bundle.csv.decode('utf-8-sig'))))
    tail=next(r for r in csv_rows if r['metric_key']=='tail_bias')
    assert tail['value']=='-1.0' and tail['experiment_name'].startswith("'=")
    assert json.loads(bundle.json)['runs'][0]['performance']==perf.model_dump(mode='json')
    assert '不是按实测稳态值归一化的标准 stepinfo' in bundle.html


def test_unreached_metric_remains_null_with_reason_and_valid_csv():
    snapshot,saved=simple_report()
    rows=metric_rows(snapshot.data())
    rise=next(r for r in rows if r['metric_key']=='rise_time')
    assert rise['value'] is None and rise['status']=='not_reached' and rise['reason']
    parsed=list(csv.DictReader(io.StringIO(export_csv(snapshot).decode('utf-8-sig'))))
    assert next(r for r in parsed if r['metric_key']=='rise_time')['value']==''


def test_quality_only_report_keeps_problems_without_fake_metrics_or_charts():
    p,q=prepared_fixture([0,1,2],[0,float('nan'),1],target=1)
    snapshot=create_report([capture_run(p,q,None,None,name='缺失值记录',source_label='解析函数合成数据')])
    bundle=build_exports(snapshot)
    assert not metric_rows(snapshot.data())
    assert snapshot.data()['runs'][0]['quality']['issues']
    assert '没有有效选段曲线' in bundle.html and 'plotly-library' not in bundle.html
    assert len(bundle.csv.decode('utf-8-sig').splitlines())==1


@pytest.mark.parametrize('mutation',['mapping','quality','values','config','raw','events'])
def test_export_rejects_mixed_stale_results(mutation):
    saved=saved_fixture([-1,0,1,2,3,4,5])
    events=detect_events(saved.prepared,saved.quality,EventConfig())
    if mutation=='mapping': saved.prepared.mapping.unit='rpm'
    if mutation=='quality': saved.quality.config.max_interval_ratio=3
    if mutation=='values': saved.performance.tracking.metrics['rmse_t'].value=987
    if mutation=='config': saved.performance.config.tail_fraction=.8
    if mutation=='raw': saved.prepared.parsed.raw_bytes += b'6,1,0\n'
    if mutation=='events': events.config.oscillation_window_s=100  # 改为记录无法满足的窗口，适用状态应变化。
    with pytest.raises(ValueError):
        capture_run(saved.prepared,saved.quality,saved.performance,events,name='A',source_label='解析函数合成数据')


def test_comparison_preserves_conditions_windows_null_denominators_and_differences():
    a=saved_fixture([-1,0,1,2,3,4,5],name='A')
    b=saved_fixture([-1,0,1,2,3,4,5],name='B',shift=10)
    comparison=compare_experiments(a,b,ComparisonConfig(common_duration_s=3,confirmed=True))
    snapshot=create_report([run_from(a),run_from(b)],comparison=comparison,comparison_inputs=(a,b))
    assert snapshot.data()['comparison']==comparison.model_dump(mode='json')
    rows=metric_rows(snapshot.data())
    assert next(r for r in rows if r['metric_key']=='delta.rmse_t')['value']==0
    assert next(r for r in rows if r['metric_key']=='percent.overshoot_pct')['value'] is None
    assert next(r for r in rows if r['scope']=='A/B · B · tracking')['start_s']==10
    assert len(snapshot.data()['comparison_charts'])==2
    comparison.config.common_duration_s=2
    with pytest.raises(ValueError):
        create_report([run_from(a),run_from(b)],comparison=comparison,comparison_inputs=(a,b))


@pytest.mark.parametrize('text',['=1+1','+SUM(A1:A2)','-1+2','@SUM(A1)','  =1','\t=1','\r=1','\n@x','\ufeff=1','\x00=1'])
def test_formula_injection_is_escaped_only_for_untrusted_text(text):
    assert safe_csv_cell(text)=="'"+text
    assert safe_csv_cell(-1.25)==-1.25 and safe_csv_cell(None) is None
    assert safe_csv_cell('普通中文')=='普通中文'


def test_malicious_notes_labels_and_script_endings_cannot_create_html_nodes():
    malicious='</ScRiPt><script>document.body.dataset.attacked="yes"</script><img src=x onerror=alert(1)>\u2028'
    p,q=prepared_fixture([0,1,2],[0,0,0],target=1,unit=malicious,filename=malicious)
    perf=analyze_performance(p,q,1,3,MetricConfig(),source_label='解析函数合成数据')
    snapshot=create_report([capture_run(p,q,perf,None,name=malicious,notes=malicious,source_label='解析函数合成数据')],project=malicious,notes=malicious)
    page=render_html(snapshot)
    parser=Tags();parser.feed(page)
    scripts=[a for t,a in parser.tags if t=='script']
    assert [a['id'] for a in scripts]==['plotly-library','chart-init']
    assert not any(t=='img' or any(k.startswith('on') for k in a) for t,a in parser.tags)
    assert '\\u003c' in json_script(malicious) and '</ScRiPt>' not in json_script(malicious)
    assert '&lt;img src=x onerror=alert(1)&gt;' in page


def test_self_contained_html_has_exactly_one_plotly_bundle_and_hashed_scripts_no_network():
    snapshot,_=simple_report()
    page=render_html(snapshot)
    parser=Tags();parser.feed(page)
    assert sum(t=='script' and a.get('id')=='plotly-library' for t,a in parser.tags)==1
    assert not any('src' in a or (t=='link' and 'href' in a) for t,a in parser.tags)
    csp=next(a['content'] for t,a in parser.tags if t=='meta' and a.get('http-equiv')=='Content-Security-Policy')
    assert "connect-src 'none'" in csp and "default-src 'none'" in csp
    for script in re.findall(r'<script id="[^"]+">(.*?)</script>',page,re.S):
        digest=base64.b64encode(hashlib.sha256(script.encode()).digest()).decode()
        assert 'sha256-'+digest in csp


def test_plot_annotations_preserve_only_attribute_free_line_breaks():
    from reports.generator import _plot_safe
    value=_plot_safe({'text':'时刻<br>1 s<br onclick="alert(1)"><script>x</script>'})['text']
    assert value.startswith('时刻<br>1 s')
    assert '<br onclick' not in value and '<script>' not in value
    assert '&lt;script&gt;' in value


def test_safe_names_and_secret_redaction_and_snapshot_integrity():
    secret='TEST-SECRET-LOCAL-ONLY'
    snapshot,_=simple_report(project='../../用户路径',notes=secret+' sk-abcdefghijklmnopqrs',secrets=(secret,))
    assert secret not in snapshot.json_text and 'abcdefghijklmnopqrs' not in snapshot.json_text
    assert snapshot.data()['privacy']['redacted_text_fields']==1
    for extension in ('html','json','csv'):
        assert re.fullmatch(r'zhidiao-[a-f0-9]{12}\.'+extension,export_filename(snapshot,extension))
    with pytest.raises(ValueError): export_filename(snapshot,'../../html')
    with pytest.raises(ValueError): ReportSnapshot(snapshot.json_text+' ',snapshot.sha256).data()


def test_ai_uses_actual_refs_and_is_excluded_when_scope_or_config_changes():
    ctx=context()
    turn,ctx,history=agent_run(ScriptedProvider(tool_reply(ctx),answer_latest),ctx)
    panel=AgentPanelState(context=ctx,conversation=history)
    views=list(ctx.experiments.values())
    captured=capture_ai(panel,views)
    assert captured['status']=='included'
    ref=captured['turns'][0]['answer']['measured_facts'][0]
    assert captured['turns'][0]['evidence'][ref['result_id']]['facts'][ref['metric_key']]['value']==ctx.evidence[ref['result_id']].facts[ref['metric_key']]['value']
    changed=deepcopy(views); changed[0].metric_config.tail_fraction=.2
    assert not capture_ai(panel,changed)['turns']
    assert not capture_ai(panel,[])['turns']


def test_ai_question_is_text_and_invalid_model_text_is_not_exported():
    malicious='</script><script>document.body.dataset.attacked="yes"</script>'
    ctx=context()
    turn,ctx,history=agent_run(ScriptedProvider(tool_reply(ctx),answer_latest),ctx,question='分析实验A '+malicious)
    assert turn.status=='success'
    panel=AgentPanelState(context=ctx,conversation=history)
    ai=capture_ai(panel,list(ctx.experiments.values()))
    snapshot,_=simple_report(ai=ai)
    page=render_html(snapshot)
    assert '&lt;script&gt;document.body.dataset.attacked=' in page
    parser=Tags(); parser.feed(page)
    assert [a.get('id') for t,a in parser.tags if t=='script']==['plotly-library','chart-init']
    turn.answer=turn.answer.model_copy(update={'clarification':malicious})
    assert not capture_ai(panel,list(ctx.experiments.values()))['turns']
