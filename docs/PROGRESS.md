# 开发进度

## 真实 AI 单实验联调补充（2026-09-06）

用户在本机填写模型配置，授权发送“分析实验A”及公开 PI A 闭环仿真0–21秒的必要摘要。首次请求在认证阶段失败，未执行工具或生成回答；用户修正完整密钥并明确重新授权后，通过正式浏览器页面完成一次真实分析。没有读取或上传私人实验，不在文档、日志、报告或交付源码中记录密钥。

- 服务为北京 `qwen-plus`，继续使用项目原有 OpenAI Python SDK 2.54.0 的 Chat Completions `tools` 接口；没有修改模型或数值计算代码、安装新依赖或绕过证据校验。
- 成功运行实际有3次模型请求及3次响应。第一轮模型发起并完成 `profile_data`、`analyze_run`、`detect_events` 各1次；后两者明确请求0–21秒。第二轮答案被证据校验拒绝，应用执行1次有限纠正，第三轮答案通过校验并显示程序渲染的事实。
- 数据为1051行公开闭环仿真，非实测；源SHA-256为 `15bccb267450ad1e8deefa94cbb7909aa8430f1b0542eff1cc20906cfdf2d1e3`，确认阶跃 `t0=1s,r0=0,r1=1,y0=0`。工具返回质量问题0条、规则事件0条；无事件不证明系统完全正常。
- 分析工具结果 `result-8293a637205e4589870e1b11df05b052` 的0–21秒跟踪值为 RMSE_t `0.22841650252730472`、IAE `2.495371903162585`、末段平均偏差 `0.0020176513823605205`；响应上升 `6.10661623679659s`、超调 `0%`、目标调节 `11.482420282338806s`。响应时间仍相对于t0，跟踪区间不能与A/B共同1–21秒指标混用。
- 可携带结构化记录见 [live-ai-summary.json](acceptance/live-ai-summary.json)，本轮实际工具和答案画面已用于交付材料更新。原媒体的页数、时长与截图验收属于下方历史版本，不能直接当作新版媒体已验收。
- 本轮全量离线回归使用 `.venv\Scripts\python.exe -m pytest -q --junitxml=work/pytest-live-delivery.xml`，实际 **701 passed in 73.94s**，0失败/错误/跳过，原始日志 `work/pytest-live-delivery.log`。与初版701项/212.50秒为独立完整运行，不相加，真实API仍不计入该数量。
- 新版统一交付目录为 `outputs/submission-live/`。初版PDF被查看器占用未能覆盖，因此保留整套旧 `outputs/submission/`，未删除用户正在查看的文件。新版PDF为16页、765024字节，SHA-256为 `d7fde709ace81f481a023438fd6192d3dbd36d59c60035d3b9227a1d241dbe7c`，350个文本块边界检查通过；修改页1/9/10/14/16重新渲染并逐页查看，其余11页PNG像素与原已逐页验收版一致，第14页显示本轮701项/73.94秒。新版MP4为266.58秒（4分26.58秒）、5321767字节，1280×720/24fps/H.264/AAC，含12张真实截图、25段本地中文旁白，全片解码退出0、10章成片抽帧已查看，人工试听未做。可携带核验记录见 [live-delivery-summary.json](acceptance/live-delivery-summary.json)，最终文件摘要由新版目录清单单独记录。
- 打包白名单更新后，相关测试实际 **22 passed in 7.83s**；`pip check` 无依赖冲突。此独立回归不与本轮701项全量测试相加，未改系统Python环境或生产计算口径。

两次已授权操作共4次实际模型请求（首次失败1次，此次成功内部3次），均独立于原701项离线测试。**本次只完成单个用户问题的真实工具循环；真实多轮区间追问、AI发起的A/B对比和其他模型/地域尚未验证。** 单例不能据以声明模型结构化输出服从率、普遍问答质量或真实设备效果。下方各阶段记录保留当时状态。

## 复赛作品交付优化（2026-09-06，本地材料已生成）

用户提供复赛提交截图并要求据此优化作品，确认暂不需要署名。按应用方案 PDF（不超过20页/200MB）、演示 MP4（不超过5分钟/200MB）和可运行源码材料整理，不新增分析指标、不重写已验收计算模块，不发布公网。

- 页面新增 `ui/demo_guide.py` 演示导览和四个公开示例快捷加载按钮；仍走既有解析流程，字段、单位和阶跃须正式确认，不生成虚假状态。新增 `tests/test_demo_guide.py`。
- `docs/SUBMISSION_CONTENT.md`、`SUBMISSION_SCRIPT.md`整理应用方案与视频分镜；`SUBMISSION.md`记录提交要求、真实实现、来源和验证边界。README新增交付导览。PDF/MP4制作与应用运行依赖分离，成品实际尺寸/页数/时长等需在生成后核对，此段不声称已完成渲染验收。
- `scripts/package_submission.py`使用精确公开文件清单，五份公开CSV固定摘要核对，不读取任意目录收集数据；原文件只读，拒绝链接/重定向、非空密钥模板和已配置凭据泄漏。默认拒绝覆盖，明确更新只覆盖三个固定源码交付产物。`docs/RUNNABLE_SUBMISSION.md`提供评委首次运行和安全交付说明。
- 最终整合全量 **701 passed in 212.50s**，0失败/错误/跳过，记录`work/pytest-submission-final.log`及XML；覆盖原672项、导览7项和打包22项。此前679项通过为打包测试加入前的中间运行；最终在同一日志/XML路径实际重新运行701项，未将独立运行数量相加。打包模块另有 **22 passed in 10.99s** 的独立验收。
- 依据阶段8汇总JSON逐项核对，13份core文件SHA-256全部保持一致。已生成源码预备包验证打包命令；最终源码和材料整理完后须重新生成交付包，预备包不代表最终材料版本。
- 可携带测试与核心保护记录写入`docs/acceptance/submission-summary.json`并加入公开打包清单。媒体制作使用独立`work/submission-tools/.venv`中的ReportLab4.4.4、pypdf6.1.1、Pillow11.3.0、imageio-ffmpeg0.6.0及本机System.Speech Huihui中文语音，生产锁依赖未改；FFmpeg二进制版本与媒体最终验收另记。

### 材料与浏览器最终核验

- 应用方案实际16页、771795字节；嵌入中文字体，348个排版文本块检查通过。Poppler渲染16页并逐页查看，修正提示框遮字和底部间距。A/B表格直接读取本次正式报告，曲线来自摘要一致的公开原始CSV。制作脚本为`scripts/build_submission_pdf.py`。
- 演示MP4实际249.34秒（4分9.34秒）、4707041字节；1280×720/24fps/H.264/AAC，FFmpeg7.1全片音视频解码退出0，验证faststart。9张真实截图、23段本地中文合成旁白，8章成片抽帧已查看；字幕均衡分行。音频非静音、无满幅样本，不声称已人工试听或连续录屏。制作脚本为`scripts/build_submission_video.py`。
- 本轮CUA真实浏览器完成解析示例加载、字段单位确认、单阶跃计算；换成问题示例后旧结果撤销，看到21条质量问题及23条现象证据，点击缺失时间事件按原始行聚焦。上传公开A/B项目包并校验恢复、显示共同20s对比、进入真实AI未配置页面、生成A/B报告与项目下载入口。报告范围第二选项在工具DOM快照中曾漏列，键盘操作与独立AppTest均确认实际状态未丢失，无需改计算模块。
- 通过正式AppTest控件生成`示例报告-闭环AB.html/json/csv`与`公开演示项目.labproj`，不是状态注入或模型替身。项目包通过正式恢复重算；本轮`pip check`无依赖冲突。全部成品位于`outputs/submission/`，媒体验收详情见本轮汇总JSON。

