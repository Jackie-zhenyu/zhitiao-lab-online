"""自包含HTML、严格JSON与防公式注入的指标CSV；全部来自同一冻结快照。"""

import base64
import csv
from dataclasses import dataclass
import hashlib
import html
import io
import json
import re

from plotly.offline import get_plotlyjs
from agent.presentation import HYPOTHESES, VERIFICATIONS, LIMITATIONS
from reports.snapshot import ReportSnapshot, metric_rows


STATUS = {"success":"计算成功","not_applicable":"不适用","not_reached":"观测内未达到","insufficient_data":"数据不足","invalid_data":"数据异常","not_computable":"无法计算","not_implemented":"尚未实现","error":"错误"}
CSV_FIELDS = ["report_id","result_id","experiment_id","experiment_name","scope","metric_key","name","value","unit","status","reason","start_s","end_s","configuration_id","config_json","definition"]


def esc(value):
    if value is None: return "未提供 / 不可用"
    return html.escape(str(value),quote=True)


def json_script(value):
    # HTML解析先于JavaScript字符串解析；必须消除任何大小写的 </script> 起点。
    return json.dumps(value,ensure_ascii=False,allow_nan=False).replace("<","\\u003c").replace(">","\\u003e").replace("&","\\u0026").replace("\u2028","\\u2028").replace("\u2029","\\u2029")


def _plot_safe(value, key=""):
    if isinstance(value,dict): return {k:_plot_safe(v,k) for k,v in value.items()}
    if isinstance(value,list): return [_plot_safe(v,key) for v in value]
    if isinstance(value,str) and key in {"text","name","hovertext","texttemplate"}:
        # 仅保留既有图表的无属性换行标签；其他输入仍全部转义。
        return html.escape(value).replace('&lt;br&gt;','<br>')
    return value


def safe_csv_cell(value):
    if not isinstance(value,str): return value  # 数值类型的合法负数必须保持数值。
    trimmed = value.lstrip(" \t\r\n\v\f\ufeff\x00")
    if value.startswith(("\t","\r","\n")) or trimmed.startswith(("=","+","-","@")):
        return "'"+value
    return value


def export_csv(snapshot):
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream,fieldnames=CSV_FIELDS,lineterminator="\r\n")
    writer.writeheader()
    for row in metric_rows(snapshot.data()):
        writer.writerow({key:safe_csv_cell(row.get(key)) for key in CSV_FIELDS})
    return stream.getvalue().encode("utf-8-sig")


def export_json(snapshot):
    snapshot.data()  # 导出前核对快照自身完整性。
    return snapshot.json_text.encode("utf-8")


def export_filename(snapshot,extension):
    if extension not in {"html","json","csv"}: raise ValueError("仅支持HTML、JSON、CSV")
    identifier = snapshot.data()["report_id"]
    if re.fullmatch(r"report-[a-f0-9]{32}",identifier) is None: raise ValueError("报告ID无效")
    return "zhidiao-"+identifier[7:19]+"."+extension


def _table(headers,rows):
    return '<div class="table-scroll"><table><thead><tr>'+''.join('<th>'+esc(h)+'</th>' for h in headers)+'</tr></thead><tbody>'+''.join('<tr>'+''.join('<td>'+esc(v)+'</td>' for v in row)+'</tr>' for row in rows)+'</tbody></table></div>'


def _detail(title,value):
    return '<details><summary>'+esc(title)+'</summary><pre>'+esc(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False))+'</pre></details>'


