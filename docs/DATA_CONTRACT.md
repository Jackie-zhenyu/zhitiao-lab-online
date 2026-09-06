# 数据契约

第一阶段结果契约位于 `core/models.py`，导入和质量契约位于 `core/import_models.py`，性能指标契约位于 `core/metric_models.py`。第四阶段增加 `core/event_models.py` 与 `core/comparison_models.py`。结构化配置和结果使用 Pydantic v2 校验；持有 DataFrame 的导入对象和快照另有普通 dataclass 容器，完整性由对应流程检查。普通说明文本去除首尾空白后不得为空；原始列名使用 RawColumnName 精确保留空格，避免切换列或合并来源元数据。指标结果数值拒绝 NaN、无穷、布尔值和字符串数字。

## 结构和追溯关系

| 结构 | 主要字段与职责 |
| --- | --- |
| `AnalysisInterval` | `start_s`、`end_s`，相对实验时间基准的秒数闭区间；起点不得大于终点，单点合法。是否有足够样本由后续算法判定。 |
| `DataSource` | `source_id`、`experiment_id`、必填 `kind`，以及可选 `filename`、64 位十六进制 `sha256`。 |
| `Experiment` | 实验 ID、名称、来源、`raw_fields`（原始列名 → 原始单位，未知单位为 `None`）。不保存或修改原始文件内容。 |
| `AnalysisConfig` | 必填分析区间、`field_mapping`（语义字段 → 原始列名）、`confirmed_units`（语义字段 → 确认单位）、有序 `processing_rules`。确认单位必须对应已映射字段。 |
| `MetricResult` | 指标名称、状态、数值、单位、原因。实验与配置上下文由外层 `AnalysisResult` 提供。 |
| `EventResult` | 事件名称、状态、事件区间、数值证据、证据单位、原因。成功事件必须有区间与有限数值证据。 |
| `AnalysisResult` | 实验 ID、来源、完整配置、必填算法版本、整体状态与原因、指标列表、事件列表。事件区间必须位于配置的分析区间内。 |

`Experiment` 和 `AnalysisResult` 各自的实验 ID 必须与所附 `DataSource.experiment_id` 一致。独立的指标或事件不重复全部元数据；后续持久化、AI 输入和报告应传递整个结果容器，避免丢失来源和配置。

## 状态、数值和单位

- `success`：该项已成功计算；指标或事件必须有有限 `value` 和非空 `unit`。真实计算结果允许为 0。成功事件还必须有发生区间。
- `not_computable`：当前数据或配置无法计算；`value=None`，`reason` 必填。
- `not_implemented`：相应能力尚未实现；`value=None`，`reason` 必填。
- `error`：计算发生错误；`value=None`，`reason` 必填。原因不得包含密钥或其他秘密。

无法计算时，已知单位可以保留，未知单位使用 `None`；不能用 0、NaN、空字符串或编造的数值充当结果。无量纲量使用 `1`，百分比使用 `%`，时间使用 `s`。第一阶段只检查单位明确性，不校验物理量维度、换算或用户确认流程。

整体状态描述分析执行状态，子项状态描述各自的可计算性；一次成功执行可能包含 `not_computable` 指标。整体非成功也必须说明原因。没有检出的事件使用空列表；空列表本身不表示“实验正常”，也不意味着已运行检测。

## 来源、配置与版本

来源类型必须显式选择 `measured`（实测）、`simulated`（仿真）或 `unknown`（未知），不默认把来源当实测。此标签是声明，不是真实性鉴定。第一阶段测试中的 `simulated` 数据仅为契约测试夹具，不向网页展示为实验。

`sha256` 可在文件读取前为 `None`；后续解析阶段须从只读原始字节计算，不能伪造指纹。`filename` 仅用于元数据记录，不在当前阶段读取文件。配置保留字段映射、确认单位和按顺序执行的处理规则；空处理规则列表表示未声明处理操作，不授权自动填补、去重、滤波或插值。后续处理必须另行实现并明确记录参数。

`algorithm_version` 必须由调用方显式填写，表示该结果使用的计算实现版本。本阶段只使用测试专用标识验证字段，不生成实际分析结果。该字段的存在不能作为算法已实现的证据。

