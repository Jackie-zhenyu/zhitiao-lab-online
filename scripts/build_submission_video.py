"""Build an honest narrated submission video from actual local screenshots.

Run with the isolated authoring environment, not the application environment:
  work/submission-tools/.venv/Scripts/python.exe scripts/build_submission_video.py

Requirements: Windows System.Speech with an enabled zh-CN voice, Pillow 11.3.0,
imageio-ffmpeg 0.6.0. This authoring script makes no cloud TTS, network, or model
requests. Its AI screenshots document a separate, authorized real integration;
there are no application mocks. Screenshot files are read-only. The video is
explicitly a narrated, edited sequence of actual screenshots, not a live recording.
"""

from __future__ import annotations

import argparse
from array import array
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
import math
from pathlib import Path
import re
import subprocess
import sys
import wave

from PIL import Image, ImageDraw, ImageFont, ImageOps
import imageio_ffmpeg


ROOT = Path(__file__).resolve().parents[1]
WIDTH, HEIGHT = 1280, 720
VERSION = "submission-video-v2-live-ai"
FONT = Path("C:/Windows/Fonts/msyh.ttc")
BOLD_FONT = Path("C:/Windows/Fonts/msyhbd.ttc")
BG, INK, MUTED, ACCENT = "#F5F8F7", "#173F3A", "#57716C", "#13796B"
LIVE_EVIDENCE = ROOT / "docs/acceptance/live-ai-summary.json"


@dataclass(frozen=True)
class Scene:
    key: str
    title: str
    source: str
    image: str | None
    lines: tuple[str, ...]


