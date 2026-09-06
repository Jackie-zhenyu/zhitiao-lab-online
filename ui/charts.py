"""基础 Plotly 曲线；仅在显示副本上抽点，禁止改变分析数据。"""

import numpy as np
import pandas as pd
import plotly.graph_objects as go

from core.import_models import MappingConfig
from core.metric_models import MetricComputation


def build_charts(
    frame: pd.DataFrame, mapping: MappingConfig, max_points: int = 5000,
    *, response: MetricComputation | None = None,
) -> list[tuple[str, go.Figure]]:
    if len(frame) < 2 or max_points < 2:
        raise ValueError("绘图至少需要两个有效采样点")
    fields = ["time", "target", "actual", "error"]
    if "control" in frame:
        fields.append("control")
    if not np.isfinite(frame[fields].to_numpy(dtype=float)).all():
        raise ValueError("绘图数据含非有限值，请先检查质量并选择连续有效区间")
    if not (np.diff(frame["time"].to_numpy()) > 0).all():
        raise ValueError("绘图时间必须严格递增")
    if not (np.diff(frame.index.to_numpy()) == 1).all():
        raise ValueError("绘图必须使用连续原始数据行，不能跨缺失区间连线")
    positions = np.linspace(0, len(frame) - 1, min(len(frame), max_points), dtype=int)
    display = frame.iloc[positions].copy(deep=True)
    row_ids = display.index.to_numpy(copy=True) + 1
    definitions = [
        ("目标值与实测值", [("target", "目标值", "#758597"), ("actual", "实测值", "#137A65")], f"{mapping.quantity} [{mapping.unit}]"),
        ("误差曲线（目标 − 实测）", [("error", "误差", "#BF6D25")], f"误差 [{mapping.unit}]"),
    ]
    if "control" in display:
        definitions.append(("控制输出", [("control", "控制输出", "#536BC8")], f"控制输出 [{mapping.optional_units['control']}]"))
    charts = []
    for title, traces, yaxis in definitions:
        figure = go.Figure()
        for field, name, color in traces:
            figure.add_trace(go.Scatter(
                x=display["time"].to_numpy(copy=True),
                y=display[field].to_numpy(copy=True),
                customdata=row_ids,
                name=name, mode="lines", connectgaps=False,
                line={"color": color, "width": 2},
                hovertemplate="时间 %{x:.6g} s<br>数值 %{y:.6g}<br>原始数据行 %{customdata}<extra>%{fullData.name}</extra>",
            ))
        figure.update_layout(
            title=title, xaxis_title="时间 [s]", yaxis_title=yaxis,
            template="plotly_white", height=320, hovermode="x unified",
            margin={"l": 30, "r": 20, "t": 55, "b": 40},
            legend={"orientation": "h", "y": 1.15},
        )
        if title == "目标值与实测值" and response is not None:
            _add_response_markers(figure, response)
        charts.append((title, figure))
    return charts


def _add_response_markers(figure: go.Figure, response: MetricComputation) -> None:
    """只在有数值证据的响应观察区间内标注，不扩展原始分析数据。"""
    markers = response.markers
    start = response.details.get("observation_start_s", markers.get("t0"))
    end = response.details.get("observation_end_s")
    if not isinstance(start, (float, int)) or not isinstance(end, (float, int)):
        return
    if "band_lower" in markers and "band_upper" in markers:
        figure.add_shape(type="rect", x0=start, x1=end, y0=markers["band_lower"], y1=markers["band_upper"], line_width=0, fillcolor="rgba(19,122,101,0.14)", layer="below")
        figure.add_annotation(x=end, y=markers["band_upper"], text="目标误差带", showarrow=False, xanchor="right", yanchor="bottom", font={"size": 11})
    for key, label in (("y_lower", "上升下阈值"), ("y_upper", "上升上阈值")):
        if key in markers:
            figure.add_shape(type="line", x0=start, x1=end, y0=markers[key], y1=markers[key], line={"dash": "dot", "width": 1, "color": "#BF6D25"})
            figure.add_annotation(x=end, y=markers[key], text=label, showarrow=False, xanchor="right", yanchor="bottom", font={"size": 10})
    for key, label, height in (("t0", "t0", 1.0), ("t_lower", "下阈值时刻", 0.55), ("t_upper", "上阈值时刻", 1.0), ("t_settle", "目标入带时刻", 0.8)):
        if key in markers:
            figure.add_shape(type="line", x0=markers[key], x1=markers[key], y0=0, y1=1, yref="paper", line={"dash": "dash", "width": 1, "color": "#8B759C"})
            figure.add_annotation(x=markers[key], y=height, yref="paper", text=f"{label}<br>{markers[key]:.6g} s", showarrow=False, font={"size": 10})