Pydantic 在构造及显式 `model_validate` 时校验，基类设置 `revalidate_instances="always"`，已有嵌套实例也重新校验。结构仍是普通可变 Python 对象；后续代码修改内容后，应先调用 `AnalysisResult.model_validate(result)` 完整校验，再序列化校验返回的对象。`model_dump` 本身不是校验入口。文件真实性、采样质量、字段存在性与单位一致性需由后续处理模块验证。

以上“第一阶段”“后续阶段”描述为契约建立时的历史范围。当前功能以 SPEC、PROGRESS 及下面的分阶段补充为准。

## 第二阶段导入与质量契约

- ParserOptions：严格编码、分隔符、max_bytes、max_rows。ParsedCSV：原始 bytes / SHA-256 / 文件名 / 字符串 DataFrame / 起始物理行号 / 实际格式 / 解析操作记录。
- MappingConfig：必需/可选语义列到精确原始列名的映射，s/ms、物理量、共同单位、各可选字段单位、显式确认。QualityConfig：间隔比例上下限。
- PreparedExperiment：ParsedCSV、Experiment、映射配置、独立数值表和完整转换记录；表中的 NaN 是已标记的问题值，绝不表示成功指标。
- QualityIssue：规则、1-based 数据行、CSV 物理行、字段、原因、阻断标识。QualityReport：实验 ID、原始行数、可用时长、间隔统计、全部问题、连续有效区间及检查配置。实验 ID 不匹配时禁止用旧报告选段。
- ValidInterval：闭区间起止数据行与秒数，至少两个点。select_interval 返回保持原始行索引的独立副本，并再次检查有效性及间隔阈值。
- 页面 traceability 保存来源、算法版本、配置指纹、映射/单位、质量规则、实际使用的行/时间区间、转换及显示操作。原始 bytes/数据表属于 LabSession 会话实例；无共享可变实验数据。

第二阶段这些结构本身不产生性能指标、诊断事件或 AI 结论。

## 第三阶段性能指标契约

- `MetricConfig`：末段时间比例、上下阈值、相对/绝对目标带、最短后续观察时长、近零幅值、目标平台、基线窗口/样本/跨度/峰峰差及沿用的质量间隔参数。拒绝非有限值和倒置阈值。
- `StepCandidate`：前平台起始行、阶跃行、观察截止行、t0、r0/r1、上一采样时刻。只推荐，不代表确认。
- `BaselineEstimate`：有限 y0 或失败状态/原因，实际基线起止行和时间。没有有效基线不能假定 y0=0。
- `StepSelection`：用户确认的 t0、r0/r1、y0、初值来源，以及基线实际起止时间/原始行和估计方法。手工初值明确标为 manual，不填伪基线。
- `MetricComputation`：逐指标 `MetricResult`，有限交点/阈值 markers，以及实际窗口、观察时长和算法口径 details。跟踪和响应分别容纳，避免两者区间混淆。
- `PerformanceResult`：实验/原始 SHA-256 和来源声明、映射/单位、完整分析行/时间区间、质量与指标配置、每项定义、归一化声明、算法版本、单阶跃原始行范围与初值确认、tracking/response 计算结果。必须传递整个容器才能保留追溯信息。报告功能尚未实现；后续报告不得丢弃归一化声明或把此口径称为标准 stepinfo。
- `ResultStatus` 补充 `not_applicable`（不适用）、`not_reached`（观测内未达到）、`insufficient_data`（数据不足）、`invalid_data`（数据异常）。所有非成功 `MetricResult.value` 必须为 None，并有原因；成功的实际零仍合法。
- `core/performance.py` 先通过 `select_interval` 验证来源、连续性与行范围，再调用独立计算。页面不做数值计算；会话确认键包含源、配置、选段、阶跃和初值。每次渲染重新构建当前指标结果，配置变化不复用旧响应确认。

定义与数值边界以 [METRICS.md](METRICS.md) 为准，第四阶段不改变这些指标口径。第三阶段当时尚无AI/报告；现象证据、Agent与报告分别在下文描述，仍不做根因诊断。

## 第四阶段现象证据契约

`core/event_models.py` 的现象结构独立于第一阶段通用 `EventResult`。一张现象卡可包含多种观测量，也可能只有可信原始行、没有可靠时间，不能强行压成一个成功数值或编造时间区间。