SCENES = (
    Scene("intro", "智调 Lab · 从日志到有依据的解释", "真实界面截图与本地合成旁白 · 经剪辑", "01-home.png", (
        "这是智调实验工作台，面向大学生控制实验、电机调试和机器人实验的日志分析工具。",
        "从数据日志出发，确认单位、计算指标、比较实验，再导出可核对的报告。本次已用公开仿真示例完成一次真实模型联调。",
    )),
    Scene("mapping", "01 / 核对字段与单位", "解析函数合成数据 · 不是实测，也不是闭环仿真", "02-mapping.png", (
        "这里展示的是解析函数合成数据，不是实测。字段可以推荐，但时间和物理单位必须由用户确认。",
    )),
    Scene("analysis", "02 / 程序计算性能指标", "解析函数合成数据 · 不是实测，也不是闭环仿真", "02-analysis.png", (
        "系统按真实时间戳计算跟踪误差。确认单次阶跃和有效基线后，再计算响应指标。",
        "响应采用相对于目标值的归一化约定。数据不足时返回明确原因，不会用零填充结果。",
    )),
    Scene("quality", "03 / 先排查数据记录问题", "解析函数合成数据 · 人为构造的问题用于验证检查规则", "03-quality.png", (
        "这一份合成日志故意包含缺失、重复时间和时间倒退。问题会定位到原始行与实际区间。",
        "原始文件只读，不静默排序、填补或平滑。有阻断问题时，必须明确选择连续有效片段，不能跨断点连线分析。",
    )),
    Scene("events", "04 / 现象证据与原因分开", "实际规则结果 · 检测现象，不冒充根因诊断", "04-events.png", (
        "每张异常证据卡都给出规则、阈值、观测量和限制。用户可以聚焦对应位置，核对判断依据。",
        "持续波动不等于参数错误。没有控制限幅信息就不判断输出近限，没有合理变化率上限就只给统计突变候选。",
    )),
    Scene("comparison", "05 / 共同窗口内比较 A 与 B", "闭环仿真数据，非实测 · 同一对象与环境，只改变 PI 参数", "05-comparison.png", (
        "这两份记录来自可复现的闭环仿真，同一对象和环境条件，仅改变比例积分参数。",
        "先分别确认阶跃，再按阶跃时刻对齐，并确认共同观察时长。每侧指标仍用自己的原始时间戳计算。",
        "这个条件下，乙组的响应时间和跟踪误差更小，两侧超调相同。零分母不计算百分比，也不自动宣布某组在所有场景下更好。",
    )),
    Scene("ai", "06 / 授权后，由真实模型调用工具", "真实 Qwen 单实验联调 · 闭环仿真数据，非实测", "live-ai-tools.png", (
        "用户授权分析实验甲，范围为零到二十一秒。只发送必要配置与摘要，不发送整份日志、文件名或备注。",
        "真实模型选择数据概览、性能分析和现象检测，三个工具各执行一次。数值仍由本地程序计算。",
    )),
    Scene("ai_facts", "AI 结果：事实与候选解释分区", "本轮真实结果 · 数值由程序按结果 ID 与指标键渲染", "live-ai-facts.png", (
        "程序把结构化结果返回模型，再校验引用。这里展示本轮测得事实，数值、区间和来源都能展开核对。",
    )),
    Scene("ai_validation", "AI 证据校验：拦截并有限纠正", "本轮 3 次模型请求 · 1 次受控纠正 · 不隐去拦截记录", "live-ai-validation.png", (
        "模型第二次回复被证据校验拒绝，一次有限纠正后通过。实际共发出三次模型请求，拦截记录保留。",
        "本次只验证单实验问题。修改区间的追问，以及由模型触发的实验对比，还没有完成真实联调。",
    )),
    Scene("mechanism", "AI 工具协作机制示意", "实现机制示意，非连续调用录像 · 无密钥仍可使用本地功能", None, (
        "这张图说明实现机制，不是调用录像。应用只执行白名单函数，核对结果、区间和配置，候选解释与事实分开。",
        "改区间必须重新调用工具。系统不能执行任意代码或下发设备参数；没有密钥时，本地分析仍然可以使用。",
    )),
    Scene("report", "07 / 导出可追溯报告", "程序计算与 AI 辅助解释分区 · 无 AI 时明确标注未参与", "07-report.png", (
        "报告使用生成时冻结的结果快照，保留来源、字段单位、阈值、区间、质量问题和限制。",
        "支持带交互曲线的网页报告，以及结构化数据和指标表。没有人工智能结果时，正常导出数值报告并注明未参与。",
    )),
    Scene("project", "08 / 保存项目，下次继续", "项目包需主动下载 · 恢复时校验并重新计算", "08-project.png", (
        "项目包保存原始数据、确认配置和历史实验。恢复时先校验摘要、版本并重新计算，全部通过后才替换会话。",
        "关机前需要主动下载。密钥、人工智能授权和活动聊天证据不会跨会话恢复。",
    )),
    Scene("outro", "智调 Lab · 让每一句结论有出处", "本地可运行源码交付 · 单实验真实 AI 联调与离线测试分开记录", "01-home.png", (
        "核心指标以独立数学基准和异常输入测试验证。离线测试与真实模型联调分开记录，不把测试替身冒充在线结果。",
        "本次交付完整源码与文档。安装后运行启动脚本即可使用，用程序守住数值，用证据约束解释。",
    )),
)


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        value = sha256()
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def run(command: list[str], *, timeout: int = 600) -> subprocess.CompletedProcess:
    result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
    if result.returncode:
        raise RuntimeError(f"Command failed ({result.returncode}): {Path(command[0]).name}\n{result.stderr[-4000:]}")
    return result


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(BOLD_FONT if bold else FONT), size)


def wrap(draw: ImageDraw.ImageDraw, text: str, face: ImageFont.FreeTypeFont, max_width: int) -> list[str]:
    rows: list[str] = []
    row = ""
    for char in text:
        if row and draw.textlength(row + char, font=face) > max_width:
            rows.append(row)
            row = char
        else:
            row += char
    if row:
        rows.append(row)
    return rows


