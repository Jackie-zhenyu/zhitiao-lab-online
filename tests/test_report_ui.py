"""报告页从真实本地流程生成，逐值核对页面、HTML/JSON和CSV快照。"""
import csv
import io
import json

from test_app import load_example_app,confirm_example
from test_performance_ui import confirm_step
from test_stage4_ui import save_example,compare
from reports.snapshot import metric_rows


def generate(app,scope='current'):
    app.sidebar.radio[0].set_value('报告预览').run()
    app.selectbox(key='report_scope').set_value(scope).run()
    app.button(key='generate_report').click().run()
    assert not app.exception and not app.error
    return app.session_state['report_exports']


def test_report_page_has_exactly_current_values_and_three_downloads_without_ai():
    app=confirm_step(confirm_example(load_example_app()))
    expected=app.session_state['lab_session'].performance.model_dump(mode='json')
    bundle=generate(app)
    payload=json.loads(bundle.json)
    assert payload['runs'][0]['performance']==expected
    assert 'AI 未参与' in bundle.html
    assert {d.proto.label for d in app.get('download_button')}=={'下载 HTML 报告','下载结构化 JSON','下载指标 CSV'}
    table=next(d.value for d in app.dataframe if '结果ID' in d.value.columns)
    expected_rows=metric_rows(payload)
    assert table['数值'].tolist()==[r['value'] for r in expected_rows]
    assert table['配置ID'].tolist()==[r['configuration_id'] for r in expected_rows]
    assert len(app.get('iframe'))==1
    parsed=list(csv.DictReader(io.StringIO(bundle.csv.decode('utf-8-sig'))))
    for row,expected_row in zip(parsed,expected_rows):
        assert float(row['value'])==expected_row['value']


def test_changing_configuration_keeps_old_report_frozen_and_requires_new_generation():
    app=confirm_step(confirm_example(load_example_app()))
    bundle=generate(app)
    before=bundle.snapshot.json_text
    app.sidebar.radio[0].set_value('实验分析').run()
    app.number_input(key='metric_tail').set_value(.5).run()
    changed=app.session_state['lab_session'].performance.model_dump(mode='json')
    app.sidebar.radio[0].set_value('报告预览').run()
    assert app.session_state['report_exports'].snapshot.json_text==before
    assert any('旧快照' in item.value for item in app.warning)
    app.button(key='generate_report').click().run()
    assert not app.exception and not app.error
    assert app.session_state['report_exports'].snapshot.data()['runs'][0]['performance']==changed
    assert bundle.snapshot.json_text==before


def test_report_text_drafts_survive_navigation_without_changing_export():
    app=confirm_step(confirm_example(load_example_app()))
    bundle=generate(app)
    app.text_input(key='report_project').set_value('中文实验课').run()
    app.text_area(key='report_notes').set_value('负载未知，待核对').run()
    app.sidebar.radio[0].set_value('实验分析').run()
    app.sidebar.radio[0].set_value('报告预览').run()
    assert app.text_input(key='report_project').value=='中文实验课'
    assert app.text_area(key='report_notes').value=='负载未知，待核对'
    assert app.session_state['report_exports'].snapshot==bundle.snapshot
    assert any('旧快照' in item.value for item in app.warning)
    app.button(key='generate_report').click().run()
    assert not app.exception and not app.error
    assert app.session_state['report_exports'].snapshot.data()['project']=='中文实验课'
    assert app.session_state['report_exports'].snapshot.data()['notes']=='负载未知，待核对'
    assert bundle.snapshot.data()['notes']==''


def test_ab_report_uses_actual_comparison_conditions_and_common_window():
    app=save_example()
    save_example(app,'closed_loop_pi_b')
    app.sidebar.radio[0].set_value('实验对比').run()
    expected=compare(app).model_dump(mode='json')
    bundle=generate(app,'comparison')
    assert bundle.snapshot.data()['comparison']==expected
    assert all(r['source_label']=='闭环仿真数据，非实测' for r in bundle.snapshot.data()['runs'])
    assert len(bundle.snapshot.data()['comparison_charts'])==2


def test_quality_only_ui_can_export_without_a_valid_analysis_interval():
    app=confirm_example(load_example_app('quality_issues'))
    assert app.session_state['lab_session'].performance is None
    bundle=generate(app)
    assert bundle.snapshot.data()['runs'][0]['quality']['issues']
    assert bundle.snapshot.data()['runs'][0]['performance'] is None
    assert '仅包含质量结果' in ' '.join(i.value for i in app.info)


def test_controls_are_in_sidebar_and_ai_is_separate_from_charts():
    app=confirm_example(load_example_app())
    assert app.sidebar.button(key='confirm_mapping')
    assert app.sidebar.number_input(key='metric_tail')
    assert next(s for s in app.sidebar.selectbox if s.label=='选择连续有效区间')
    assert app.tabs[0].label=='实验工作台'
    ai=next(t for t in app.tabs if t.label=='AI 分析')
    assert ai.chat_input[0].disabled and not ai.get('plotly_chart')
    assert not app.sidebar.get('plotly_chart')
