# PI A/B 闭环仿真示例

两份 CSV 的所有数据行均明确标注 **闭环仿真数据，非实测**。对象状态由控制器输出驱动递推，没有人工改画或拼接响应曲线，也未预设 A 或 B 的性能优劣。两组只改变 PI 的 `kp`、`ki`，性能差异须由项目既有指标程序计算。

| 参数 | 共用设置 |
| --- | --- |
| 对象 | `dy/dt=(-y+u)/tau`，`tau=1 s`，初始 `y=0` |
| 时间 | `0–21 s`，固定 `h=0.02 s`，含首末时刻，共 1051 行 |
| 目标 | `t<1 s` 时为 0；`t>=1 s` 时为 1 |
| 输出上下限 | `[-2.5, 2.5]` |
| 初始积分项 | 0 |
| 负载、扰动、测量延迟 | 均为 0 |
| 噪声 | 无噪声；记录种子 `20260906`，本版本不调用随机数发生器 |
| 响应量、控制及 P/I 项单位 | `1`；时间单位 `s`；`kp` 单位 `1`、`ki` 单位 `1/s` |

A（`closed_loop_pi_a.csv`）使用 `kp=0.6, ki=0.4`；B（`closed_loop_pi_b.csv`）使用 `kp=2, ki=2`。这两组参数是教学对照用手工选择值，没有通过指标反向挑选或调整曲线。

每个采样时刻按照以下固定顺序执行：

1. 读取对象状态 `y[k]` 与当前目标 `r[k]`，计算 `e[k]=r[k]-y[k]`。
2. 计算 `p_term[k]=kp*e[k]`。
3. **先更新积分**：`i_term[k]=i_term[k-1]+ki*h*e[k]`。
4. 计算并限幅 `control[k]=clip(p_term[k]+i_term[k], -2.5, 2.5)`。
5. 记录当前时刻的目标、实际状态、限幅后控制输出及限幅前 P/I 分量。
6. 在下一个采样区间内保持 `control[k]`，按精确零阶保持解更新对象：`y[k+1]=exp(-h/tau)*y[k]+(1-exp(-h/tau))*control[k]`。

这里没有抗积分饱和，输出限幅时积分项仍更新；不使用微分项，也不对控制参数做在线调整。最后一行记录该时刻算出的控制输出，但不推进到记录终点之外。

CSV 列为 `time,target,actual,control,p_term,i_term,source_label`，使用 UTF-8 BOM 与逗号分隔。状态和控制值以 17 位有效数字写入，时间以 8 位小数写入。`manifest.json` 记录方程、控制顺序、全部共用条件、两组 PI 参数、参数来源说明、版本、来源标签、单位和 SHA-256。清单正文摘要用于识别清单的内容变更。

从项目根目录执行：

```powershell
.\.venv\Scripts\python.exe examples/closed_loop.py
.\.venv\Scripts\python.exe examples/closed_loop.py --output work/closed-loop-copy
.\.venv\Scripts\python.exe -m pytest tests/test_closed_loop.py -q
```

默认生成到 `examples/closed_loop_data/`；同一代码版本重复执行得到相同字节。写入前检查所有同名目标，只覆盖本生成器清单可以识别且摘要相符的旧文件。用户改动、未知同名文件、损坏清单和符号链接会导致拒绝；不会删除文件。要保留已有改动并重新生成，可使用 `--output` 指向新目录。

`list_closed_loop_examples()` 返回新的元信息列表；`load_closed_loop_example(id, max_bytes=...)` 有界读取并验证原始字节，返回 `(filename, bytes, metadata)`。文件缺失时提示生成命令，不自动生成。元信息包含与已有示例一致的字段、单位和来源，以及 `conditions`、`output_limits`、`optional_units` 和两组 PI 参数。加载后仍须通过统一 CSV 解析、字段/单位确认与质量检查流程，不能凭示例标签跳过用户确认。