| 结构 | 主要字段与约束 |
| --- | --- |
| `OutputLimits` | 有限 `lower/upper`，必须 `lower<upper` 且 `confirmed=True`。没有默认确认值；示例清单中的限值只用于提示，仍须明确确认。 |
| `EventConfig` | 波动窗口、交替次数、峰峰差、死区、目标带、时间占比、近限带/时长，以及可选 `output_limits`、变化率上限与独立确认标志、统计规则参数。物理上限必须同时有数值和确认，未确认时只使用统计候选规则。 |
| `EvidenceEvent` | `event_id/experiment_id`、四类 category（oscillation、control_limit、measurement_change、data_record）、原始起止行、可空起止时间、rule、thresholds、observations、charts、phenomenon、undetermined_causes、limitations 和版本。起止行不得倒置；时间必须同时存在或同时为空，存在时不得倒置。 |
| `RuleCheck` | 每条规则的执行状态：checked、not_applicable、insufficient_data 或 invalid_data，以及原因。零事件与规则未执行必须区分。 |
| `EvidenceReport` | 实验 ID、完整 DataSource、EventConfig、逐规则适用性和证据列表，版本 `evidence-rules-v4.0`。空列表不代表系统完全正常。 |

事件 ID 根据实验来源、规则/质量配置、版本和原始定位生成。`detect_events` 仅在质量报告中每个连续有效片段内检查动态现象；已有质量问题逐条保留数据行、CSV 物理行和说明。时间轴缺失、重复、倒退、重置或溢出导致定位不可靠时，记录证据的 `start_s/end_s` 同时为 `None`，用原始行聚焦。

`thresholds` 和 `observations` 存放有限数值、文字、布尔值或空值；规则同时记录单位、实际窗口、所用带宽/上限和时间统计方法。持续时间使用原始首尾时间差；状态时间占比采用左端状态乘相邻 `dt`；平均误差使用实际时间梯形均值。波动、近限和统计突变只支持相应现象描述，不确认 PID 设置、积分饱和、传感器故障或稳定性。

页面并列展示当前值和默认值，标记“默认 / 与默认一致”或“用户设置 / 确认”。规则或当前实验变更会撤销旧报告与聚焦；点击证据只改变独立展示范围，不改性能分析区间。完整规则见 [EVIDENCE.md](EVIDENCE.md)。

## 第四阶段快照与 A/B 对比契约

| 结构 | 主要字段与职责 |
| --- | --- |
| `ExperimentConditions` | 可空的 plant、load、sampling、environment、controller 条件记录。空值或未知文字不补成已知事实，控制器参数允许不同。 |
| `SavedExperiment` | dataclass：snapshot_id、name、PreparedExperiment、QualityReport、PerformanceResult 和条件。`capture_experiment` 校验后深复制，保存于当前 LabSession；它不是磁盘持久化或冻结对象。 |
| `ComparisonConfig` | 正的有限共同请求时长 `common_duration_s` 和必填 `confirmed=True`。不能超出任一已确认阶跃段。 |
| `CompatibilityCheck` | 检查名称、match/mismatch/unknown 状态和具体原因，覆盖物理量、单位、阶跃参考、评价参数、条件和实际原生窗口。 |
| `ComparisonSide` | 快照名称/ID、来源和声明、物理量/单位、阶跃/指标配置、条件、原始起止行、绝对和相对首尾时间，以及重新计算的 tracking/response。 |
| `MetricDifference` | 指标键和名称、两侧原始 MetricResult、可空 delta/percent 及原因；保留两侧状态与单位，不只保存差值。 |
| `ComparisonResult` | comparison_id、两侧完整结果、共同配置、checks、differences、warnings、descriptive_only、can_overlay、既有归一化声明及版本 `step-comparison-v4.0`。 |

保存和比较流程检查原始字节 SHA-256、原始字符串表/行位置、准备数据、质量配置及报告、原有 PerformanceResult 与重新计算结果的一致性。保存时深复制所有对象；之后工作实验发生变化不改写已保存快照。快照自身被改写而身份摘要不符时拒绝继续比较。快照身份包含来源、名称、解析/映射/质量配置、指标/阶跃和条件；共同窗口或条件变化生成新的比较身份并撤销旧页面结果。