AI未配置/真实接口联调尚未验证的边界保留。离线替身只用于测试；不编造模型回复、设备实测、商业用户或收益。该版本不能证明完整在线智能体运行，真实配置与逐轮授权具备后需实际补录AI片段。原阶段测试记录保留。要求对照见[SUBMISSION](SUBMISSION.md)。

## 第八阶段：本地项目保存、恢复与历史管理（2026-09-06）

用户接受下一阶段建议并授权开工。已实现可下载/上传的`.labproj`包、完整核验后恢复、历史快照载入/移除及当前会话清空。保持Windows x64 / Python3.12.14 / 原项目虚拟环境，不新增依赖、数据库或分析指标；核心13份Python文件与阶段开始摘要完全一致，指标定义未改。

- `projects/archive.py`：有界ZIP_STORED与严格JSON、原始CSV摘要去重、元数据白名单、版本检查和凭据拒绝。复用原函数重新解析、准备映射、质量检查、指标、事件和A/B计算，结果一致才接受。不解压写盘、不执行包内代码。
- `ui/project_state.py`：白名单捕获与原子替换，恢复原单位、区间、基线/手工y0、规则和A/B窗口。旧会话身份、活动聊天、工具证据、待问题及授权不恢复；历史报告仅只读JSON归档。
- `ui/project_panel.py`、`app.py`：显式生成/下载、校验/确认替换、历史快照载入/删除和会话清除。页面变化后旧包保持冻结并提示。导入页保留原来源元数据，避免恢复后错误标记。Docker源清单纳入projects，Git忽略项目包，不构建或部署容器。
- 实际发现并修复：JSON排序改变字段及质量问题顺序；A/B历史报告附加字段被误拒；伪造ZIP目录计数触发提前分配；浏览器清空后旧入口、备注及文件控件残留。最终清空显式重置控件，恢复/清空为两个上传控件生成新身份，项目折叠区使用固定身份。

### 实际验证

| 项目 | 结果 |
| --- | --- |
| 最终全量 | **672 passed in 132.36s**，退出0，0失败/错误/跳过；`work/pytest-stage8-final.xml`和同名log |
| 新增测试 | 归档30、安全51、会话22、面板4，共107项；原565项保留 |
| 依赖与启动 | `pip check`无冲突；`scripts/start.cmd --check-only`成功；compileall退出0。依赖未变，本轮检查既有锁定环境，第七阶段干净安装记录保留 |
| 服务 | 通过`start.cmd`实际启动项目Python的`-m streamlit run app.py`，最终完整重启，127.0.0.1:8501健康端点200/ok；`work/streamlit-stage8-release.*.log` |
| 浏览器 | CUA真实加载解析示例、确认阶跃、保存快照并收到项目下载事件（36260字节）。另通过真实文件选择上传正式页面导出流程生成的公开PI A/B包，恢复B1051行/21s、两份快照、共同20s、上升1.07547s/调节1.93851s、基线及仿真来源。历史JSON隔离、AI未配置、清空后入口/文字/上传复位、再次上传恢复均验证；已查看桌面截图 |
| 保护检查 | 核心13文件摘要不变，Git索引为空且无远程；包、work、outputs、环境和.env均忽略，未提交/推送/购买/部署 |

原测试仅调整`test_app.py`“只能有一个上传器”的历史预期，明确断言已实现的CSV和项目两个入口均可用；没有删除正确测试或放宽数值断言。新增恢复测试曾把预期质量阻断提示误当恢复失败，现精确断言该提示。安全失败复现记录保留在`work/pytest-stage8-security-regression-before.xml`。

操作与格式见[PROJECTS](PROJECTS.md)，验收见[ACCEPTANCE](ACCEPTANCE.md)和[汇总JSON](acceptance/stage8-summary.json)。下载工具仅返回事件，无可读取的下载路径；恢复验收使用另由正式导出流程生成的包，未声称从下载目录取回同一文件。

未完成/未验证：自动保存、数据库、活动聊天恢复和跨版本迁移不实现。真实模型联调仍未验证，本轮真实API调用0次。Docker、Linux/macOS实机、物理断网重开HTML和多人压力未验证。项目包未加密，摘要不认证作者或来源真实性；超出包或控件范围的配置拒绝恢复，不静默转换。已有Streamlit控件默认值提示未阻断功能。

## 第七阶段：最终验收与本地交付整理（2026-09-06）

完整读取项目约束、需求、进度、指标定义及现有测试，按最终验收任务修复和整理，没有扩张功能或重写计算。实际环境保持 Windows x64 / Python 3.12.14 / 项目`.venv`。核心13份Python文件与阶段开始摘要一致；全部旧测试文件保持不变。METRICS只纠正报告已实现的历史状态句，数值定义未改。

### 完成内容

- 修复 `ui/evidence.py`：字段/单位/质量确认变化时恢复现象规则默认值并撤销物理限幅、变化率确认；切回旧单位不复活旧确认，同上下文跨页仍保留。五类错误先由新增回归实际复现。
- 修复 `agent/tools.py`：A/B响应证据区间从真实t0标记，跟踪仍从原生样本开始；只修引用元数据。`agent/config.py`、`ui/report_page.py`：即使模型配置不完整，仍读取已填写Key用于报告脱敏，保持AI禁用。
- 新增 `scripts/install.cmd`、`start.cmd`、`manage.py`、`check_environment.py`：检查Python3.12、独立环境、锁定版本及导入；失败明确退出，固定localhost，不打印模型配置。修复CMD错误码未传播的问题；拒绝虚拟环境路径指向项目外。未修改系统ExecutionPolicy。
- 三份依赖文件固定实际版本，完整锁文件57包。新增非root Dockerfile和默认排除的构建上下文；仅为可选本地容器交付，不部署公网。
- README完善安装/启动、CSV、示例、模型、导出、会话清除和故障处理；新增DEMO、ACCEPTANCE、DEPLOYMENT及可携带的实际验收汇总JSON。页面去掉过期阶段文案，保持三个入口和独立AI区。
- 新增最终工作流10项、证据/安全15项、启动交付16项测试，共41项；没有删除测试、放宽断言或硬编码计算结果。仍用独立数学基准验证指标。

### 实际结果

| 项目 | 结果与记录 |
| --- | --- |
| 全量基线 | 524项，0失败/错误/跳过；`work/pytest-stage7-baseline.xml` |
| 最终全量 | **565 passed in 117.75s**，退出0，0失败/错误/跳过；`work/pytest-stage7-final.xml` |
| 独立重新安装 | 空目录`work/stage7-install-clean/.venv`实际安装全部57个锁定包成功；已有项目环境安装检查也退出0；对应install日志保留 |
| 依赖与编译 | `pip check`无冲突、compileall退出0；`scripts/start.cmd --check-only`退出0 |
| 启动脚本 | 8507临时端口实际根页/健康端点200、ok、仅127.0.0.1，验证后停止；8501最终服务完整重启、两个端点200/ok |
| 浏览器 | 真实文件选择上传UTF-8 BOM/分号CSV，手动ms映射后121行/6s/0质量问题，确认阶跃；同名新内容撤销旧映射与曲线，摘要变化。问题示例23条现象证据，聚焦缺失时间行34；PI A/B独立保存、20→10→20窗口撤销及重算，A/B报告生成并实际收到三种下载事件，HTML呈现8组曲线与安全备注文字，AI无配置禁用。细节见ACCEPTANCE与汇总 |
| 数值/测试保护 | 13份core文件和所有原测试SHA-256未变；新测试覆盖文件/单位/区间/阈值失效、旧聊天证据与授权撤销、报告AI排除旧上下文、跨会话拒绝 |
| 平台 | Linux CPython3.12 x86_64 wheel解析可获取性检查通过；Linux/macOS实机未验收。Docker CLI无，未build/run |
| Git | 索引为空、无远程，候选公开示例5份匹配生成清单；没有环境、缓存、私人实验或真实凭据进入版本控制。未提交/推送/购买/公网部署 |