def balanced_caption(draw: ImageDraw.ImageDraw, text: str, face: ImageFont.FreeTypeFont, max_width: int) -> list[str]:
    """Balance two subtitle lines without changing text, type size, or meaning."""
    rows = wrap(draw, text, face, max_width)
    if len(rows) != 2:
        return rows
    choices = []
    for boundary in range(1, len(text)):
        left, right = text[:boundary], text[boundary:]
        if right[0] in "，。；：、！？）】》”’.,;:!?)]}":
            continue
        if left[-1].isascii() and right[0].isascii() and left[-1].isalnum() and right[0].isalnum():
            continue
        first_width = draw.textlength(left, font=face)
        second_width = draw.textlength(right, font=face)
        if max(first_width, second_width) > max_width:
            continue
        # Prefer a clause/enumeration boundary if neither line becomes a tail.
        natural = left[-1] in "，。；：、！？" and min(first_width, second_width) >= 0.55 * max(first_width, second_width)
        choices.append((abs(first_width - second_width), left, right, natural))
    if choices:
        natural_choices = [item for item in choices if item[3]]
        _, left, right, _ = min(natural_choices or choices, key=lambda item: item[0])
        rows = [left, right]
    assert "".join(rows) == text
    return rows


def center_text(draw: ImageDraw.ImageDraw, text: str, y: int, face: ImageFont.FreeTypeFont, color: str, width: int = WIDTH) -> None:
    draw.text(((width - draw.textlength(text, font=face)) / 2, y), text, fill=color, font=face)


def diagram() -> Image.Image:
    image = Image.new("RGB", (1248, 550), BG)
    draw = ImageDraw.Draw(image)
    center_text(draw, "机制示意 · 不作为本轮执行记录", 9, font(25, True), INK, 1248)
    boxes = (
        ("用户问题", "确认实验、单位与区间"),
        ("逐轮摘要授权", "默认不上传整份 CSV"),
        ("模型选择工具", "由配置决定真实模型"),
        ("白名单校验与执行", "复用本地 core 计算"),
        ("结构化结果回传", "result_id / event_id"),
        ("证据校验与展示", "数值、单位由程序渲染"),
    )
    for index, (title, body) in enumerate(boxes):
        col, row = index % 3, index // 3
        x, y = 26 + col * 410, 66 + row * 144
        draw.rounded_rectangle((x, y, x + 376, y + 112), radius=12, fill="#FFFFFF", outline="#B9D7CD", width=2)
        draw.text((x + 18, y + 15), f"0{index + 1}  {title}", font=font(23, True), fill=INK)
        draw.text((x + 18, y + 61), body, font=font(19), fill=MUTED)
        if col < 2:
            draw.text((x + 383, y + 37), "→", font=font(25, True), fill=ACCENT)
    draw.rounded_rectangle((26, 369, 1222, 442), radius=10, fill="#E0F0EA")
    center_text(draw, "profile_data     analyze_run     detect_events     compare_runs", 390, font(23, True), INK, 1248)
    center_text(draw, "改区间需要重新调用工具；拒绝旧配置、越权或不存在的证据。", 467, font(23), INK, 1248)
    return image


def screenshot_view(path: Path, crop: list[float] | None) -> Image.Image:
    with Image.open(path) as source:
        image = ImageOps.exif_transpose(source).convert("RGB")
    if crop is not None:
        if len(crop) != 4 or any(not math.isfinite(v) or not 0 <= v <= 1 for v in crop) or crop[0] >= crop[2] or crop[1] >= crop[3]:
            raise ValueError(f"Invalid normalized screenshot crop: {path.name}")
        image = image.crop(tuple(round(v * (image.width if i % 2 == 0 else image.height)) for i, v in enumerate(crop)))
    return ImageOps.contain(image, (1248, 550), Image.Resampling.LANCZOS)


def load_live_evidence() -> dict:
    """Read the public acceptance record, never server credentials or API data."""
    record = json.loads(LIVE_EVIDENCE.read_text(encoding="utf-8"))
    calls = [item["name"] for item in record["records"] if item["kind"] == "tool" and item["status"] == "success"]
    if (record["success"] is not True or sorted(calls) != ["analyze_run", "detect_events", "profile_data"]
            or record["requested_range_s"] != [0, 21]
            or record["model_requests_in_successful_turn"] != 3
            or record["output_corrections"] != 1
            or record["raw_csv_sent"] is not False
            or record["credentials_included"] is not False
            or record["results"]["analyze_run"]["tracking_interval_s"] != [0, 21]):
        raise ValueError("Public integration evidence no longer matches the narrated scope; review the script")
    if not any(item["kind"] == "validation" and item["status"] == "rejected" for item in record["records"]):
        raise ValueError("Expected rejected response history is absent from the public record")
    for metric in record["results"]["analyze_run"]["metrics"].values():
        if metric["status"] != "success" or not math.isfinite(metric["value"]):
            raise ValueError("Narrated metric must come from a successful finite result")
    return record


