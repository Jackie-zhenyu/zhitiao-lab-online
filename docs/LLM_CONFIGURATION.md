# 通义千问配置与接口核实 · 第五阶段

核实日期：2026-09-06。实际项目环境为 Python 3.12.14，OpenAI Python SDK **2.54.0**、httpx **0.28.1**、python-dotenv **1.2.3**。使用 SDK 的兼容接口访问阿里云百炼，不访问 OpenAI 模型服务；无 DashScope SDK、LangChain 或远程代码工具。

## 官方依据和地域

采用非流式 `client.chat.completions.create`：请求携带 `tools`、`tool_choice="auto"`，读取 `tool_calls`，由应用执行白名单函数，以 `role="tool"` 和对应 `tool_call_id` 回传结构化结果，再继续模型轮次。按官方非思考示例传 `extra_body={"enable_thinking": False}`；不发送或保存思维链。[官方 Function Calling 文档](https://help.aliyun.com/zh/model-studio/qwen-function-calling)

官方支持北京业务空间地址 `https://{WorkspaceId}.cn-beijing.maas.aliyuncs.com/compatible-mode/v1`；将 `{WorkspaceId}` 替换成控制台实际业务空间ID。北京旧地址 `https://dashscope.aliyuncs.com/compatible-mode/v1` 仍可用，因此作为 `.env.example` 的可编辑示例。Key必须属于对应地域。[官方兼容接口与域名说明](https://help.aliyun.com/zh/model-studio/compatibility-of-openai-with-dashscope)

核实当日 `qwen-plus` 的北京能力表标明支持 Function Calling；同页新加坡、法兰克福和弗吉尼亚的相应条目标为不支持。不能把模型名相同视为各地域能力一致。示例选北京 `qwen-plus`；业务逻辑没有模型默认值，也不会自动切换到其他模型。若更换模型/地域，请核对其当前能力表、非思考 tools 支持及账号权限。[官方 qwen-plus 能力表](https://help.aliyun.com/zh/model-studio/qwen-plus)

适配器只接受已核实的百炼HTTPS目的地址：北京/新加坡/东京业务空间域名，以及 `dashscope.aliyuncs.com`、`dashscope-intl.aliyuncs.com`、`dashscope-us.aliyuncs.com`。路径必须为 `/compatible-mode/v1`，拒绝用户信息、查询参数、片段、其他端口及任意第三方代理地址。地址在白名单内不代表该地域的任意模型可用。禁止跟随重定向；不继承HTTP代理或 `OPENAI_*` 的目的地址/凭据设置。

## 本地配置

应用只读取项目根目录 `.env` 和服务端环境变量。环境变量优先，包括显式空值；不搜索父目录，不扩展 `${...}`，不改写系统环境；`.env` 最多64KiB，须UTF-8/BOM。没有Key、字段缺失或配置无效时显示 **AI 未配置**，本地所有分析仍可使用。

在项目根目录的 PowerShell 中，仅在文件不存在时复制：

```powershell
if (-not (Test-Path -LiteralPath .env)) { Copy-Item -LiteralPath .env.example -Destination .env }
```

用本地编辑器填写以下四项，再启动或重启应用；**不要把真实Key发到聊天中**：

| 字段 | 含义 |
| --- | --- |
| `LLM_PROVIDER` | 当前仅支持 `qwen` |
| `LLM_BASE_URL` | 账号地域对应的官方兼容接口根地址 |
| `LLM_MODEL` | 控制台实际可用且支持 tools 的模型ID，必填 |
| `LLM_API_KEY` | 只在本地 `.env` 或服务器环境填写 |

`.env` 已被Git忽略；不要把值填进 `.env.example`。本阶段不读取 `.streamlit/secrets.toml`。服务端配置不放进聊天消息或浏览器输入控件；授权预览只含去掉Key的公开配置。错误仅报告固定类型/字段名，SDK请求调试日志被过滤，不展示原始异常、响应头或请求正文。

从控制台的密钥复制操作取得完整值，不要手工复制带星号、圆点或省略号的隐藏显示。密钥只填在本机配置中并保存；“配置已读取”仅说明字段通过本地校验，不代表远程认证成功。“模型服务拒绝认证或当前账号无权访问”统一表示认证/访问拒绝，不能单凭这句固定提示确定唯一原因；先核对完整密钥、对应地域和业务空间，再核对账号模型权限。不要把密钥或包含密钥的截图发到聊天中。

## 预算配置

| 可选环境变量 | 默认值 | 允许范围 |
| --- | ---: | --- |
| `LLM_MAX_ROUNDS` | 6 | 1–20 个模型逻辑轮次 |
| `LLM_MAX_TOOL_CALLS` | 8 | 1–32 次工具尝试，非法调用也计数 |
| `LLM_REQUEST_TIMEOUT_S` | 30 | (0,120] 秒，每次模型尝试 |
| `LLM_TOTAL_TIMEOUT_S` | 90 | (0,300] 秒，本轮总预算 |
| `LLM_MAX_RETRIES` | 1 | 0–3，每个模型逻辑轮次的额外尝试 |
| `LLM_MAX_CORRECTIONS` | 1 | 0–2，输出结构/证据纠正次数，占用轮次预算 |
| `LLM_MAX_HISTORY_TURNS` | 4 | 0–20 个通过校验且上下文一致的历史问答 |
| `LLM_MAX_OUTPUT_TOKENS` | 2000 | 1–8192 |
| `LLM_MAX_CONTEXT_CHARS` | 30000 | 1–100000，发送前序列化messages+tools字符数 |
| `LLM_MAX_TOOL_RESULT_CHARS` | 12000 | 1–30000，每份结果摘要字符数 |
| `LLM_MAX_EVENTS` | 20 | 1–100，每份摘要最多事件数 |

SDK内部重试为0，仅应用对超时、连接、限流、暂时服务错误进行有限重试，认证或参数错误不重试。耗尽次数/时间即停止，显示实际记录。事件摘要超长时减少事件并记录省略数量；非事件结构仍超长则明确拒绝发送，绝不截断成错误JSON。上下文过大需缩小范围或减少历史。

模型等待有独立墙钟截止，迟到响应无法执行工具或修改会话；底层HTTP请求仍由其自身超时关闭，不能保证云端已停止计费。原有本地数值函数同步执行，调用前后检查总预算，不能在计算中途强制杀死线程；超预算的结果不再发往云端。没有自动无限重试或后台续跑。

## 联调状态

**2026-09-06 已通过一次单实验真实联调。** 用户在本地填写配置，并明确授权向北京 `qwen-plus` 发送问题“分析实验A”及公开 PI A 闭环仿真0–21秒的必要摘要。通过正式页面执行了3次模型请求/响应，模型选择的 `profile_data`、`analyze_run`、`detect_events` 各实际执行1次；第二次模型响应的证据校验不通过，应用进行1次有限纠正，第三次响应通过结构与证据校验。数值由实际工具结果渲染，没有使用测试替身或上传整份CSV。可携带记录见 [live-ai-summary.json](acceptance/live-ai-summary.json)。

此前一次已授权请求在认证阶段失败，未执行任何工具或生成答案；修正本地配置、重新授权后才进行上述成功运行。两次操作共4次实际模型请求，成功运行内部为3次；这些请求不计入701项离线测试。测试中的 `ScriptedProvider` 与 `httpx.MockTransport` 仍仅用于自动测试。

本次只验证一个问题和一个公开仿真实验；**真实多轮区间追问、AI 发起的 `compare_runs`、其他地域/模型、结构化输出服从率及普遍问答质量仍未验证**。单次成功不代表后续请求必然成功或账号长期可用，也不代表真实设备实验验收通过。

真实最小联调：配置后加载一个合成示例并确认字段，选择AI实验A，输入“分析实验A”，核对目的地址/摘要范围，勾选授权并点击发送。成功需看到真实模型请求记录、至少一次 `analyze_run` 成功记录、后续模型响应及通过校验的 `result_id / metric_key`。若仅澄清、降级或报错，不能记录为完整联调通过。详情见 [Agent执行与验收](AGENT.md)。