def render_html(snapshot):
    data = snapshot.data()
    parts = ['<header><p class="eyebrow">智调 LAB / 可追溯实验报告</p><h1>'+esc(data['project'])+'</h1><p>'+esc(data['created_at'])+' · '+esc(data['report_id'])+'</p></header>',
        '<p class="notice">这是生成时的冻结快照，后续页面修改不会更新本报告。JSON SHA-256：'+esc(snapshot.sha256)+'</p>',
        '<p>'+esc(data['notes'])+'</p><h2>程序计算结果</h2><p class="notice">'+esc(data['normalization'])+'</p>']
    charts = []
    for index,run in enumerate(data['runs']):
        parts.append('<section><h2>'+esc(run['name'])+'</h2><p class="badge">'+esc(run['source_label'])+'</p><p>'+esc(run['notes'])+'</p>')
        parts.append(_table(['实验ID','数据摘要','结果ID','配置ID'],[[run['experiment_id'],run['source'].get('sha256'),run['result_id'],run['configuration_id']]]))
        parts.append(_detail('字段映射、单位、来源和处理记录', {k:run[k] for k in ('source','mapping','parser','operations','conditions')}))
        quality = run['quality']
        parts.append('<h3>数据质量</h3>'+_table(['行数','时长 / s','问题条数'],[[quality['row_count'],quality['duration_s'],len(quality['issues'])]]))
        parts.append(_detail('质量阈值、采样间隔统计和有效区间',{k:quality[k] for k in ('config','interval_stats','valid_intervals')}))
        parts.append(_detail('质量问题位置与原因（完整列表）',quality['issues']))
        p = run['performance']
        if p:
            parts.append(_detail('分析区间、初值、阈值、算法版本与完整指标',p))
        else:
            parts.append('<p class="notice">数据不足或尚未选定有效区间：本实验未计算性能指标，未以0替代。</p>')
        if p and not p.get('response'):
            parts.append('<p>单次阶跃尚未确认；响应指标未计算。</p>')
        evidence = run['evidence']
        parts.append('<h3>异常证据</h3>')
        if run.get('evidence_provenance'):
            parts.append('<p>'+esc(run['evidence_provenance'])+'</p>')
        if evidence:
            parts.append(_detail('检测规则、阈值和适用性',{k:evidence[k] for k in ('config','checks','algorithm_version')}))
            parts.append('<p>规则只检测现象，不证明根因；未发现事件不等于完全正常。</p>')
            for event in evidence['events']:
                parts.append('<article class="event"><b>'+esc(event['phenomenon'])+'</b><p>'+esc(event['event_id'])+'</p><p>尚不能确定的原因：'+esc(event['undetermined_causes'])+'</p>'+_detail('区间、阈值、观测量、图表及限制',event)+'</article>')
        else: parts.append('<p>异常规则未执行或当前配置无效，未补写检测结果。</p>')
        charts.extend([{**c,'title':run['name']+' · '+c['title']} for c in run['charts']])
        parts.append('<p>'+esc(run['chart_policy'])+'</p></section>')
    parts.append('<h2>指标表</h2>')
    rows = metric_rows(data)
    parts.append(_table(['实验','范围','指标','值','单位','状态','起点 / s','终点 / s','结果ID','配置ID'],[[r[k] if k!='status' else STATUS.get(r[k],r[k]) for k in ('experiment_name','scope','name','value','unit','status','start_s','end_s','result_id','configuration_id')] for r in rows]))
    parts.append(_detail('指标定义、状态原因和逐项配置',rows))
    if data['comparison']:
        c = data['comparison']
        parts.append('<h2>A/B对比条件及限制</h2>'+_detail('共同窗口、各侧条件、对齐区间与比较结果',c))
        parts.extend('<p class="notice">'+esc(w)+'</p>' for w in c['warnings'])
        charts.extend(data['comparison_charts'])
    else: parts.append('<h2>A/B对比</h2><p>本报告未进行A/B对比。</p>')
    parts.append('<h2>交互曲线</h2><p>缩放、悬停与复位均在本地运行；横轴单位见各图。不跨数据断点连接。</p>')
    for i,c in enumerate(charts):
        parts.append('<h3>'+esc(c['title'])+'</h3><div class="chart" id="chart-'+str(i)+'"></div>')
    if not charts: parts.append('<p>没有有效选段曲线；未填充假曲线。</p>')
    ai = data['ai']
    parts.append('<section class="ai"><h2>AI 辅助解释</h2><p>'+esc(ai['message'])+'</p><p>本区引用可能使用自己的较小分析区间；请按result_id核对，不能与主表范围混用。</p>')
    for turn in ai['turns']:
        parts.append('<h3>问题：'+esc(turn['question'])+'</h3>')
        answer = turn['answer']
        for ref in answer['measured_facts']:
            record = turn['evidence'][ref['result_id']]
            value = record['facts'][ref['metric_key']] if ref.get('metric_key') else record['events'][ref['event_id']]
            parts.append(_detail('程序证据 '+ref['result_id']+' / '+str(ref.get('metric_key') or ref.get('event_id')),value))
        parts.append('<h4>候选解释（未确认原因）</h4>')
        parts.extend('<p>'+esc(HYPOTHESES.get(item['code'],'未识别的解释类别，未展示模型断言'))+'</p>' for item in answer['candidate_explanations'])
        parts.append('<h4>下一步验证建议</h4>')
        parts.extend('<p>'+esc(VERIFICATIONS.get(code,'未识别的验证建议'))+'</p>' for code in answer['next_verification'])
        parts.extend('<p>'+esc(LIMITATIONS.get(code,'未识别的限制类别'))+'</p>' for code in answer['limitations'])
        parts.append(_detail('AI结构化引用与实际工具结果快照',turn))
    parts.append('</section><h2>下一步验证与报告限制</h2>')
    parts.extend('<p>'+esc(t)+'</p>' for t in [*data['next_verification'],*data['limitations']])
    parts.append(_detail('报告格式与数据保护',{'schema_version':data['schema_version'],**data['privacy']}))
    script_tags = ''
    hashes = []
    if charts:
        library = re.sub(r'</script',r'<\/script',get_plotlyjs(),flags=re.I)
        init = 'const figures='+json_script([_plot_safe(c['figure']) for c in charts])+''';
Promise.all(figures.map((f,i)=>Plotly.newPlot("chart-"+i,f.data,f.layout,{responsive:true,scrollZoom:true,displaylogo:false}))).then(()=>{document.documentElement.dataset.chartsReady="true";});
'''
        for identifier,script in (('plotly-library',library),('chart-init',init)):
            hashes.append("'sha256-"+base64.b64encode(hashlib.sha256(script.encode()).digest()).decode()+"'")
            script_tags += '<script id="'+identifier+'">'+script+'</script>'
    policy = "default-src 'none'; connect-src 'none'; script-src "+(' '.join(hashes) or "'none'")+"; style-src 'unsafe-inline'; img-src data: blob:; font-src data:; object-src 'none'; base-uri 'none'; form-action 'none'"
    style = """*{box-sizing:border-box}body{margin:0;background:#f8faf9;color:#183a32;font:16px/1.65 system-ui,'Microsoft YaHei',sans-serif}main{max-width:1120px;margin:auto;padding:32px 24px}h1,h2,h3{line-height:1.35;overflow-wrap:anywhere}h2{margin-top:32px}p,td,summary{overflow-wrap:anywhere}.eyebrow{color:#137a65;font-size:13px;letter-spacing:.1em}.badge{color:#137a65;font-weight:650}.notice{border-left:3px solid #137a65;background:#edf5f1;padding:12px}.table-scroll{overflow:auto;max-width:100%}table{border-collapse:collapse;width:100%;font-size:14px}th,td{text-align:left;border-bottom:1px solid #dce7e0;padding:9px;min-width:90px}th{background:#edf5f1}details{background:white;border:1px solid #dce7e0;border-radius:8px;padding:12px;margin:12px 0}summary{cursor:pointer;color:#137a65}pre{white-space:pre-wrap;overflow-wrap:anywhere;font-size:13px}.event{border-left:3px solid #bf6d25;padding:12px}.chart{width:100%;min-width:0;height:360px;background:white}.chart .modebar-container{top:100%!important}.ai{background:#eef4f7;border-radius:12px;padding:20px;margin-top:30px}@media(max-width:600px){main{padding:16px 12px}h1{font-size:25px}h2{font-size:21px}th,td{padding:6px}.chart{height:360px}.ai{padding:12px}}"""
    return '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="Content-Security-Policy" content="'+esc(policy)+'"><title>'+esc(data['project'])+' · 实验报告</title><style>'+style+'</style></head><body><main>'+''.join(parts)+'</main>'+script_tags+'</body></html>'


@dataclass(frozen=True)
class ExportBundle:
    snapshot: ReportSnapshot
    html: str
    json: bytes
    csv: bytes


def build_exports(snapshot):
    return ExportBundle(snapshot,render_html(snapshot),export_json(snapshot),export_csv(snapshot))