主要命令：`scripts/install.cmd`、`scripts/start.cmd --port 8507`、`scripts/start.cmd`、`scripts/start.cmd --check-only`、`.venv\Scripts\python.exe -m pytest -q --junitxml=work/pytest-stage7-final.xml`、`-m pip check`、`-m compileall -q app.py core ui agent reports examples tests scripts`、实际HTTP/监听核验、源码摘要与Git候选检查。安装日志在`work/stage7-existing-install.log`、`stage7-clean-install.log`，服务日志在`work/streamlit-stage7.*.log`。

### 交付与未验证边界

[ACCEPTANCE.md](ACCEPTANCE.md)列出实际测试、浏览器记录、人工步骤与未完成项；[DEMO.md](DEMO.md)提供约三分钟公开合成/仿真示例路径；[DEPLOYMENT.md](DEPLOYMENT.md)单独说明容器命令和公开演示前提；[acceptance/stage7-summary.json](acceptance/stage7-summary.json)保留实际计数、JUnit摘要和源码保护证据。

真实模型接口联调尚未验证，真实API调用0次；自动测试禁止真实socket并清空Key，测试替身不进入正式页面。Docker运行、Linux/macOS实机、物理断网后重新打开本地HTML、多用户压力和长期会话均未验证。浏览器此前拒绝file://，没有绕过；iframe自包含交互验收不能替代该项。Word/PDF、复杂指标、设备控制、登录和长期存储未实现。现有Streamlit控件默认值警告未阻断功能；代码更新后需要完整重启并重新确认数据，避免旧类混用。

默认本地可运行交付已完成；用户按README启动，未下载数据仅属当前会话，关机/重启后需重新加载。后续工作须另按明确任务确定范围。

## 第六阶段：报告导出与界面整理已完成（2026-09-06）

先读取AGENTS、需求、指标定义、进度与既有页面，继续当前目录中的工作。Python 3.12.14、Streamlit单体和项目虚拟环境保持不变；没有新增依赖、修改系统Python或重写已验收计算模块。仓库仍无提交，保留原有未跟踪文件和用户修改，没有推送或公网部署。

### 完成内容

- `reports/snapshot.py`：生成前复用既有函数核验原始摘要、映射、质量、区间、参数、指标和事件；拒绝新配置与旧结果混用。冻结规范JSON及SHA-256，三种文件和页面核对表共用同一结果，读取返回独立副本。报告超过32 MiB明确拒绝，不静默省略证据。
- `reports/generator.py`、`charts.py`：自包含HTML、严格JSON、UTF-8 BOM指标CSV；保留来源标签/摘要、单位/转换、分析与阶跃区间、阈值/初值/观察时长、算法版本、质量问题、指标/图表、异常现象、A/B条件及限制。原确认结果与共同窗口比较结果分开标记，不重新定义指标。图表最多5000个显示点，不修改分析数据。
- HTML分开“程序计算结果”“AI 辅助解释”，无AI正常导出并注明AI未参与。仅收录仍属于本会话且匹配报告实验/配置的实际AI证据；按result_id/event_id/指标键引用原工具结果，候选解释仍为受控模板。AI自己的区间和条件单独保留，不混同主表。
- HTML正文和图表文本转义、脚本内JSON编码危险字符，保留无属性换行；只内嵌一次Plotly，CSP脚本内容哈希及禁止网络连接，无CDN。白名单报告ID文件名不使用用户路径，CSV文本防公式注入而不改合法数值负数。已配置密钥精确脱敏及常见疑似Key处理，不导出服务端配置、完整原始CSV或SDK日志。
- `ui/report_page.py`：当前实验/已确认A/B范围、报告名称备注、明确生成、三格式冻结下载、指标/配置核对及HTML预览。配置、数据、AI上下文或备注变化时提示旧快照，明确重新生成；未确认阶跃、仅质量问题和无AI均有状态。报告草稿离页保留。
- `app.py`与UI组件：左侧集中实验/字段/单位/选段/配置，主区质量/指标/图与异常证据分开，顶端独立AI区域，保留三个入口。浅色统一强调色、窄屏换行/侧栏折叠、表格局部滚动。实际窄屏检查修正固定顶栏遮挡标签、报告时刻换行及工具栏覆盖图标题的问题。
- 新增报告与页面33项测试，更新既有证据图测试的语义定位，未删除测试；更新README、SPEC、DATA_CONTRACT并新增 `docs/REPORT_FORMAT.md`。通过真实本地示例流程生成 `outputs/智调Lab-闭环AB示例.html/.json/.csv`，闭环仿真、非实测、AI未参与。

### 最终实际验收