def live_ai_view(scene: Scene, asset_dir: Path, evidence: dict) -> Image.Image:
    """Preserve narrow real screenshots and label the separate evidence summary."""
    image = Image.new("RGB", (1248, 550), BG)
    draw = ImageDraw.Draw(image)
    names = [scene.image]
    if scene.key == "ai_facts":
        names.append("live-ai-metrics.png")
    x = 16
    for name in names:
        with Image.open(asset_dir / name) as source:
            capture = ImageOps.contain(source.convert("RGB"), (220, 506), Image.Resampling.LANCZOS)
        image.paste(capture, (x, 34))
        draw.text((x, 8), "真实界面截图", font=font(16, True), fill=MUTED)
        x += capture.width + 18
    panel_x = max(270, x + 20)
    panel_width = 1226 - panel_x
    draw.text((panel_x, 20), "本轮验收记录摘要 · 非网页重绘", font=font(24, True), fill=INK)
    draw.text((panel_x, 62), f"{evidence['model']}  ·  {evidence['source_label']}", font=font(18), fill=MUTED)
    if scene.key == "ai":
        rows = [
            ("用户授权", evidence["question"] + "；0–21 s"),
            ("实际执行工具", "profile_data  →  analyze_run  →  detect_events"),
            ("调用次数", f"{evidence['tool_calls']} 个工具，各成功执行 1 次"),
            ("数据发送边界", "必要配置、指标与事件摘要；未上传整份 CSV"),
            ("会话权限", "仅当前会话的本轮选中实验；应用注入身份"),
        ]
    elif scene.key == "ai_facts":
        result = evidence["results"]["analyze_run"]
        metrics = result["metrics"]
        def measured(key: str) -> str:
            item = metrics[key]
            return f"{item['value']:.9g} {item['unit']}"
        rows = [
            ("跟踪区间 0–21 s", f"RMSE_t  {measured('rmse_t')}    IAE  {measured('iae')}"),
            ("阶跃响应区间 1–21 s", f"上升时间  {measured('rise_time')}    超调  {measured('overshoot_pct')}"),
            ("目标调节时间", measured("settling_time") + "；仅本次观测内满足"),
            ("结果引用", result["result_id"]),
            ("记录与限制", "目标归一化；无事件不证明正常；未确认输出限值"),
        ]
    else:
        rows = [
            ("真实请求", f"{evidence['model_requests_in_successful_turn']} 次模型请求，{evidence['tool_calls']} 次工具执行"),
            ("第 2 次模型回复", "输出或引用被校验拒绝，未展示不合法原文"),
            ("第 3 次模型回复", f"{evidence['output_corrections']} 次有限纠正后，结构与本轮证据引用校验通过"),
            ("范围边界", "一次用户问题；不等于三轮用户追问"),
            ("仍未真实验证", "修改区间追问、模型触发 compare_runs"),
        ]
    y = 112
    for heading, body in rows:
        draw.text((panel_x, y), heading, font=font(20, True), fill=ACCENT)
        face = font(19)
        lines = wrap(draw, body, face, panel_width)
        if len(lines) > 2:
            raise ValueError("AI evidence summary exceeds its readable panel")
        for line_index, line in enumerate(lines):
            draw.text((panel_x, y + 27 + line_index * 25), line, font=face, fill=INK)
        y += 65 + 25 * (len(lines) - 1)
    if y > 545:
        raise ValueError("AI evidence summary exceeds the frame")
    return image


