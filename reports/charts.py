"""仅从对比原始采样点构造显示副本；无数值指标计算。"""
import json
import numpy as np
import plotly.graph_objects as go


def comparison_figures(result,a,b):
    groups = [[("A",a,result.a),("B",b,result.b)]] if result.can_overlay else [[("A",a,result.a)],[('B',b,result.b)]]
    charts = []
    for group in groups:
        for error in (False,True):
            fig = go.Figure()
            for letter,saved,side in group:
                frame = saved.prepared.frame.iloc[side.start_row-1:side.end_row]
                frame = frame.iloc[np.linspace(0,len(frame)-1,min(5000,len(frame)),dtype=int)]
                for field in (["error"] if error else ["target","actual"]):
                    fig.add_scatter(x=(frame.time-side.step.t0).tolist(),y=frame[field].tolist(),mode="lines",connectgaps=False,
                        name=f"{letter} · {field}",line={"color":"#137A65" if letter=="A" else "#BF6D25","dash":"dot" if field=="target" else "solid"})
            title = "A/B · "+("跟踪误差" if error else "目标与实测")+" · "+"/".join(g[0] for g in group)
            fig.update_layout(title=title,xaxis_title="相对于各自阶跃时刻 t−t0 [s]",yaxis_title=f"响应量 [{group[0][2].unit}]",xaxis_range=[0,result.config.common_duration_s],template="plotly_white",height=340)
            charts.append({"title":title,"figure":json.loads(fig.to_json())})
    return charts