| 检查 | 实际结果 |
| --- | --- |
| 全量pytest | **524 passed in 29.19s**，退出0，0失败/错误/跳过；原491项加新增33项；`work/pytest-stage6.xml` |
| 报告/安全测试 | 中文、缺失指标、无AI、质量报告、冻结副本、新旧映射/区间/配置/数据拒绝、A/B窗口/零分母、实际AI引用及失效、模型文本拒绝、恶意备注/标签/脚本结束、CSP哈希/单份脚本、CSV公式/合法负数、凭据脱敏、安全文件名均通过 |
| AppTest | 真实示例确认→指标→保存A/B→对比→三格式导出；页面/JSON/CSV逐值及配置一致、修改配置保留旧快照、草稿离页保留及再次生成、质量结果导出、左侧控件和独立AI区域通过 |
| 核心模块保护 | **全部13份core/*.py与docs/METRICS.md，共14份SHA-256与阶段开始一致**；`work/stage6-core-verification.json` |
| 依赖/语法 | `pip check`无依赖冲突，`compileall`退出0；本阶段无需安装新依赖 |
| 服务 | 最终代码已完整重启；127.0.0.1:8501根页面和健康端点均200，正文ok；`work/stage6-service-verification.json` |
| 实际浏览器 | 解析示例121行、6s、无质量问题；确认单阶跃，上升1.5379358184631735s、超调0%、目标调节2.7387319132867596s，连同四项跟踪值与HTML一致。AI独立区域无配置明确禁用。三种按钮均实际收到下载事件 |
| 恶意备注浏览器验收 | 脚本结束标签、script/img/onerror作为文字显示；仅2个预期script、0额外img/onerror节点、无攻击标记和弹窗 |
| 图表与窄屏浏览器验收 | 实际查看桌面及390×844截图；页面scrollWidth=390、报告正文339/339，无横向溢出。实际缩放刻度由0/2/4/6变为2/3/4，复位恢复，工具栏不盖标题；临时视口已恢复，控制台无错误 |
| 自包含资源浏览器验收 | 报告iframe的chartsReady=true，1份内联Plotly、0外部资源链接，CSP禁止网络连接且图表正常交互 |
| 完全离线文件重开 | **待手动复核**：浏览器安全策略禁止file://访问；未绕过、未用额外HTTP服务器转发文件。没有执行系统物理断网并重新打开下载HTML，不能将iframe验证写成该项完成 |
| 真实模型 | **接口联调尚未验证**；本次未提供真实Key/云端授权，没有真实API调用，自动测试仍为明确测试替身 |

主要命令：`.venv\Scripts\python.exe -m pytest -q --junitxml=work/pytest-stage6.xml`、针对reports/report_ui的pytest、`-m pip check`、`-m compileall -q app.py core ui agent reports examples tests`、`python work/verify_stage6.py`、`-m streamlit run app.py`，以及PowerShell文件摘要和本地HTTP健康核对。实际浏览器记录在 `work/stage6-browser-verification.json`，示例产物记录在 `work/stage6-artifact-verification.json`，完整文件清单/命令/手动步骤在 `outputs/第六阶段验收记录.md`。

### 仍有的限制

用户需下载HTML后断网重开，手动确认其本机浏览器的交互行为。Word/PDF、签名鉴定、长期结果持久化、跨平台和多用户压力测试未实现/未验收。窗口或条件不一致的A/B仍只作带限制的描述性比较，现象不诊断根因；显示抽点可能遗漏尖峰。JSON超过32 MiB拒绝生成。

既有Streamlit默认值与Session State同时设置的开发警告仍存在，功能验收正常；新增预览采用当前已安装版本支持的st.iframe。开发中热重载曾保留旧类/旧样式，导致生成被一致性检查拒绝；最终已完整重启并重新导入确认，正常生成和下载。以后更新代码也建议完整重启。原始数据和未下载快照仅属当前会话，关机/重启后需重新加载。

## 第五阶段：代码及离线验收已完成，真实接口联调尚未验证（2026-09-06）

本阶段先读取AGENTS、SPEC、METRICS、已有分析接口与本进度。关机后核查并继续已保存代码，保留此前文件和用户修改。继续使用项目 `.venv` 的Python 3.12.14；新增依赖实际已安装：OpenAI SDK2.54.0、httpx0.28.1、python-dotenv1.2.3。未修改系统Python，未提交、推送或部署公网。

### 已完成

- `agent/config.py`、`provider.py`：读取服务端四项LLM配置和有界预算，按阿里云官方文档核实OpenAI兼容Chat Completions tools接口。模型必须从配置读取；示例北京qwen-plus有官方Function Calling能力依据，地域能力不能混同。禁止任意目的地址/重定向，不继承其他供应商凭据，固定错误消息，过滤SDK请求调试日志。配置与官方来源见 `docs/LLM_CONFIGURATION.md`。
- `agent/tools.py`：四个实际只读工具profile_data、analyze_run、detect_events、compare_runs；参数Pydantic校验，白名单之外无执行入口。复用原核心函数重算，区间按原始有效时间点，确认阶跃和配置不能由模型绕过，A/B只在用户允许共同窗口内计算。
- `agent/models.py`、`privacy.py`：应用注入会话身份，只允许本轮选中A/B；原始摘要、准备数据、映射、指标/规则配置、区间、条件变化使证据失效。result_id/event_id/指标键绑定本会话、本轮和当前指纹。摘要排除CSV原始行、文件名、备注、任意原列名、条件原文和路径。
- `agent/runner.py`：实际模型选择工具→校验执行→回传role=tool→模型结构回答；有限模型/工具/重试/纠正/超时预算，规范参数和调用ID重复拦截。每轮重新计算证据，明确2～5秒等范围变化时拒绝旧区间事实。格式和证据错误有限纠正后降级，迟到模型响应不能执行工具。
- `agent/presentation.py`：第一版受控解释/验证文案，模型只选择类别和证据引用；数字、单位、状态、差值和比例来自程序，拒绝任意自由事实或设备参数。候选解释与事实区分，近限和记录问题解释另检查对应事件/计数证据。
- `ui/agent_context.py`、`agent_panel.py`及app入口：聊天、A/B选择、共同窗口、仅本地的条件输入、逐请求摘要预览及授权、真实执行记录、证据展开。勾选本身不发送；每轮点发送后才构造真实Qwen请求；没有模型替身的正式UI开关。无配置时明确AI未配置，本地能力继续工作。来源/条件/规则/区间改变或选择歧义均撤销旧上下文与授权。
- `ui/session.py`、`comparison.py`：快照保存时另存确认EventConfig，移除时同步清理；AI条件不改变原快照。更新旧页面占位说明、README、SPEC、数据契约、配置样例、依赖与锁定快照；报告仍未实现。详细执行和测试说明见 `docs/AGENT.md`。

### 最终实际验收

| 检查 | 实际结果 |
| --- | --- |
| 全量pytest | **491 passed in 24.56s**，退出0，失败0、错误0、跳过0；`work/pytest-stage5.xml` |
| 回归与新增 | 原第四阶段406项保留通过；新增85项工具/循环/SDK/页面测试。旧“无密钥禁用输入”断言更新为聊天控件，无删除测试掩盖问题 |
| 工具/循环 | 真实本地函数返回数值、多轮范围重算、A/B原始点计算、自比零差值/零分母、非法工具/参数、越权ID、伪造指标/事件、旧会话/旧配置/旧轮次、循环、超时迟到、重试/轮次/上下文预算、有限格式纠正通过 |
| SDK离线协议 | 用真实OpenAI SDK加MockTransport验证请求URL、配置模型、tools、非思考参数、错误映射、无SDK重试/重定向、超时、截断响应及DEBUG日志脱敏。仅为离线协议测试 |
| 页面AppTest | 无密钥加载/质量/曲线、草稿预览、勾选不发送、逐轮授权、新区间新结果、快照A/B窗口、条件/规则变化撤销、选择歧义清理、会话隔离、问题凭据拒绝均通过。模型为明确测试替身 |
| 数值口径保持 | `core/metrics.py`、`metric_models.py`、`performance.py`、`events.py`、`comparison.py`和`docs/METRICS.md`六份SHA-256与阶段开始完全相同；`work/stage5-numerical-verification.json` |
| 依赖/语法 | `pip check`无冲突，`compileall`退出0；锁定快照由当前虚拟环境pip freeze生成 |
| 实际本地服务 | 关机后已重新启动；仅监听127.0.0.1:8501，根页面和`/_stcore/health`均HTTP200，健康正文ok；`work/stage5-service-verification.json` |
| 实际浏览器 | 新会话加载一阶解析示例121行，确认字段/单位后显示无阻断性质量问题、五条统计突变候选和三条曲线；确认单阶跃后上升1.53794s、超调0%、目标调节2.73873s，误差带/时刻标记可见；AI未配置且聊天/发送禁用 |
| 真实云模型 | **接口联调尚未验证**。实际检查四项LLM配置均缺失，没有真实Key或本轮发送授权，没有发起真实模型请求。不能把MockTransport/AppTest通过写成真实API通过 |

浏览器一阶示例是“解析函数合成数据”，时间常数0.7s、t0=1s，其正常快速动态命中统计突变候选不代表故障；不是第三阶段tau=1独立理论基准，也不是实测或闭环仿真。已有独立理论数值与闭环PI测试全部随全量回归通过。

实际浏览器验收记录另存 `work/stage5-browser-verification.json`。服务日志仍有此前已记录的Streamlit控件默认值与Session State同时设置警告，页面流程正常；本阶段未宣称修复该开发期警告或热重载问题。

本次主要命令：`.venv\Scripts\python.exe -m pytest -q --junitxml=work/pytest-stage5.xml`、针对四个Agent测试文件的pytest、`-m pip check`、`-m compileall -q app.py core ui agent reports examples tests`、从当前虚拟环境执行pip freeze更新锁文件、`-m streamlit run app.py`，以及PowerShell的Get-FileHash和本地HTTP健康检查。完整交付与手动步骤见 `outputs/第五阶段验收记录.md`。

### 未完成与限制

真实账号权限、地域连接、模型格式服从率和实际问答质量均待用户本地配置并授权最小联调；应用配置已读取也不代表服务验证通过。首版为有限解释模板，无法回答任意开放式控制理论问题。复杂自然语言多区间仍需澄清，不能保证识别任意时间表述。新范围未包含完整确认阶跃时仅计算跟踪指标。

模型等待超时后丢弃迟到结果；不保证取消云端已开始计费的推理。本地既有同步数值函数只在调用前后检查总预算，不能中途强制终止，但超预算后不继续发送模型。未测试真实设备、跨平台、生产部署或多用户压力；没有报告导出、PID自动下发、模型训练。关机/重启后会话数据不持久化，需重新加载；代码/模型变更建议完整重启以避免原有Streamlit热重载同名类兼容问题。

## 第四阶段：已完成（2026-09-06）

已先读取AGENTS、METRICS、SPEC、PROGRESS与既有代码；沿用Python 3.12.14项目虚拟环境，未增加依赖或修改系统Python。Git仍为本地未提交、文件未跟踪状态，没有远程配置；未删除既有修改、提交、推送或部署公网。

### 完成内容

- `core/event_models.py`、`core/events.py`：四类可解释现象证据，含实验/事件ID、原始行和可确定的秒时间、规则、阈值、观测量、关联图表、未确定原因、限制、来源与版本。动态规则逐连续有效片段计算，不跨断点；持续时长按原始时间差，时间占比按左端状态乘实际dt。详见 `docs/EVIDENCE.md`。
- `ui/evidence.py`：当前值/默认值/来源表，限幅与物理变化率独立确认，规则适用性和中文观测量说明、每页10张卡片及聚焦图。未知时间改按原始行定位；聚焦不更改性能选段。每片最多1000个显示点、原始记录预览最多100行，明确只影响显示。略超阈值的显示自适应提高精度，避免舍入成“1超过1”。
- `core/comparison_models.py`、`core/comparison.py`：核验原始SHA、映射、质量、单阶跃和重算结果后保存独立快照；会话内最多8份。各侧按t−t0对齐，须确认共同观察时长，分别从原始点重算七项既有指标，保留各侧实际边界；不插入对齐计算点。检查物理量、单位、r0/r1/y0、评价配置、对象/负载/采样/环境/控制器记录。详见 `docs/COMPARISON.md`。
- `ui/comparison.py`：分别选择已确认快照、填写条件、确认共同窗口，展示可比性、原始实际区间、七指标B−A差值/百分比及目标/实测、误差对齐曲线。条件/窗口/选择变化清除旧结果；物理量或单位不同分图且不算无意义差值，零分母百分比为空。
- `examples/closed_loop.py`、`examples/CLOSED_LOOP.md`及两份CSV/manifest：可重复的离散PI闭环，对象精确ZOH更新；同一对象、目标、初始条件、0.02s周期、限幅、无噪声规则及种子20260906，仅A的kp=.6/ki=.4与B的kp=2/ki=2不同。全部标注“闭环仿真数据，非实测”；生成器拒绝覆盖被用户改动的产物。两次运行字节一致，与旧解析合成示例分别维护。
- 新旧示例均通过原有有界CSV解析和字段/单位确认，没有示例分析捷径。实验和快照只在当前会话保存。README、SPEC、DATA_CONTRACT、新规则/对比文档和本进度已更新；AI、频域/多变量分析、根因诊断和报告生成仍未实现。

### 最终实际验收

| 检查 | 结果 |
| --- | --- |
| 全量pytest | **406 passed in 27.88s**，退出码0，0失败、0跳过；`work/pytest-stage4.xml` |
| 新增测试 | 事件29、对比35、闭环生成12、独立数值/边界验收15、页面8、独立证据视图6，共105项；第三阶段301项全部保留通过 |
| 核心边界 | 真实事件起止、时间占比、缺失限幅、负向阶跃、单位/配置/窗口不同、原生非等间隔、断点、零分母、自比、快照/结果失效、溢出和确认边界均覆盖 |
| 页面AppTest | 两侧示例实际导入/确认阶跃/保存/比较、共同窗口必须确认、未知条件警告、选项变化失效、自比、删除快照、会话隔离、证据聚焦不改变指标和原数据通过 |
| 指标口径不变 | `core/metrics.py`、`core/metric_models.py`、`docs/METRICS.md`及两个原独立指标测试文件SHA-256与阶段开始完全一致；核对记录在`work/stage4-metric-verification.json` |
| 依赖/语法 | `pip check`无冲突；Python3.12.14；`compileall`通过；未新增依赖 |
| 独立A/B执行 | `work/verify_stage4_ab.py`实际退出0，完整原始CSV→解析/映射→质量→基线/阶跃→快照→共同20s；完整数值在`work/stage4-ab-reference.json` |
| 本地服务 | 最终服务只监听127.0.0.1:8501；`/`与`/_stcore/health`均HTTP200，健康正文ok。实际记录在`work/stage4-service-verification.json` |
| 实际浏览器 | 完整重启后在新会话分别加载PI A/B、确认映射/基线/单阶跃、保存两份快照、确认20s并计算成功。表内七指标与独立脚本一致，原始行51–1051，t−t0横轴和两组曲线可见，零超调百分比为空，PID归因限制明确 |
| 证据交互 | 实际加载“数据质量问题”，显示23条现象证据；聚焦第34行时间缺失卡，显示原始行图及记录表、明确未确定原因；图表Zoom in / Reset axes可用。返回对比页后两份独立快照与结果保留 |

两侧共同请求时长20s，实际指标数据行51–1051、绝对时间1–21s、相对时间0–20s。响应采用原有**相对于目标值的归一化约定**，不是标准stepinfo；目标调节只表示在本次观测区间内满足。

| 指标 | A | B |
| --- | ---: | ---: |
| 时间加权RMSE / 1 | 0.232986703 | 0.110327156 |
| IAE / 1·s | 2.485371903 | 0.490000000 |
| 最大绝对误差 / 1 | 1 | 1 |
| 末段平均偏差 / 1 | 0.001983440 | 1.64105e-10 |
| 上升时间 / s | 6.106616237 | 1.075474539 |
| 超调量 / % | 0 | 0 |
| 目标调节时间 / s | 11.482420282 | 1.938511545 |

这组记录里B响应更快、跟踪误差更小，最大绝对误差与超调没有变化；不宣布绝对优胜。超调差值为0，变化百分比因分母A=0保持为空。A/B默认事件规则各0条；独立脚本明确确认输出限值后也各0条，不证明系统完全正常，脚本确认不会替网页用户确认。

### 限制与后续范围

规则阈值须结合对象核对；波动现象不等于不稳定性、频谱结论或PID根因。没有control/确认限幅不判断接近饱和；本阶段不确认积分饱和。没有合理物理变化率确认只给统计候选，真实阶跃动态也可能被标记。条件是用户或示例记录，未独立验证；未知/不同条件只做带限制的描述性对比。不同采样时刻可能使实际末点不同，按原始点分别计算并给警告，不假造相同窗口。未测试真实设备、多用户负载或部署；未接AI、未做报告导出。

开发中修改代码触发的Streamlit热重载再次复现同名Pydantic旧/新类实例不兼容。完整停止服务并启动新会话后上述浏览器流程通过；README明确模型或代码变更后重启。本地日志另有既有“控件默认值和Session State同时设置”的警告，不是运行失败；没有声称热更新已修复。

## 第三阶段：已完成（2026-09-06）

已先读取 AGENTS、SPEC、PROGRESS 和已有代码。`docs/METRICS.md` 原为占位文件，已先更新正式定义，再实现指标配置、独立计算、阶跃确认和页面展示。继续使用项目 Python 3.12.14；`pip check` 无依赖冲突，未变更系统环境。Git 仍为本地未提交状态，未清理已有文件。

### 完成内容与定义

- 独立 `core/metrics.py`：RMSE_t=`sqrt(∫e²dt/(tb-ta))`、IAE=`∫abs(e)dt`，对实际时间戳上的被积样本做复合梯形积分；采样最大绝对误差、默认末尾10%时间窗口的平均偏差。末段起点线性求积分边界，不是普通样本均值，不称为稳态误差。
- 响应采用**相对于目标值的归一化约定**：q=(actual-y0)/(r1-y0)。上升时间默认q首次0.1至0.9的交点差；超调=max(0,max(q)-1)×100%；目标带宽=max(0.02×abs(r1-y0),绝对容差)，最终入带时刻减t0且后续至少观察0.5s。参数可配置并保留。
- `core/steps.py`：保守候选识别、多阶跃独立观察段、有效阶跃前基线估计。候选须用户确认，无法可靠识别时允许明确手动指定。无基线时用户须提供y0，不默认零，不跨其他阶跃借基线。
- `core/metric_models.py`、`core/performance.py`：独立配置/结果结构，记录实验、来源摘要、映射、一般选段与单阶跃区间、阈值、初值、基线原始行/时间/方法、交点、观察时长及版本。逐指标返回成功、不适用、观测内未达到、数据不足或数据异常，非成功值为None。
- `ui/performance.py`、会话及页面：四项跟踪/三项响应卡片和结果表；每项可展开定义、状态、配置及来源。选段、参数、初值或候选改变撤销旧阶跃确认；导航保留当前会话。核心计算没有写入页面回调。
- `ui/charts.py`：目标/实测曲线在响应观察段内标记上下阈值、目标误差带、t0、阈值与最终入带时刻；显示抽点不影响全量指标。复核后错开相邻时刻文字以提高可读性。
- README、METRICS、SPEC、DATA_CONTRACT、报告入口说明与验收记录均明确：**不是按实测稳态值归一化的标准stepinfo结果**；目标调节时间只表示**在本次观测区间内满足**，不保证未来不再离带。报告生成仍未实现。

### 最终实际验收

| 检查 | 结果 |
| --- | --- |
| 全量 pytest | **301 passed in 11.40s**，退出码0，0失败、0跳过；`work/pytest-stage3.xml` |
| 新增测试 | 独立数值44、指标边界33、候选/基线47、指标页面9、多阶跃页面2，共135项；第二阶段166项保留并按第三阶段页面能力更新必要断言 |
| 数值独立性 | 理论值直接由ln(9)、-ln(.02)及手算梯形面积生成，不调用被测函数构造期望；正负阶跃、非零初值、非等间隔、静态、波动、未达、短记录、断点均覆盖 |
| AppTest | 完整指标、手工y0必填、配置/选段失效、来源/基线追溯、导航会话、图表标记、双阶跃分开计算和拒绝手工混段均通过 |
| 依赖与编译 | Python3.12.14；`pip check`无冲突；`compileall`通过，未新增依赖 |
| 实际服务 | 本地Streamlit成功启动，`/`及`/_stcore/health`均HTTP200，健康正文ok；仅监听127.0.0.1:8501 |
| 实际浏览器 | 示例加载→单位确认→四项跟踪指标→候选与基线确认→三项响应指标成功；展开说明显示阈值、基线行号、原始摘要及观察时长，曲线误差带和时刻标记可见，复位可用 |

独立解析基准τ=1s、h=.001s、t0=0，6501点（−.5至6s）的实际记录：

| 指标 | 实际值 | 理论值 | 绝对误差 |
| --- | --- | --- | --- |
| 上升时间 | 2.197224583434949s | ln(9)=2.1972245773362196s | 6.10×10⁻⁹s |
| 目标调节时间 | 3.912023016668022s | -ln(.02)=3.912023005428146s | 1.12×10⁻⁸s |
| 超调 | 0% | 0% | 0 |
| t=[0,1,3],e=[0,2,2] 的RMSE_t | 1.8257418583505538 | sqrt(10/3) | 0 |
| 同一非等间隔案例的IAE | 5 | 5 | 0 |

指数交点容差依据h²设为10⁻⁶s，没有任意放宽。数值记录在 `work/metrics-reference-stage3.json`。这些全为解析函数合成或数学测试夹具，不是实测，也不声称真实闭环仿真。

浏览器内已有示例的τ是0.7s、t0=1s，与上面的τ=1验收基准不同：实际显示上升1.53794s、目标调节2.73873s、超调0%；一般跟踪选段0–6s，RMSE_t≈0.250198、IAE≈0.724744、最大绝对误差1、末段平均偏差≈0.00125147（响应量单位1）。

### 修复与命令记录

独立数值复核发现默认2%目标带边界（例如0.98）会因浮点相减误判为带外，已改为有限带端点的闭区间比较，未放宽容差。集成复核补齐完整结果中的基线原始行与方法，并修复极大有限数值的交点分母溢出产生伪零问题；现在中间差值不有限时返回数据异常。均有回归覆盖，没有删除失败测试。

```powershell
Get-Content AGENTS.md, docs/SPEC.md, docs/PROGRESS.md, docs/METRICS.md -Encoding UTF8
git status --short --branch
.\.venv\Scripts\python.exe --version
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -m pytest tests/test_metrics_numerics.py -q
.\.venv\Scripts\python.exe -m pytest tests/test_metrics_edges.py tests/test_metrics_numerics.py -q
.\.venv\Scripts\python.exe -m pytest tests/test_steps.py tests/test_performance_multistep.py -q
.\.venv\Scripts\python.exe -m pytest tests/test_performance_ui.py tests/test_app.py -q
.\.venv\Scripts\python.exe -m pytest -q --junitxml=work/pytest-stage3.xml
.\.venv\Scripts\python.exe work/verify_stage3.py
.\.venv\Scripts\python.exe -m compileall -q app.py core ui agent reports examples tests
.\.venv\Scripts\python.exe -m streamlit run app.py
Invoke-WebRequest -Uri 'http://127.0.0.1:8501/' -UseBasicParsing
Invoke-WebRequest -Uri 'http://127.0.0.1:8501/_stcore/health' -UseBasicParsing
```

后台服务通过隐藏窗口Start-Process执行启动命令，重启前验证进程属于本项目。PID记录在 `work/streamlit.pid`，日志在 `work/streamlit-stage3.stdout.log`、`work/streamlit-stage3.stderr.log`。控件恢复仍可能产生第二阶段已记录的默认值/Session State框架警告。开发代码热重载复现过旧会话Pydantic类与新类不匹配，交付前已重启并在新会话验收；修改代码后应按README重启，当前不保证开发热重载跨类版本保留确认。

### 仍不能可靠处理与未实现

- 自动候选不适合持续斜坡、容差内难区分的小阶跃、噪声/漂移平台；手动确认也不能把持续变化目标或多个阶跃混算成单阶跃。
- 没有可信基线时需要明确手工y0；真实触发若在记录前不外推。候选t0分辨率受采样间隔限制。
- 欠采样和样本之间未捕获的峰值/离带仍不能推断；入带连续性采用相邻采样间线性假设，不能保证未来。质量规则未识别的丢包无法仅凭指标证实。
- 近零幅值、未达到阈值、观察不足、断点和数值溢出按原因给状态；不输出伪造0/NaN/无限大。末段偏差不是稳定性证据。
- AI、报告生成/导出、实验对比、频域/多变量指标、设备异常诊断、实时控制和自动参数下发仍未实现。
- 仅验收Windows/Python3.12.14本地环境；未验收其他系统、移动端、多进程或高并发。未修改系统环境、未提交/推送Git、未部署公网。

## 第二阶段：已完成（2026-09-06）

已读取 AGENTS.md、SPEC.md、本进度及现有代码。继续使用项目 Python 3.12.14 虚拟环境；现有第一阶段文件保留并在授权范围修改，Git 仍为本地未提交状态。

本次范围：有读取限制的 CSV 解析、显式字段/单位确认、质量检查与连续有效区间、基础曲线、解析函数合成示例、会话隔离和失效管理。下方第一阶段内容为历史记录，不代表当前功能状态。

### 完成内容

- `core/parser.py`：UTF-8/BOM 自动解析、手动编码和三种分隔符；歧义明确报错。默认 20 MiB / 200000 行；分块读取最多上限加一个探测字节，逐条检查记录数量，不无限读取后补检查。保留原始字节、字符串、列名、物理行位置与 SHA-256。
- `core/import_models.py`、`core/models.py`：导入、映射、质量和连续区间契约。原始列名精确保留空白，避免把 `actual` 与 ` actual` 混为同一信号。推荐映射须明确确认，目标/实测使用用户确认的共同单位，时间仅按 s/ms 转换。
- `core/quality.py`：非数值、缺失、无穷、重复、倒退、疑似重置、采样间隔检查及位置定位；展示真实行数、时长和间隔统计。阻断时须选择至少两行的连续有效片段，禁止跨问题行或异常间隔；选择还会复核实验身份和有效性。
- `ui/session.py`：每个会话持有独立原始字节与结果；同名不同内容、映射、时间单位、物理单位、解析或质量配置变化都会撤销失效结果。页面切换保留实验、已确认配置和选段；上传控件重建后仍可使用已保留字节或明确清除。
- `ui/import_page.py`、`ui/charts.py`：导入、示例、确认、质量、选段和 Plotly 曲线。目标/实测、误差及可选控制输出均有单位和区间；等索引抽点只操作显示副本，记录显示规则。
- `examples/`：三份可重复生成的“解析函数合成数据”，附公式、参数、种子和 SHA-256 清单；包括阶跃前基线、非等间隔和数据质量问题。下载与一键加载走同一解析流程；生成器拒绝覆盖用户改动。
- 更新需求、数据契约、解析/质量规则、README 和测试。第一阶段模型测试保留；旧页面断言按第二阶段能力调整，并新增集成与回归测试。

### 实际验收结果

| 检查 | 实际结果 |
| --- | --- |
| Python / 依赖 | 沿用项目 `.venv` 的 Python 3.12.14；`pip check` 无冲突，未安装系统包；Streamlit 最低依赖改为 1.63，与已安装版本相符 |
| 全量 pytest | **166 passed in 5.78s**，退出码 0；0 失败、0 跳过，JUnit：`work/pytest-stage2.xml` |
| 测试构成 | 原有模型 25；解析 46；质量 55；示例 13；会话 6；曲线 3；原始列名集成 1；页面 17 |
| 页面自动化 | AppTest 验证三个示例的确认、质量与曲线；问题阻断/选段、配置失效、无模型启动、会话隔离、导航与上传保留/清除 |
| 示例生成 | 默认生成命令重复执行成功；测试验证确定性、摘要一致及用户文件保护 |
| 200000 行烟雾验证 | 3,888,912 字节的解析合成 CSV 走完整解析/准备/质量/选段；0 问题、1 连续区间；本机该次合计 0.9055 s（不含造数和绘图，不作为性能承诺），记录 `work/quality-smoke-200000.json` |
| 编译检查 | `compileall` 通过，退出码 0 |
| 实际服务 | 本地 Streamlit 启动成功，页面与健康检查 HTTP 200；监听 `127.0.0.1:8501` |
| 实际浏览器 | 上传 UTF-8 BOM 示例，填写单位并确认后显示 121 行、6 s、0 问题及三组曲线；一键加载同样成功，图表缩放/复位可用；导航返回保留数据与表单值 |

开发中发现并修复了未映射选项回退、带空白原始列名被规范化、质量报告可能复用到其他实验以及导航时上传/表单状态丢失的问题，均补充回归测试。最终全量测试在这些修改完成后运行，没有通过删测试掩盖失败。

### 实际执行的主要命令

除分模块 pytest 外，主要命令如下；均从项目根目录执行。

```powershell
Get-Content AGENTS.md, docs/SPEC.md, docs/PROGRESS.md -Encoding UTF8
rg --files --hidden -g '!.git' -g '!.venv'
git status --short --branch
.\.venv\Scripts\python.exe --version
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe examples/generate.py
.\.venv\Scripts\python.exe -m pytest -q --junitxml=work/pytest-stage2.xml
.\.venv\Scripts\python.exe -m compileall -q app.py core ui agent reports examples tests
.\.venv\Scripts\python.exe work/smoke_quality_200000.py
.\.venv\Scripts\python.exe -m streamlit run app.py
Invoke-WebRequest -Uri 'http://127.0.0.1:8501/' -UseBasicParsing
Invoke-WebRequest -Uri 'http://127.0.0.1:8501/_stcore/health' -UseBasicParsing
```

本次后台服务通过隐藏窗口 `Start-Process` 执行上述启动命令；启动器 PID 在 `work/streamlit.pid`，日志在 `work/streamlit-stage2.stdout.log` 与 `work/streamlit-stage2.stderr.log`。开发热重载产生过新旧 Pydantic 类身份冲突，已重启服务解决；README 提醒模型代码变化后重启。

### 未完成内容与验证边界

- AI、复杂控制指标、设备异常诊断、实验对比、报告生成/导出仍为“尚未实现”；未接模型，无 API Key 要求。处理记录只在页面/会话中可查，并不代表已实现报告功能。
- 间隔与疑似重置采用公开说明的检查规则，不等于设备故障，也不能证明物理时钟确实重置；用户可调整间隔阈值并重新确认。
- 未做自动清洗、插值、平滑、编码器换算或实时控制。显示抽点可能漏掉尖峰，缩放不自动增加点数；可手动调高显示上限。
- 会话数据不持久化；服务重启或浏览器刷新建立新会话后须重新导入。未验收多进程部署、高并发、其他操作系统、移动端或其他 Python 版本。
- 原生上传/Plotly 工具栏有少量英文。恢复会话控件时，Streamlit 日志会提示同时设置了默认值和 Session State（例如 `max_mib`）；页面运行与控件恢复已验证正常，当前保留该框架警告。实际浏览器已验证上传、示例、确认、导航和缩放；编码/边界组合、同名内容变更、三个示例的完整阻断流程主要由 pytest / AppTest 覆盖。
- 未推送远程、未提交 Git、未部署公网。下一阶段须先读取需求与进度并按新任务实施。

## 第一阶段：已完成（2026-09-05）

任务范围：项目初始化和可启动网页骨架。以 [SPEC.md](SPEC.md) 为准。

### 环境检查（2026-09-05）

- 工作目录：`C:\Users\admin\Documents\Codex\2026-09-05\lab-agent-csv-ai-python-git`。
- 现有内容仅为空的 `work/` 与 `outputs/`，未发现冲突文件或上级 AGENTS.md。
- 初始 Git 状态：不是 Git 仓库。
- `python --version` 命中了不可运行的 WindowsApps 别名；`py -3.14 --version` 返回 Python 3.14.3。
- 已使用 bundled Python 3.12.14 创建项目 `.venv`，`include-system-site-packages = false`，没有修改系统 Python。
- 已执行 `pip install -r requirements-dev.txt --log work/pip-install.log`，退出码 0；未遇到安装权限或网络限制。
- 已执行 `pip check`，返回 `No broken requirements found.`；7 个直接依赖全部实际导入成功。
- 已保存 `requirements.lock.txt`，并通过 `pip install --dry-run -r requirements.lock.txt` 检查当前环境匹配。该快照的 NumPy/SciPy 要求 Python ≥ 3.12，README 已说明 Python 3.11 需重新解析依赖。
- Git 已初始化为本地 `main` 分支，无提交、无远程配置。

### 实际依赖版本

| 依赖 | 安装版本 |
| --- | --- |
| Streamlit | 1.63.0 |
| pandas | 2.3.3 |
| NumPy | 2.5.2 |
| SciPy | 1.18.1 |
| Plotly | 6.9.0 |
| Pydantic | 2.13.5 |
| pytest | 9.1.1 |

### 本阶段完成内容

- 建立 `app.py` 与 core、ui、agent、reports、examples、tests、docs；后续模块仅职责占位，不返回伪造的分析结果。
- 中文实验工作台包含“实验分析”“实验对比”“报告预览”三个可切换入口。上传、分析、实验选择、问答和导出禁用；未实现能力明确标注。
- 建立 Experiment、AnalysisConfig、MetricResult、EventResult、AnalysisResult 及少量共享结构。保留实验 ID、来源、配置、区间、数值、单位、状态、原因和算法版本。
- 约束非成功数值为 `None`，真实成功的 0 合法；拒绝 NaN、无穷、布尔值、字符串数字、未知字段和不一致的实验 ID；事件必须处于分析区间内。
- 代码复核发现既有嵌套 Pydantic 对象可能跳过字段重校验；已设置 `revalidate_instances="always"`，并增加回归测试，明确序列化前的校验流程。
- README 提供独立环境安装、启动、测试命令；AGENTS.md 保留九项长期约束；需求、数据契约、指标约定和依赖快照齐全。
- Streamlit 本地监听 `127.0.0.1`、关闭使用统计、隐藏默认 Deploy 工具栏入口；未调用模型，无需 API Key。

### 实际验收结果

| 检查 | 结果 |
| --- | --- |
| 独立依赖安装 | 成功，退出码 0，日志在 `work/pip-install.log` |
| 依赖一致性 | `pip check`：`No broken requirements found.` |
| 7 个直接依赖实际导入 | 全部成功 |
| 数据契约测试 | 最终 25 项通过 |
| 页面测试 | 5 项通过，包括无 API Key 启动和三个入口 |
| 最终全量 pytest | **30 passed in 1.39s**，退出码 0；JUnit 记录 `work/pytest-results.xml` |
| Python 编译检查 | 通过，退出码 0 |
| 实际 Streamlit 启动 | 成功，`http://127.0.0.1:8501` |
| HTTP 检查 | `/` 返回 200；`/_stcore/health` 返回 200，正文 `ok` |
| 实际浏览器验收 | 三个入口均可切换；异常与 AI 标签均显示尚未实现；输入/操作禁用；无虚假指标或分析 |
| 页面视觉复核 | 修正顶部留白遮挡，重启后确认标题正常、默认 Deploy 入口隐藏 |
| Git | 本地 `main`，无提交、无远程；环境、密钥和运行日志已忽略 |

最终 30 项测试是在嵌套对象重校验修复后执行。此前数据契约 24 项、页面 5 项及全量 29 项也通过；新增回归测试后为 30 项，没有删除测试。

### 实际执行的主要命令

以下在项目根目录执行；服务通过隐藏窗口 `Start-Process` 启动，核心命令与 README 一致。

```powershell
Get-Location
rg --files --hidden -g '!.git' -g '!.venv'
Get-ChildItem -Force
python --version  # 初始失败：WindowsApps 别名不可运行
py -0p
py -3.14 --version
& 'C:\Users\admin\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' --version
git status --short --branch  # 初始不是仓库；初始化后再次检查
& 'C:\Users\admin\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt --log work/pip-install.log
.\.venv\Scripts\python.exe -m pip freeze
.\.venv\Scripts\python.exe -m pip install --dry-run -r requirements.lock.txt
.\.venv\Scripts\python.exe -m pip check
git init --initial-branch=main
git remote -v
.\.venv\Scripts\python.exe -m pytest tests/test_models.py
.\.venv\Scripts\python.exe -m pytest tests/test_app.py -q
.\.venv\Scripts\python.exe -m pytest -q --junitxml=work/pytest-results.xml
.\.venv\Scripts\python.exe -m compileall -q app.py core ui agent reports examples tests
.\.venv\Scripts\python.exe -m streamlit run app.py
Invoke-WebRequest -Uri 'http://127.0.0.1:8501/' -UseBasicParsing
Invoke-WebRequest -Uri 'http://127.0.0.1:8501/_stcore/health' -UseBasicParsing
Get-NetTCPConnection -LocalPort 8501 -State Listen
```

另使用 Python `importlib.metadata` 读取实际版本与 Python 约束，导入七个直接依赖，读取虚拟环境配置，并检查 `.gitignore` 规则。版本快照写入 `requirements.lock.txt` 后补充了环境说明注释。

### 运行状态与验证边界

- 交付时本地后台服务保持运行，可直接访问上述地址；启动器 PID 记录于 `work/streamlit.pid`，启动输出在 `work/streamlit.stdout.log` 和 `work/streamlit.stderr.log`。之后手动按 README 启动的前台服务可用 `Ctrl+C` 停止。
- 主界面、导航与业务提示为中文；Streamlit 原生上传控件仍显示 `Upload`、文件大小提示等少量英文。当前版本未提供该按钮文本配置，本阶段未改造框架控件。
- 本机 Windows / Python 3.12.14 已验证；Python 3.11、系统 3.14.3、Linux/macOS 和移动端尚未验收。
- 数据来源标签与指纹字段是结构约定，未读取原始文件或验证实验真实性；指标文档列出的候选指标未计算。
- 未配置模型、未安装系统包、未推送远程、未部署公网。

### 未实现

CSV 解析、字段/单位确认、曲线、指标、异常检测、实验对比、AI、报告导出和示例数据生成均不属于本阶段。

下一阶段需先读取需求与本进度，根据新任务确定范围；不自动启动上述功能开发。
