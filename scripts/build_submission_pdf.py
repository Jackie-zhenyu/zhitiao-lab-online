"""Build the reviewed 16-page competition application PDF from public evidence.

This is a delivery-document builder, not an experiment-report PDF feature.
Run with the isolated submission-tools environment. Original CSV is read only.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import html
import io
import json
from pathlib import Path
import re

from PIL import Image
from pypdf import PdfReader
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas
from reportlab.platypus import Paragraph

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "work/submission-assets"
OUT = ROOT / "outputs/submission/智调Lab-应用方案.pdf"
QA = ROOT / "work/submission-pdf-qa.json"
WIDTH, HEIGHT = landscape(A4)
MARGIN = 46
BODY_WIDTH = WIDTH - 2 * MARGIN
GREEN = colors.HexColor("#137A65")
INK = colors.HexColor("#183B34")
TEXT = colors.HexColor("#314D47")
MUTED = colors.HexColor("#647E76")
PALE = colors.HexColor("#ECF4EF")
LINE = colors.HexColor("#D8E6DE")
ORANGE = colors.HexColor("#BE7734")
CREAM = colors.HexColor("#FBF7EF")

pdfmetrics.registerFont(TTFont("Yahei", "C:/Windows/Fonts/msyh.ttc", subfontIndex=0))
pdfmetrics.registerFont(TTFont("Yahei-Bold", "C:/Windows/Fonts/msyhbd.ttc", subfontIndex=0))
pdfmetrics.registerFontFamily("Yahei", normal="Yahei", bold="Yahei-Bold")


def safe(value):
    return html.escape(str(value)).replace("\n", "<br/>")


class Book:
    def __init__(self):
        self.stream = io.BytesIO()
        self.c = canvas.Canvas(self.stream, pagesize=(WIDTH, HEIGHT), pageCompression=1)
        self.c.setTitle("智调 Lab - 复赛应用方案")
        self.c.setAuthor("智调 Lab")
        self.c.setSubject("控制实验日志分析 Agent；本地分析、受限工具、可追溯交付")
        self.page_number = 0
        self.records = []
        self.page_record = None

    def box(self, x, y, w, h, fill=PALE, border=None, radius=10):
        self.c.setFillColor(fill)
        self.c.setStrokeColor(border or fill)
        self.c.roundRect(x, y, w, h, radius, fill=1, stroke=int(border is not None))

    def p(self, text, x, top, w, size=13, leading=21, color=TEXT,
          bold=False, max_height=None, min_bottom=52, markup=False):
        style = ParagraphStyle("content", fontName="Yahei-Bold" if bold else "Yahei",
            fontSize=size, leading=leading, textColor=color, alignment=TA_LEFT,
            wordWrap="CJK", splitLongWords=True, spaceAfter=0)
        paragraph = Paragraph(text if markup else safe(text), style)
        pw, ph = paragraph.wrap(w, 1000)
        bottom = top - ph
        assert bottom >= min_bottom, (self.page_number, "bottom overflow", text[:50], bottom)
        assert x >= 0 and x + pw <= WIDTH + .1, (self.page_number, "horizontal overflow")
        if max_height is not None:
            assert ph <= max_height + .1, (self.page_number, "block overflow", text[:70], ph, max_height)
        paragraph.drawOn(self.c, x, bottom)
        self.page_record["blocks"].append({"x":x, "top":top, "width":w, "height":ph,
            "font_size":size, "text":re.sub("<[^>]+>", "", text)})
        return bottom

    def line(self, x1, y1, x2, y2, color=LINE, width=1):
        self.c.setStrokeColor(color)
        self.c.setLineWidth(width)
        self.c.line(x1, y1, x2, y2)

    def page(self, section, title, subtitle=""):
        if self.page_number:
            self.c.showPage()
        self.page_number += 1
        self.page_record = {"page":self.page_number, "title":title, "blocks":[], "screenshots":[]}
        self.records.append(self.page_record)
        self.c.setFillColor(colors.white)
        self.c.rect(0, 0, WIDTH, HEIGHT, stroke=0, fill=1)
        self.c.setFillColor(GREEN)
        self.c.rect(0, HEIGHT-8, WIDTH, 8, stroke=0, fill=1)
        self.p("智调 LAB  /  应用方案", MARGIN, HEIGHT-27, 300, 10, 15, GREEN)
        self.p(section, WIDTH-230, HEIGHT-27, 184, 10, 15, MUTED)
        self.p(title, MARGIN, HEIGHT-71, BODY_WIDTH, 27, 35, INK, True, max_height=40)
        if subtitle:
            self.p(subtitle, MARGIN, HEIGHT-116, BODY_WIDTH, 11.5, 18, MUTED, max_height=36)
        self.line(MARGIN, 42, WIDTH-MARGIN, 42)
        self.p("2026.09.06  ·  本地可运行版  ·  数值由程序计算", MARGIN, 30, 590, 9, 12, MUTED, min_bottom=10)
        self.p(f"{self.page_number:02d} / 16", WIDTH-100, 30, 54, 9, 12, MUTED, min_bottom=10)

    def note(self, text, y=68, h=54, fill=PALE):
        h=max(h,58)
        for block in self.page_record["blocks"]:
            if min(block["x"]+block["width"],MARGIN+BODY_WIDTH)-max(block["x"],MARGIN)>1:
                overlap=min(block["top"],y+h)-max(block["top"]-block["height"],y)
                assert overlap<=1, (self.page_number,"note would cover text",block["text"])
        self.box(MARGIN, y, BODY_WIDTH, h, fill=fill)
        self.p(text, MARGIN+17, y+h-12, BODY_WIDTH-34, 11.5, 17.5,
               color=INK, max_height=h-18)

    def section(self, number, title, x, top, w):
        self.p(number, x, top, 35, 12, 20, GREEN, True)
        self.p(title, x+40, top, w-40, 15, 21, INK, True)
        return top-32

    def table(self, headers, rows, x, top, widths, row_h=47, font=12):
        total = sum(widths)
        self.box(x, top-34, total, 34, GREEN, radius=5)
        xx=x
        for text, w in zip(headers, widths):
            self.p(text, xx+10, top-8, w-20, 11.5, 17, colors.white, True, max_height=23)
            xx+=w
        y=top-34
        for i, row in enumerate(rows):
            self.c.setFillColor(PALE if i%2==0 else colors.white)
            self.c.rect(x, y-row_h, total, row_h, stroke=0, fill=1)
            xx=x
            for text, w in zip(row,widths):
                self.p(str(text),xx+10,y-10,w-20,font,18,INK if xx==x else TEXT,
                       bold=xx==x,max_height=row_h-15)
                xx+=w
            y-=row_h
        return y

    def screenshot(self, name, x, top, w, h, caption):
        path=ASSETS/name
        assert path.exists(), f"Real screenshot required: {name}"
        with Image.open(path) as im:
            iw, ih=im.size
        scale=min(w/iw,h/ih)
        dw,dh=iw*scale,ih*scale
        self.box(x-5,top-dh-5,dw+10,dh+10,colors.white,LINE,5)
        self.c.drawImage(str(path),x,top-dh,width=dw,height=dh,mask="auto")
        self.p(caption,x,top-dh-13,w,10,15,MUTED,max_height=34)
        self.page_record["screenshots"].append({"file":name,"sha256":hashlib.sha256(path.read_bytes()).hexdigest(),
            "pixel_size":[iw,ih],"drawn_size":[dw,dh],"x":x,"top":top,"width":dw,"height":dh})

    def chart(self, x, top, w, h, series, title="阶跃后的原始响应"):
        self.box(x,top-h,w,h,colors.white,LINE)
        self.p(title,x+15,top-13,w-30,12,18,INK,True)
        px,py=x+44,top-h+43
        pw,ph=w-62,h-91
        for val in (0,.25,.5,.75,1):
            yy=py+val/1.08*ph
            self.line(px,yy,px+pw,yy,LINE,.6)
            self.p(f"{val:g}",px-35,yy+7,29,8.8,11,MUTED,min_bottom=0)
        for tick in (0,5,10,15,20):
            xx=px+tick/20*pw
            self.p(str(tick),xx-6,py-10,25,9,12,MUTED,min_bottom=0)
        self.line(px,py,px,py+ph,MUTED,.7)
        self.line(px,py,px+pw,py,MUTED,.7)
        self.c.setDash(3,3)
        self.line(px,py+ph/1.08,px+pw,py+ph/1.08,MUTED,.9)
        self.c.setDash()
        for label, points, color in series:
            path=self.c.beginPath()
            for index, (t,value) in enumerate(points):
                xx=px+t/20*pw
                yy=py+value/1.08*ph
                if index==0:path.moveTo(xx,yy)
                else:path.lineTo(xx,yy)
            self.c.setStrokeColor(color)
            self.c.setLineWidth(2)
            self.c.drawPath(path)
        self.p("响应量 [1]",x+15,top-35,150,9.2,13,MUTED)
        self.p("距各自阶跃 t - t0 [s]",x+w/2-68,top-h+20,190,9.2,13,MUTED,min_bottom=0)
        self.p("A",x+w-89,top-35,20,10,14,GREEN,True)
        self.p("B",x+w-57,top-35,20,10,14,ORANGE,True)


def load_inputs():
    draft=(ROOT/"docs/SUBMISSION_CONTENT.md").read_text(encoding="utf-8")
    assert all(f"第 {i:02d} 页" in draft for i in range(1,17))
    data=json.loads((ASSETS/"ab-evidence.json").read_text(encoding="utf-8"))
    report=json.loads((ASSETS/"ab-report.json").read_text(encoding="utf-8"))
    assert data["report_id"]==report["report_id"]
    assert data["comparison_config"]["common_duration_s"]==20
    assert data["ai_status"]["status"]=="not_participated"
    audit=json.loads((ROOT/"docs/acceptance/live-delivery-summary.json").read_text(encoding="utf-8"))
    assert audit["tests"]["passed"]==701 and not audit["tests"]["failures"]
    assert all(item["matches_stage8"] for item in audit["protected_core"])
    series=[]
    for letter,color in (("A",GREEN),("B",ORANGE)):
        side=data["sides"][letter]
        raw=(ROOT/"examples/closed_loop_data"/side["source"]["filename"]).read_bytes()
        assert hashlib.sha256(raw).hexdigest()==side["source"]["sha256"]
        t0=side["step"]["t0"]
        points=[(float(row["time"])-t0,float(row["actual"]))
                for row in csv.DictReader(io.StringIO(raw.decode("utf-8-sig")))
                if t0<=float(row["time"])<=t0+20]
        assert len(points)==1001
        series.append((letter,points,color))
    live=json.loads((ROOT/"docs/acceptance/live-ai-summary.json").read_text(encoding="utf-8"))
    assert live["success"] is True and live["tool_calls"]==3
    assert live["requested_range_s"]==[0,21] and live["output_corrections"]==1
    assert live["source_sha256"]==data["sides"]["A"]["source"]["sha256"]
    return draft,data,report,audit,series,live


def build():
    draft,data,report,audit,series,live=load_inputs()
    if OUT.exists():
        prior=json.loads(QA.read_text(encoding="utf-8")) if QA.exists() else {}
        if prior.get("sha256")!=hashlib.sha256(OUT.read_bytes()).hexdigest():
            raise FileExistsError("Existing PDF is not this builder's last verified output; refusing overwrite.")
    b=Book()
    m=MARGIN
    full=BODY_WIDTH
    col=(full-28)/2
    rx=m+col+28
    metrics={item["key"]:item for item in data["differences"]}

    # 01 - cover
    b.page("作品主张", "从实验日志，到有依据的解释")
    b.p("智调 Lab",m,463,380,44,57,INK,True)
    b.p("面向大学生控制实验、电机调试\n与机器人实验的日志分析 Agent。",m,385,350,17,28,TEXT)
    b.p("让每一个数值可复算，\n让每一句结论有出处。",m,309,340,23,34,GREEN,True)
    b.p("上传 CSV，确认含义与区间；程序计算指标和异常证据，可选 AI 调用受限工具组织解释，最终交付能核对、能继续复盘的实验材料。",m,215,330,13,21)
    b.chart(rx+12,456,col-12,256,series,"真实计算示例 · PI A/B")
    b.p("闭环仿真数据，非实测。曲线来自公开示例原始采样；共同观察 20 s，不以装饰曲线代替实验数据。",rx+14,184,col-16,11.5,18,MUTED)
    b.note("交付：可运行源码 + 应用方案 + 操作演示。本地分析无需模型密钥；通义千问已完成一次公开实验的真实工具调用与证据校验。",h=58)

    # 02 - problem
    b.page("01 / 背景与需求", "实验结束，分析工作才刚开始", "以下是工作流风险与需求假设，不是虚构的用户调研统计。")
    b.p("一份 CSV 还不能直接回答：记录是否连续，指标口径是否一致，两次实验是否可比，以及 AI 的判断究竟引用了什么。智调 Lab 把确认和核对放在解释之前。",m,431,full,13,21,max_height=55)
    b.table(["分析环节","常见工作流风险","产品回应"],[
        ("导入与整理","编码、列名或时间单位不一致","显式字段与单位确认"),
        ("观察与计算","忽略断点，混算多阶跃或时间权重","连续区间 + 公开数值定义"),
        ("实验对比","窗口或条件不同，却归因于参数修改","阶跃对齐 + 共同窗口 + 条件核对"),
        ("解释与交付","AI 数值无来源，页面新配置配旧结果","结果引用 + 冻结报告 + 恢复重算"),
    ],m,351,[116,310,full-426],row_h=50)
    b.note("目标不是承诺未经测量的提效比例，而是让学生能说明：在什么数据、区间和规则下，程序得到了这个结果。",h=55)

    # 03 - users
    b.page("01 / 背景与需求", "服务一次完整的实验复盘", "首要用户是大学生；教师、助教和学生项目组是延伸使用场景。")
    cards=[("大学生","解释一次响应，比较两次实验，完成有依据的实验报告。","指标有定义、状态、单位与来源。"),
           ("教师与助教","核对分析口径，追查单位、选段和结论引用。","查看结构化结果，不替代教学评价。"),
           ("学生项目组","离线复盘电机、控制或机器人日志，保留分析上下文。","下载项目包，恢复时重新核算。")]
    cw=(full-32)/3
    for i,(title,text,result) in enumerate(cards):
        x=m+i*(cw+16)
        b.box(x,230,cw,207)
        b.p(title,x+17,418,cw-34,18,26,INK,True)
        b.p(text,x+17,377,cw-34,13,22,max_height=94)
        b.p(result,x+17,282,cw-34,11.5,18,GREEN,max_height=44)
    b.p("最小输入",m,200,130,15,22,INK,True)
    b.p("time / target / actual；可选 control、p_term、i_term、d_term。时间支持秒与毫秒，物理量和共同单位由用户确认。未知设备、负载和环境保持未知。",m+135,201,full-135,13,21,max_height=65)
    b.note("当前不包含教师账户、班级管理、多人协作或自动评分。先让单次复盘可复查，再通过真实试用验证后续需求。",h=50)

    # 04 - product layout
    b.page("02 / 功能与交互", "一个工作台，三个主要入口", "实验分析 / 实验对比 / 报告预览；AI 位于独立标签，不挤压主要图表。")
    b.screenshot("01-home.png",m,432,485,275,"实际运行界面：作品导览与三个导航入口。")
    x=m+509
    t=434
    for no,title,text in [
        ("01","导入与确认","上传自己的 CSV，或从公开示例开始。左侧核对字段、单位、区间与规则。"),
        ("02","检查与分析","主区查看质量、指标、曲线；在异常证据区展开依据并聚焦原始片段。"),
        ("03","比较与交付","保存两份确认快照，检查共同窗口与条件，再导出报告和项目包。")]:
        t=b.section(no,title,x,t,full-509)
        t=b.p(text,x,t,full-509,12,19,max_height=81)-18
    b.note("操作顺序：导入 → 字段/单位 → 质量/有效段 → 单阶跃 → 异常证据 → A/B → 可选 AI → 报告。没有模型配置仍能完成全部本地步骤。",h=58)

    # 05 - quality
    b.page("02 / 功能与交互", "先确认数据，再解释实验", "原始字节只读；不静默排序、删除、填补、平滑或跨断点插值。")
    t=b.section("01","受控导入",m,435,col)
    t=b.p("支持 UTF-8 / BOM，编码与分隔符可手选。默认 20 MiB、200000 条记录；限制在读取与解析过程中执行，超限拒绝。",m,t,col,13,21)-20
    t=b.section("02","含义由用户确认",m,t,col)
    t=b.p("推荐映射必须确认。秒或毫秒统一为秒；目标与实测同物理量、同单位。不猜编码器分辨率，不把脉冲擅自换成角度。",m,t,col,13,21)-20
    t=b.section("03","质量问题可定位",m,t,col)
    b.p("检查缺失、非数值、无穷、重复时间、倒退、疑似重置和采样异常。阻断问题停止整段分析，须选择连续有效片段。",m,t,col,13,21)
    b.screenshot("03-quality.png",rx,433,col,180,"实际问题数据界面；解析函数合成数据，非实测。")
    b.p("默认采样间隔为正相邻时间差中位数的 0.5 至 2.0 倍，可配置。规则命中不等于设备故障；SHA-256 不认证来源真实性。",rx,188,col,12,19,max_height=65)
    b.note("数据摘要、原始行、实际时间和转换操作一起保留。发现问题时展示原因，不用一条“清洗后正常”的曲线掩盖原始记录。",h=50)

    # 06 - metrics
    b.page("02 / 功能与交互", "指标定义公开，计算状态明确", "响应采用相对于目标值的归一化约定；不是按实测稳态归一化的标准 stepinfo。")
    b.p("一般跟踪 · 实际时间戳积分",m,435,col,16,23,INK,True)
    b.table(["指标","本版定义"],[
        ("RMSE_t","sqrt(积分 e²dt / 区间时长)"),
        ("IAE","积分 |e|dt"),
        ("最大绝对误差","采样点 max(|e|)"),
        ("末段平均偏差","默认末尾 10% 时间窗的平均误差"),
    ],m,398,[130,col-130],row_h=44,font=11.5)
    b.p("e = target - actual。复合梯形积分支持非等间隔，全量有效段用于计算。末段偏差不称“稳态误差”，采样点间峰值可能漏检。",m,169,col,11.5,18,max_height=79)
    b.p("单阶跃 · 先确认 t0、r0、r1、y0",rx,435,col,16,23,INK,True)
    b.p("q = (actual - y0) / (r1 - y0)\n有效阶跃前基线估计 y0；无基线时由用户明确提供。正向与负向阶跃使用同一约定。",rx,399,col,12.5,20,max_height=82)
    b.p("上升时间：q 首次从 0.1 到 0.9 的时间差。\n超调：max(0, max(q) - 1) × 100%。\n目标带：|actual - r1| ≤ max(0.02|r1 - y0|, 绝对容差)。",rx,311,col,12.5,21,max_height=105)
    b.p("目标调节时间：最后一次进入并留在目标带的时刻减去 t0；默认还需 0.5 s 后续观察。阈值交点仅在相邻有效点间线性求值。",rx,188,col,12,19,max_height=82)
    b.note("只表示“在本次观测区间内满足”。静态目标、短记录、未达交点或数据断点返回状态与原因，不以 0 代替不可计算。完整口径见 docs/METRICS.md。",y=54,h=46)

    # 07 - events
    b.page("02 / 功能与交互", "看到异常现象，也看到尚不确定的原因", "规则公开且可配置；持续时间和时间占比按实际时间计算。")
    b.table(["检测到的现象","不能据此直接确认"],[
        ("稳定目标附近持续波动","某个 PID 参数一定错误"),
        ("输出持续接近确认限值","已发生积分饱和"),
        ("统计突变或变化率异常","传感器发生故障"),
        ("时间缺失、重复或重置","设备、通信或记录程序责任"),
    ],m,435,[175,col-175],row_h=51,font=11.5)
    b.p("事件保留 ID、实验、原始行/时间、规则、阈值、观测量与限制。点击卡片聚焦片段，性能分析区间保持原确认值。",m,179,col,12.5,20,max_height=85)
    b.screenshot("04-events.png",rx,433,col,170,"实际证据卡与聚焦交互；公开问题示例，非设备故障实录。")
    b.p("没有 control 或未确认限值，不判断输出近限。没有合理物理变化率上限，只给统计候选。阈值区分默认与用户值，判断依据可查。",rx,200,col,12.5,21,max_height=90)
    b.note("未发现事件，不等于证明系统完全正常。现象卡回答“哪里发生了什么”，原因仍需结合采集链路、设备资料或重复实验验证。",y=54,h=46)

    # 08 - comparison
    b.page("02 / 功能与交互", "把可比条件，摆到差异前面", "真实计算示例：同一一阶对象、初值、目标、采样和环境，仅 PI 参数不同。闭环仿真数据，非实测。")
    table_rows=[]
    for key,label,unit in (("rmse_t","时间加权 RMSE","1"),("iae","IAE","1·s"),
                            ("rise_time","上升时间","s"),("overshoot_pct","超调","%"),("settling_time","目标调节时间","s")):
        item=metrics[key]
        table_rows.append((f"{label} [{unit}]",f"{item['a']['value']:.6f}",f"{item['b']['value']:.6f}"))
    b.table(["指标","A","B"],table_rows,m,435,[154,95,col-249],row_h=40,font=11.5)
    b.p("共同阶跃后窗口：20 s。各侧使用原始时间戳；横轴按各自 t - t0 对齐。单位、目标、初值、评价参数与条件先核对，再给描述性差异。",m,176,col,12,19,max_height=80)
    b.chart(rx,435,col,246,series)
    b.p("B 在本次条件下的所列响应时间和跟踪误差更小，超调相同。差值为 B - A；百分比为 100 × (B - A) / |A|。A 超调为 0，变化百分比不计算。",rx,168,col,12,19,max_height=82)
    b.note("不评分，不宣布绝对优胜，也不向真实设备推荐该参数。表值直接读取本次冻结报告；报告及完整配置、源摘要随示例报告 JSON 交付。",y=54,h=47)

    # 09 - AI purpose
    b.page("03 / AI 核心作用", "让模型调度分析，让程序守住数值", "下图为执行结构示意；真实单次联调的过程与证据见下一页，示意图不冒充运行画面。")
    node_w=(full-34)/3
    nodes=[("用户问题","理解实验指代与所需区间"),("逐轮授权","先展示必要摘要和发送范围"),("模型选择工具","由模型提出工具名与参数"),
           ("应用校验执行","白名单、会话、参数与次数限制"),("返回结构化结果","真实指标、事件和比较结果"),("证据校验与解释","程序渲染数值，模型组织分析")]
    for i,(title,text) in enumerate(nodes):
        row,idx=divmod(i,3)
        x=m+idx*(node_w+17)
        top=434-row*99
        b.box(x,top-82,node_w,82,PALE if i!=1 else CREAM)
        b.p(f"{i+1:02d}  {title}",x+14,top-13,node_w-28,13,20,INK,True)
        b.p(text,x+14,top-43,node_w-28,11.5,18,max_height=28)
    b.p("四个完整工具",m,228,col,15,22,INK,True)
    b.p("profile_data：质量摘要与有效段\nanalyze_run：指定范围的性能计算\ndetect_events：选段内的规则现象\ncompare_runs：两份实验的共同窗口对比",m,202,col,12,20,max_height=90)
    b.p("可选通义千问适配",rx,228,col,15,22,INK,True)
    b.p("通过 OpenAI Python SDK 访问百炼兼容 Chat Completions 的 tools / tool_calls；SDK 名称不代表调用 OpenAI 模型。服务地址、模型和密钥由服务端配置，业务计算不写死模型名。",rx,202,col,12,20,max_height=92)
    b.note("无密钥时显示“AI 未配置”，本地功能完整；测试替身只用于离线测试，不能在正式界面冒充在线模型。",y=54,h=39)

    # 10 - AI evidence
    b.page("03 / AI 真实联调", "三个工具，一组能核对的结果", "2026-09-06 · 北京 qwen-plus · “分析实验A” · 公开 PI A 闭环仿真数据，非实测。")
    lm=live["results"]["analyze_run"]["metrics"]
    b.table(["程序计算指标","本轮实际值"],[
        ("时间加权 RMSE",f"{lm['rmse_t']['value']:.9f}  [1]"),
        ("IAE",f"{lm['iae']['value']:.9f}  [1·s]"),
        ("目标归一化上升时间",f"{lm['rise_time']['value']:.9f} s"),
        ("目标归一化超调量",f"{lm['overshoot_pct']['value']:.0f} %"),
        ("目标调节时间",f"{lm['settling_time']['value']:.9f} s"),
    ],m,435,[163,col-163],row_h=39,font=11)
    b.p("范围：跟踪 0–21 s；阶跃响应 1–21 s。A/B 页的跟踪区间为 1–21 s，不能把两个区间的指标混为一谈。",m,184,col,11.5,18,max_height=59)
    b.p("完整结果 ID、指标键、阈值与源摘要：\ndocs/acceptance/live-ai-summary.json",m,115,col,10.5,16,MUTED,max_height=39)
    b.p("本轮实际执行记录（据页面转写）",rx,435,col,15,22,INK,True)
    b.p("用户确认发送摘要\n→ 模型调用 profile_data\n→ 调用 analyze_run（0–21 s）\n→ 调用 detect_events（0–21 s）\n→ 返回结构化结果\n→ 输出校验拒绝，有限纠正一次\n→ 最终结构与证据引用校验通过",rx,398,col,12.5,23,max_height=164)
    b.p("本轮共 3 次模型请求、3 次工具调用。原始数据 1051 行，质量问题与规则事件均为 0；未确认输出限值，不判断输出近限。",rx,220,col,12,19,max_height=80)
    b.p("3 次模型请求不等于 3 轮用户追问。本次仅验证单实验的一次问题；真实区间追问与 AI A/B 对比仍未验证。",rx,127,col,11.5,18,max_height=55)

    # 11 - architecture
    b.page("04 / 技术实现", "保留清楚的计算边界", "Windows x64 / Python 3.12.14；项目独立虚拟环境，锁定依赖。下图为代码模块结构示意。")
    boxes=[(m,431,230,"UI 与会话","app.py + ui/\nStreamlit 工作台与明确确认"),
           (m+259,431,230,"数值与证据","core/\n解析、质量、指标、异常、对比"),
           (m+518,431,full-518,"受限 Agent","agent/\n适配、工具执行、证据校验"),
           (m,303,230,"冻结报告","reports/\n同一快照导出 HTML / JSON / CSV"),
           (m+259,303,230,"项目归档","projects/\n结构、摘要、版本校验与重算"),
           (m+518,303,full-518,"验证与交付","examples/ · tests/ · scripts/\n公开数据、测试与本地启动")]
    for x,top,w,title,text in boxes:
        b.box(x,top-105,w,105)
        b.p(title,x+16,top-15,w-32,16,23,INK,True)
        b.p(text,x+16,top-48,w-32,11.5,19,max_height=49)
    b.p("pandas / NumPy 处理数据与计算；Plotly 展示交互曲线；Pydantic 校验结构；pytest / AppTest 验证数值和页面。SciPy 为锁定分析依赖，当前指标不依赖复杂求解。",m,166,full,13,21,max_height=51)
    b.note("核心计算不依赖 Streamlit 或模型；Agent 复用 core。没有前后端分离、向量库、实时串口、自动下发 PID 或模型训练，方便课程环境本地审查与复算。",y=54,h=48)

    # 12 - reports
    b.page("04 / 技术实现", "结果离开页面，依据仍然保留", "报告用于阅读与引用；项目包用于重算与继续工作，两者承担不同任务。")
    t=b.section("01","冻结报告",m,435,col)
    t=b.p("HTML / JSON / 指标 CSV 共用生成时快照，含来源、摘要、映射、单位、区间、阈值、版本、指标、交互图、异常与 A/B 条件。程序计算与 AI 解释分区，无 AI 正常导出并标记未参与。",m,t,col,12.5,20,max_height=126)-19
    t=b.section("02","可恢复项目",m,t,col)
    b.p(".labproj 保留原始 CSV、确认配置与最多 8 份快照。上传后校验结构、摘要和版本，重新分析与比较；全部通过才替换会话。历史报告只读归档，不变成当前 AI 证据。",m,t,col,12.5,20,max_height=124)
    b.screenshot("07-report.png",rx,433,col,225,"实际报告页面；HTML / JSON / CSV 来自同一冻结结果。")
    b.p("HTML 只内嵌一次 Plotly，无 CDN 依赖，不嵌完整 CSV。不可信备注和模型文字作为文本转义；CSV 文本防公式注入，合法负数保留。",rx,181,col,12.5,20,max_height=87)
    b.note("项目包需主动下载且未加密；摘要不证明作者或实测真实性。密钥、授权、活动聊天不恢复。应用尚不支持实验报告 PDF；本应用方案 PDF 是独立交付文档。",y=54,h=47)

    # 13 - instructions
    b.page("05 / 使用说明", "从首次启动，到下次继续复盘", "默认交付本地可运行版；无需把真实密钥发送到聊天或写进代码。")
    b.box(m,295,col,140)
    b.p("Windows 安装与启动",m+17,417,col-34,15,23,INK,True)
    b.p("安装 Python 3.12，解压源码后在根目录执行：",m+17,382,col-34,12,19)
    b.p("scripts\\install.cmd\nscripts\\start.cmd",m+17,347,col-34,13,22,GREEN,True,max_height=46)
    b.p("浏览器打开 http://127.0.0.1:8501/。安装器检查独立环境与锁定依赖，失败明确退出，不修改系统设置。其他系统对应命令见 README。",m,268,col,12.5,20,max_height=82)
    b.p("可选模型配置",m,166,col,15,22,INK,True)
    b.p("本地 .env / 服务端配置 LLM_PROVIDER、LLM_BASE_URL、LLM_MODEL、LLM_API_KEY；重启后逐轮核对发送摘要并授权。未配置仍可本地演示。",m,136,col,11.5,18,max_height=72)
    y=432
    for no,title,text in [
        ("01","准备","加载示例或上传 CSV；核对映射和单位，检查质量；断点处选择连续有效段。"),
        ("02","分析","核对阶跃时刻、前后目标和基线；确认后查看指标与证据卡，保存 A/B 独立快照。"),
        ("03","比较","核对对象、负载、采样与环境；确认共同观察窗口，查看差异和限制。"),
        ("04","交付","报告页生成后下载 HTML / JSON / CSV；关机前下载项目包，下次上传校验恢复。")]:
        y=b.section(no,title,rx,y,col)
        y=b.p(text,rx,y,col,12,19,max_height=64)-19

    # 14 - validation
    b.page("06 / 验证与边界", "用可复查的记录，支持可计算的结论", "本次全量离线测试；真实 API 联调单独标记，不计入离线通过数量。")
    b.box(m,330,full,106)
    b.p(str(audit["tests"]["passed"]),m+20,424,170,48,59,GREEN,True)
    b.p("项通过",m+202,411,110,20,29,INK,True)
    b.p(f"0 失败 / 0 错误 / 0 跳过\n{audit['tests']['console_duration_s']:.2f} s · Python 3.12.14 · 禁止测试访问真实网络",m+320,408,full-345,14,25,INK)
    b.p("独立数值基准",m,304,col,15,22,INK,True)
    b.p("y = 1 - exp(-t)，y0 = 0，目标为 1。理论上升时间 ln(9) ≈ 2.197225 s；2% 调节时间 -ln(0.02) ≈ 3.912023 s；超调为 0。容差依据采样间隔，不调用被测函数生成期望。",m,272,col,12.5,20,max_height=104)
    b.p("测试与保护",rx,304,col,15,22,INK,True)
    b.p("覆盖非等间隔、负阶跃、静态目标、短记录、断点，及会话隔离、工具白名单、证据失效、快照一致性和安全转义。核心 13 份文件摘要未变；实际浏览器覆盖本地操作链路。",rx,272,col,12.5,20,max_height=104)
    b.p("复查命令：python -m pytest -q\n记录：docs/acceptance/live-delivery-summary.json 与对应 JUnit / 日志摘要。",m,153,full,11.5,18,MUTED,max_height=42)
    b.note("真实单次 AI 联调另见第10页。仍未验证：真实 AI 多轮与 A/B、真实设备效果、多人压力、跨平台实机、Docker运行和物理断网重开HTML。",y=54,h=45)

    # 15 - future
    b.page("07 / 应用前景", "从教学验证开始，再决定商业扩展", "应用场景与商业模式是待验证方向；当前没有客户、收入或市场规模验证。")
    b.table(["拟验证场景","当前可提供的价值","下一步验证"],[
        ("控制课程实验","形成原始数据到证据结论的作业材料","不同使用者能否按同口径复算"),
        ("电机 / 机器人学生项目","离线复盘阶跃日志与同条件实验差异","真实采样问题是否能准确定位"),
        ("教师与助教核对","读取结构化结果、区间和限制","报告是否帮助发现口径不一致"),
    ],m,435,[162,292,full-454],row_h=58,font=12)
    b.p("商业路径假设",m,193,col,15,22,INK,True)
    b.p("个人本地版作为低门槛体验入口；可探索实验室部署支持、课程示例、培训与数据规范服务。机构功能须先验证需求与成本，不列为当前已上线能力。",m,161,col,12.5,20,max_height=101)
    b.p("可检验的试用方法",rx,193,col,15,22,INK,True)
    b.p("获得试用同意，收集去标识化任务与错误反馈；预先约定正确性、可复算性和任务完成时间的测量方法，再根据实际记录决定协作与机构部署方向。",rx,161,col,12.5,20,max_height=101)

    # 16 - handoff
    b.page("08 / 作品交付", "一个能运行、能核对的作品", "用程序守住数值口径，用证据约束 AI 解释，用明确状态保留实验不确定性。")
    b.table(["交付件","用途与使用方式"],[
        ("应用方案 PDF","16 页，覆盖背景、需求、技术、功能、AI、交互说明与应用前景。"),
        ("操作演示 MP4","真实界面截图定格讲解，包含本次 AI 工具执行与证据画面；机制图另标示意。"),
        ("可运行源码 ZIP","应用、锁定依赖、公开示例、测试和 Windows 脚本；解压后本地启动。"),
        ("示例报告与项目包","报告可阅读和核对；项目包用于重算恢复，两者均标注仿真来源。"),
    ],m,435,[172,full-172],row_h=48,font=12)
    b.p("能力边界",m,176,col,15,22,INK,True)
    b.p("不连接设备或自动调参，不确认故障根因，不保证未来稳定，不混算多阶跃，不做频域、多变量或任意开放式理论问答。未知条件不补写。",m,145,col,12.5,20,max_height=87)
    b.p("本地交付说明",rx,176,col,15,22,INK,True)
    b.p("127.0.0.1 仅本机可访问，不是评审公网链接。按赛事入口提交源码；若必须公网地址，需另行完成访问控制、资源与密钥配置及部署验收。",rx,145,col,12.5,20,max_height=87)

    assert b.page_number==16
    # Text and screenshots must occupy separate rectangles; shared backgrounds are ignored.
    for page in b.records:
        blocks=page["blocks"]+page["screenshots"]
        for index, a in enumerate(blocks):
            for other in blocks[index+1:]:
                overlap_x=min(a["x"]+a["width"],other["x"]+other["width"])-max(a["x"],other["x"])
                overlap_y=min(a["top"],other["top"])-max(a["top"]-a["height"],other["top"]-other["height"])
                assert overlap_x<=1 or overlap_y<=1, (page["page"],"overlapping blocks",a.get("text",a.get("file")),other.get("text",other.get("file")))
    b.c.save()
    payload=b.stream.getvalue()
    reader=PdfReader(io.BytesIO(payload))
    assert len(reader.pages)==16 and len(payload)<200_000_000
    embedded=[]
    textpages=[]
    for i,page in enumerate(reader.pages):
        text=page.extract_text()
        textpages.append(text)
        assert b.records[i]["title"] in text, (i+1,"missing title")
        assert abs(float(page.mediabox.width)-WIDTH)<.1
        assert abs(float(page.mediabox.height)-HEIGHT)<.1
        for key,fontref in page["/Resources"]["/Font"].items():
            font=fontref.get_object()
            name=str(font.get("/BaseFont",""))
            if "Yahei" in name or "Microsoft" in name or "msyh" in name.lower():
                descriptor=font["/FontDescriptor"].get_object()
                assert "/FontFile2" in descriptor
                embedded.append(name)
    fulltext="\n".join(textpages)
    for phrase in ("AI 未配置","真实单次联调","闭环仿真数据，非实测", "701", "商业", "LLM_API_KEY", "末段平均偏差"):
        assert phrase in fulltext, ("missing key text",phrase)
    for key in ("rmse_t","iae","rise_time","settling_time"):
        for side in ("a","b"):
            assert f"{metrics[key][side]['value']:.6f}" in textpages[7]
    assert embedded
    assert "视觉建议" not in fulltext and "制作备注" not in fulltext and "暂未提供" not in fulltext
    OUT.parent.mkdir(parents=True,exist_ok=True)
    OUT.write_bytes(payload)
    QA.write_text(json.dumps({
        "file":str(OUT.relative_to(ROOT)),"sha256":hashlib.sha256(payload).hexdigest(),
        "pages":len(reader.pages),"bytes":len(payload),"page_size_points":[WIDTH,HEIGHT],
        "fonts_embedded":sorted(set(embedded)),"text_extraction_checked":True,
        "layout_blocks_checked":sum(len(p["blocks"]) for p in b.records),
        "text_chars_by_page":[len(t) for t in textpages],
        "content_source_sha256":hashlib.sha256(draft.encode("utf-8")).hexdigest(),
        "evidence_source":data["report_id"],"evidence_sha256":data["report_sha256"],
        "tests":audit["tests"],"page_records":b.records,
        "live_ai_evidence_sha256":hashlib.sha256((ROOT/"docs/acceptance/live-ai-summary.json").read_bytes()).hexdigest(),
        "rendering_verified":False,"visual_review_verified":False,
    },ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps({"output":str(OUT),"pages":16,"bytes":len(payload),
                      "fonts":sorted(set(embedded)),"qa":str(QA)},ensure_ascii=False))


if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output",type=Path,default=OUT)
    OUT=parser.parse_args().output.resolve()
    build()
