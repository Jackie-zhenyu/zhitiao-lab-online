# 解析函数合成示例

这三份 CSV 都是**解析函数合成数据**，不是实测数据，也不是实际闭环仿真。每一数据行都有 `source_label=解析函数合成数据`。程序来源分类使用 `simulated`，并保留上述更具体的标签。

字段统一为 `time`、`target`、`actual`、`control`、`source_label`。时间单位为 `s`，目标与实测字段代表的物理量统一声明为“响应量”，单位为 `1`；`control` 单位为 `1`。CSV 使用 UTF-8 BOM 编码和逗号分隔。示例中的 `actual` 是公式输出，字段名不意味着它来自测量。

| 示例 ID / 文件 | 内容 |
| --- | --- |
| `first_order_step` / `first_order_step.csv` | 0–6 s，间隔 0.05 s，共 121 行。1 s 前目标与响应均为 0；1 s 起目标为 1，响应为 `1-exp(-(t-1)/0.7)`，包含阶跃前基线。 |
| `irregular_sampling` / `irregular_sampling.csv` | 同一解析响应，共 121 行。固定随机种子 `20260905`，普通间隔均匀采自 0.045–0.055 s；第 60 与 61 数据行之间为 0.2 s，即名义 0.05 s 的 4 倍，供采样间隔检查与分段使用。 |
| `quality_issues` / `quality_issues.csv` | 原始间隔 0.1 s，共 61 行。第 14 行缺失 actual，第 21 行 target 非数字，第 28 行 control 为无穷，第 34 行缺失 time，第 40 行重复前一时间，第 49 行时间重置为 0，之后从该时间继续递增。行号从 1 开始且不含标题。 |

三份数据都使用 `control=0.4*target` 的解析给定输入；它不是 PID 输出，没有用于闭环求解。问题示例先生成解析值再注入数据缺陷，不代表设备故障或任何真实实验事实。

从项目根目录执行：

```powershell
.\.venv\Scripts\python.exe examples/generate.py
.\.venv\Scripts\python.exe examples/generate.py --output work/generated-examples
.\.venv\Scripts\python.exe -m pytest tests/test_examples.py -q
```

默认输出为本目录下的 `data/`。`manifest.json` 记录生成器版本、解析公式、参数、随机种子、缺陷位置、单位、行数和 CSV SHA-256；另含清单正文摘要，用于识别清单修改。生成不写入当前时间，固定版本下可重复执行且字节相同。重复执行前先检查全部目标；只有清单可以验证、现有 CSV 摘要与清单相符时才允许覆盖。任何用户修改、无归属同名文件或符号链接都会导致拒绝；不会删除文件。其他名称的文件保持原样。若要保留改动并重新生成，请用 `--output` 指定新目录。

网页通过 `examples.catalog.list_examples()` 取得元信息；`load_example(example_id, max_bytes=20*1024*1024)` 返回 `(filename, bytes, metadata_dict)`，只读原始字节，并校验其与清单的一致性，不进行解析或自动生成。读取采用统一 `read_limited` 落实字节上限。元信息包括 `quantity`、`unit`、`time_unit`、`control_unit`、`source_label`、来源分类与内容摘要。文件缺失时会提示生成命令。示例加载与用户上传都需走 `core.parser.parse_csv`，随后由用户确认映射和单位；示例元数据不会替用户跳过确认。