def render_frame(scene: Scene, caption: str, chapter: int, asset_dir: Path, output: Path, crop: list[float] | None, evidence: dict) -> None:
    image = Image.new("RGB", (WIDTH, HEIGHT), BG)
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, WIDTH, 53), fill=INK)
    draw.text((22, 9), scene.title, font=font(25, True), fill="#FFFFFF")
    label = f"{chapter:02d} / {len(SCENES):02d}"
    draw.text((WIDTH - 23 - draw.textlength(label, font=font(19)), 16), label, font=font(19), fill="#BFD8D0")
    if scene.key in {"ai", "ai_facts", "ai_validation"}:
        if crop:
            raise ValueError("The real AI screenshots must remain intact in this evidence layout")
        view = live_ai_view(scene, asset_dir, evidence)
    else:
        view = diagram() if scene.image is None else screenshot_view(asset_dir / scene.image, crop)
    image.paste(view, ((WIDTH - view.width) // 2, 59 + (550 - view.height) // 2))
    draw.rectangle((0, 613, WIDTH, HEIGHT), fill="#FFFFFF")
    draw.line((22, 613, WIDTH - 22, 613), fill="#CEE2DC", width=2)
    face = font(25)
    rows = balanced_caption(draw, caption, face, WIDTH - 66)
    if len(rows) > 2:
        face = font(23)
        rows = balanced_caption(draw, caption, face, WIDTH - 66)
    if len(rows) > 2:
        raise ValueError(f"Caption too long to remain readable: {caption}")
    for offset, row in enumerate(rows):
        center_text(draw, row, 620 + offset * 32, face, INK)
    center_text(draw, scene.source + (" · 页面局部" if crop else ""), 693, font(14), MUTED)
    image.save(output)


def create_speech(jobs: list[dict], work_dir: Path, rate: int) -> dict:
    json_path = work_dir / "tts-input.json"
    json_path.write_text(json.dumps(jobs, ensure_ascii=False, indent=2), encoding="utf-8")
    ps_path = work_dir / "synthesize-local.ps1"
    ps_path.write_text(r'''param([string]$JobFile, [int]$SpeechRate)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Speech
$speaker = New-Object System.Speech.Synthesis.SpeechSynthesizer
try {
    $voice = $speaker.GetInstalledVoices() | Where-Object { $_.Enabled -and $_.VoiceInfo.Culture.Name -eq 'zh-CN' } | Select-Object -First 1
    if ($null -eq $voice) { throw 'No enabled local zh-CN System.Speech voice.' }
    $speaker.SelectVoice($voice.VoiceInfo.Name)
    $speaker.Rate = $SpeechRate
    $speaker.Volume = 100
    $jobs = Get-Content -LiteralPath $JobFile -Raw -Encoding UTF8 | ConvertFrom-Json
    foreach ($job in $jobs) {
        $speaker.SetOutputToWaveFile($job.output)
        $speaker.Speak($job.text)
        $speaker.SetOutputToNull()
    }
    @{ voice = $voice.VoiceInfo.Name; culture = $voice.VoiceInfo.Culture.Name; rate = $SpeechRate; engine = 'Windows System.Speech, local' } | ConvertTo-Json -Compress
} finally {
    $speaker.Dispose()
}
''', encoding="utf-8-sig")
    result = run(["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(ps_path), "-JobFile", str(json_path), "-SpeechRate", str(rate)], timeout=300)
    # The override affects only this child process. No system policy is changed.
    return json.loads(result.stdout.strip().splitlines()[-1])


def combine_audio(jobs: list[dict], destination: Path) -> list[float]:
    durations = []
    expected = None
    with wave.open(str(destination), "wb") as joined:
        for job in jobs:
            with wave.open(job["output"], "rb") as source:
                params = (source.getnchannels(), source.getsampwidth(), source.getframerate())
                if expected is None:
                    expected = params
                    joined.setnchannels(params[0])
                    joined.setsampwidth(params[1])
                    joined.setframerate(params[2])
                elif params != expected:
                    raise ValueError("TTS produced inconsistent PCM formats")
                frames = source.getnframes()
                joined.writeframes(source.readframes(frames))
                pause = round(0.18 * params[2])
                joined.writeframes(bytes(pause * params[0] * params[1]))
                durations.append((frames + pause) / params[2])
    return durations


def pcm_statistics(path: Path) -> dict:
    """Measure actual local speech samples; this is not a human listening check."""
    with wave.open(str(path), "rb") as source:
        if source.getsampwidth() != 2 or source.getcomptype() != "NONE":
            raise ValueError("Expected 16-bit uncompressed local TTS WAV")
        samples = array("h", source.readframes(source.getnframes()))
        if sys.byteorder != "little":
            samples.byteswap()
        rate, channels, frames = source.getframerate(), source.getnchannels(), source.getnframes()
    if not samples:
        raise ValueError("Local TTS returned an empty WAV")
    peak = max(abs(value) for value in samples)
    rms = math.sqrt(sum(value * value for value in samples) / len(samples))
    nonzero = sum(value != 0 for value in samples)
    if peak == 0 or rms == 0:
        raise ValueError("Local TTS returned silent WAV")
    return {
        "sample_rate_hz": rate, "channels": channels, "sample_width_bits": 16,
        "duration_s": frames / rate, "sample_count": len(samples),
        "peak_pcm": peak, "peak_dbfs": 20 * math.log10(peak / 32768),
        "rms_pcm": rms, "rms_dbfs": 20 * math.log10(rms / 32768),
        "nonzero_sample_count": nonzero, "nonzero_fraction": nonzero / len(samples),
        "full_scale_sample_count": sum(abs(value) >= 32767 for value in samples),
        "non_silent": True, "human_listening_verified": False,
    }


def extract_inspection_frames(ffmpeg: str, video: Path, frame_records: list[dict], directory: Path) -> list[dict]:
    directory.mkdir(parents=True, exist_ok=True)
    inspections = []
    for name in ("intro", "analysis", "comparison", "ai", "ai_facts", "ai_validation", "mechanism", "report", "project", "outro"):
        frame = next(item for item in frame_records if f"-{name}-" in item["file"])
        at = frame["start_s"] + min(1.0, frame["duration_s"] / 2)
        target = directory / f"{name}.png"
        run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{at:.6f}", "-i", str(video), "-frames:v", "1", str(target)])
        inspections.append({"chapter": name, "time_s": at, "path": str(target), "sha256": digest(target)})
    return inspections


def timecode(value: float) -> str:
    millis = round(value * 1000)
    hours, millis = divmod(millis, 3_600_000)
    minutes, millis = divmod(millis, 60_000)
    seconds, millis = divmod(millis, 1000)
    return f"{hours:02}:{minutes:02}:{seconds:02},{millis:03}"


def mp4_boxes(path: Path) -> list[str]:
    """Read top-level atom names to verify that fast-start really was written."""
    kinds = []
    end = path.stat().st_size
    with path.open("rb") as source:
        while source.tell() < end:
            start = source.tell()
            header = source.read(8)
            if len(header) != 8:
                raise ValueError("Truncated MP4 box header")
            size = int.from_bytes(header[:4], "big")
            kind = header[4:].decode("ascii", errors="strict")
            minimum = 8
            if size == 1:
                extended = source.read(8)
                if len(extended) != 8:
                    raise ValueError("Truncated extended MP4 box header")
                size = int.from_bytes(extended, "big")
                minimum = 16
            elif size == 0:
                size = end - start
            if size < minimum or start + size > end:
                raise ValueError("Invalid MP4 box extent")
            kinds.append(kind)
            source.seek(start + size)
    return kinds


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--assets", type=Path, default=ROOT / "work/submission-assets")
    parser.add_argument("--work-dir", type=Path, default=ROOT / "work/submission-video")
    parser.add_argument("--output", type=Path, default=ROOT / "outputs/submission/智调Lab-演示视频.mp4")
    parser.add_argument("--voice-rate", type=int, choices=range(-2, 4), default=1)
    parser.add_argument("--prepare-only", action="store_true", help="Synthesize local narration and metadata without requiring screenshots or encoding video")
    args = parser.parse_args()
    args.assets, args.work_dir, args.output = args.assets.resolve(), args.work_dir.resolve(), args.output.resolve()
    if sys.platform != "win32":
        raise RuntimeError("This authoring script requires Windows System.Speech; the application itself is separate.")
    if not FONT.is_file() or not BOLD_FONT.is_file():
        raise RuntimeError("Microsoft YaHei fonts are required for Chinese frame rendering")
    if args.output.exists() and not args.prepare_only:
        raise FileExistsError(f"Refusing to overwrite an existing deliverable: {args.output}")
    if ROOT not in args.work_dir.parents:
        raise ValueError("Authoring work directory must stay within this project")
    marker = args.work_dir / "build-owner.json"
    if args.work_dir.exists() and any(args.work_dir.iterdir()) and not marker.exists():
        raise ValueError("Non-empty work directory has no authoring ownership marker; use a new directory")
    args.work_dir.mkdir(parents=True, exist_ok=True)
    if marker.exists() and json.loads(marker.read_text(encoding="utf-8")).get("version") != VERSION:
        raise ValueError("Work directory belongs to another authoring version")
    marker.write_text(json.dumps({"version": VERSION}, ensure_ascii=False), encoding="utf-8")
    required = sorted({scene.image for scene in SCENES if scene.image} | {"live-ai-metrics.png"})
    evidence = load_live_evidence()
    if not args.prepare_only:
        missing = [name for name in required if not (args.assets / name).is_file()]
        if missing:
            raise FileNotFoundError("Missing actual screenshot assets: " + ", ".join(missing))
    crop_file = args.assets / "video-crops.json"
    crops = json.loads(crop_file.read_text(encoding="utf-8")) if crop_file.exists() else {}
    if not isinstance(crops, dict) or any(key not in required for key in crops):
        raise ValueError("Crop config must map known screenshot filenames to normalized rectangles")
    jobs = []
    for scene_index, scene in enumerate(SCENES, 1):
        for line_index, line in enumerate(scene.lines, 1):
            key = f"{scene_index:02}-{scene.key}-{line_index:02}"
            jobs.append({"key": key, "chapter": scene_index, "scene": scene.key, "text": line, "output": str(args.work_dir / f"{key}.wav")})
    speech = create_speech(jobs, args.work_dir, args.voice_rate)
    audio_path = args.work_dir / "narration.wav"
    durations = combine_audio(jobs, audio_path)
    audio_measurements = pcm_statistics(audio_path)
    total = sum(durations)
    if total >= 280:
        raise ValueError(f"Actual speech is {total:.2f} s; shorten narration or explicitly choose a faster local voice rate")
    (args.work_dir / "narration-metadata.json").write_text(json.dumps({"voice": speech, "duration_s": total, "chunks": [{**job, "duration_s": duration} for job, duration in zip(jobs, durations)]}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Local narration: {speech['voice']}; actual duration {total:.2f} s; {len(jobs)} captioned clips", flush=True)
    if args.prepare_only:
        return 0
    concat_lines = []
    srt_lines = []
    clock = 0.0
    frame_records = []
    for index, (job, duration) in enumerate(zip(jobs, durations), 1):
        scene = SCENES[job["chapter"] - 1]
        frame_path = args.work_dir / f"{job['key']}.png"
        render_frame(scene, job["text"], job["chapter"], args.assets, frame_path, crops.get(scene.image), evidence)
        # Frame names contain no user input or escaping-sensitive characters.
        concat_lines.extend((f"file '{frame_path.name}'", f"duration {duration:.9f}"))
        srt_lines.extend((str(index), f"{timecode(clock)} --> {timecode(clock + duration)}", job["text"], ""))
        frame_records.append({"file": frame_path.name, "sha256": digest(frame_path), "start_s": clock, "duration_s": duration})
        clock += duration
    concat_lines.append(f"file '{frame_path.name}'")
    concat_path = args.work_dir / "frames.txt"
    concat_path.write_text("\n".join(concat_lines) + "\n", encoding="utf-8")
    subtitles_path = args.work_dir / "智调Lab-中文字幕.srt"
    subtitles_path.write_text("\n".join(srt_lines), encoding="utf-8")
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    partial_path = args.work_dir / "encoded-video.mp4"
    encode = run([ffmpeg, "-hide_banner", "-y", "-f", "concat", "-safe", "0", "-i", str(concat_path), "-i", str(audio_path), "-map", "0:v:0", "-map", "1:a:0", "-vf", "fps=24,format=yuv420p", "-c:v", "libx264", "-preset", "medium", "-crf", "22", "-profile:v", "high", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart", "-t", f"{total:.9f}", str(partial_path)], timeout=900)
    (args.work_dir / "encode.log").write_text(encode.stderr, encoding="utf-8")
    decode = run([ffmpeg, "-hide_banner", "-v", "info", "-i", str(partial_path), "-map", "0:v:0", "-map", "0:a:0", "-f", "null", "-"], timeout=900)
    (args.work_dir / "decode-verification.log").write_text(decode.stderr, encoding="utf-8")
    duration_match = re.search(r"Duration: (\d+):(\d+):([\d.]+)", decode.stderr)
    if not duration_match:
        raise RuntimeError("Unable to verify encoded duration from ffmpeg decoder")
    actual_duration = int(duration_match[1]) * 3600 + int(duration_match[2]) * 60 + float(duration_match[3])
    video_line = next((line.strip() for line in decode.stderr.splitlines() if "Stream #0:0" in line and "Video:" in line), "")
    audio_line = next((line.strip() for line in decode.stderr.splitlines() if "Stream #0:1" in line and "Audio:" in line), "")
    if not all(token in video_line for token in ("h264", "yuv420p", "1280x720")) or "aac" not in audio_line:
        raise RuntimeError(f"Unexpected actual encoded media format: {video_line}; {audio_line}")
    if actual_duration >= 280 or partial_path.stat().st_size >= 200_000_000:
        raise RuntimeError("Encoded video exceeds submission duration or size target")
    boxes = mp4_boxes(partial_path)
    if not {"ftyp", "moov", "mdat"}.issubset(boxes) or boxes.index("moov") > boxes.index("mdat"):
        raise RuntimeError("Encoded MP4 lacks verified fast-start atom order")
    # Complete full decoding before promoting the newly created deliverable.
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if args.output.exists():
        raise FileExistsError("Deliverable appeared during encoding; refusing to replace it")
    with partial_path.open("rb") as source, args.output.open("xb") as target:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            target.write(block)
    report = {
        "version": VERSION,
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "artifact": str(args.output),
        "bytes": args.output.stat().st_size,
        "sha256": digest(args.output),
        "duration_s": actual_duration,
        "size": [WIDTH, HEIGHT],
        "video_stream": video_line,
        "audio_stream": audio_line,
        "full_decode_exit_code": decode.returncode,
        "mp4_top_level_boxes": boxes,
        "fast_start_verified": True,
        "voice": speech,
        "narration_pcm_measurements": audio_measurements,
        "narration_chunks_all_non_silent": all(pcm_statistics(Path(job["output"]))["non_silent"] for job in jobs),
        "speech_is_local_synthesis": True,
        "video_type": "Actual application screenshots with captions and local narration; edited still-image demonstration, not continuous screen recording",
        "ai_status": "One authorized real Qwen single-experiment integration completed: profile_data, analyze_run and detect_events each executed once; three model requests with one rejected evidence response and one bounded correction. Real follow-up/range changes and model-triggered compare_runs remain unverified.",
        "ai_evidence_source": {"file": str(LIVE_EVIDENCE.relative_to(ROOT)), "sha256": digest(LIVE_EVIDENCE)},
        "source_screenshots": [{"file": name, "sha256": digest(args.assets / name), "crop": crops.get(name)} for name in required],
        "frames": frame_records,
        "subtitles": str(subtitles_path),
        "ffmpeg": str(ffmpeg),
        "encoding_command": ["libx264", "crf=22", "24fps", "yuv420p", "AAC 96k", "+faststart"],
    }
    report["inspection_frames"] = extract_inspection_frames(ffmpeg, args.output, frame_records, args.work_dir / "inspection")
    report_path = args.work_dir / "video-verification.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(args.output), "duration_s": actual_duration, "bytes": report["bytes"], "full_decode_exit_code": 0, "verification": str(report_path)}, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