对齐只使用各自 `t−t0`。默认可用时长是两份已确认阶跃记录的较小者，仍须确认。响应计算保留各自阶跃前基线；跟踪计算只用各自 `t>=t0` 的原始点，截到 `t<=t0+common_duration_s`，至少两个阶跃后原始点。不为共同截止时间人工插入计算点。不同采样周期或非等间隔造成的实际首尾偏差必须记录并提示；`reference_interval_s` 保留为各自数据属性，不因它不同就判定用户评价口径不同。

物理量、单位、r0/r1/y0 或用户评价参数不一致时，显示两侧值及警告，delta 和 percent 均为空。单位/物理量不同使 `can_overlay=False`；不做隐式单位换算。其他条件未知或不一致时允许带限制的描述性差值，但不作 PID 因果归因。`descriptive_only` 表示额外的可比性限制；该标记为 False 也不授权综合评分、绝对优胜或因果结论。

数值差值为 `B−A`；百分比为 `100*(B−A)/abs(A)`。A=0 时允许保留有效差值，但 percent 必须为空并说明分母无效；任一指标不可用或两侧口径不同，两项均为空。差值溢出则两项为空，仅百分比溢出时可保留有限差值。自比成功指标的差值为真实 0；正负末段偏差的符号变化不能单独解释为性能改善。完整口径见 [COMPARISON.md](COMPARISON.md)。

闭环示例的 DataSource.kind 仍为 simulated，另完整保留 **“闭环仿真数据，非实测”** 声明、生成版本、PI 参数和全部共用条件；原有“解析函数合成数据”使用独立目录和清单。两类示例均走统一解析、字段/单位、质量和阶跃确认流程，不替用户默认确认。事件、快照、比较及原始数据仅属于当前会话；报告传递完整容器，AI接口由第五阶段增加，报告导出由第六阶段增加。

## 第五阶段Agent边界

`AgentContext`含应用注入的session_id、本轮选中A/B及深复制实验视图、配置指纹、共同窗口上限和会话证据字典。会话身份不进入模型工具schema。`ToolExperiment`复用准备数据/质量/指标/事件配置与确认快照；保存快照时另存确认的EventConfig，原有数值容器不变。

`EvidenceRecord`在实际工具完成后生成result_id，并绑定session_id、context_key、request_id、实验ID、原始/配置指纹、工具摘要、可引用指标和事件。`EvidenceRef`必须且只能选择metric_key或event_id之一。引用必须存在，属于本轮本会话，并与当前数据及全部分析配置一致；变更后拒绝旧引用。

`AgentAnswer`含measured_facts、candidate_explanations、next_verification、limitations、evidence_references和可选clarification。事实只允许引用，禁止模型提供value、unit或自由事实文字。候选解释/验证/限制为受控类别，文案和全部数值由程序渲染；结构错误有限纠正后降级。`AgentTurn`保存真实状态、验证后答案、实际ExecutionRecord和result_ids，不保存无效模型原文、思维链、密钥或SDK敏感异常。完整协议与局限见 [AGENT.md](AGENT.md)。

## 第六阶段报告快照

`ReportSnapshot`为冻结dataclass，只持规范JSON字符串和SHA-256；每次读取返回独立副本。生成前复用原函数核验数据、配置、性能和事件一致性；原core数据模型和指标算法不变。`ExportBundle`持有同一快照生成的HTML、JSON字节和CSV字节，存在当前Streamlit会话中，不缓存跨用户可变数据。

报告版本 `lab-report-v6.0`，含report_id、生成UTC时间、项目/备注、runs、comparison、ai与限制。每个run含完整来源、映射/单位、质量/性能/事件容器、转换记录、结果和配置摘要；图表仅保存最多5000点的显示数据，不嵌入完整原始CSV。AI引用须经会话、实际工具证据和当前实验配置复核后才能纳入，不能只复制模型文本。

`metric_rows`是三格式和报告页面的共同投影，数值来自既有结果，含结果身份/指标键/区间/配置，空值保留状态和原因。A/B原确认范围与共同窗口结果分别标记，差值使用既有比较结果。具体JSON字段、安全边界、大小限制与导出流程见 [REPORT_FORMAT.md](REPORT_FORMAT.md)。
