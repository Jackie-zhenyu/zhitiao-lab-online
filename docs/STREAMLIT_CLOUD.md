# 智调 Lab · Streamlit Community Cloud 部署候选

更新日期：2026-09-06。用户选择提供在线体验链接，当前正在准备官方托管所需材料。**状态：部署说明已准备，尚未部署成功，尚无可提交的在线URL。** 本地启动、离线测试及北京 `qwen-plus` 单实验联调的历史验收不等于云端运行验证，详见 [ACCEPTANCE](ACCEPTANCE.md)。

本次保持 Streamlit 单体应用和已有计算口径，不重写核心模块。已验收的 `outputs/submission-live/` 材料完整保留为历史交付版本；云部署候选另放 `outputs/cloud-deployment/`，不直接覆盖旧方案、视频或源码包。

## 准备部署目录

使用项目虚拟环境执行专用生成脚本：

```powershell
.\.venv\Scripts\python.exe scripts/prepare_cloud_deployment.py
```

脚本生成 `outputs/cloud-deployment/zhitiao-lab/` 目录及对应ZIP，归档文件名和实际生成结果以脚本输出为准。没有成功输出及校验记录前，不将“有生成脚本”写成“部署包已验收”。只向托管仓库提交生成目录中的公开应用文件；不提交本机 `.env`、密钥、`.venv`、私人CSV、会话项目包、日志或整个 `work/outputs` 目录。

上传或提交时，将 `zhitiao-lab/` 目录内的文件放到仓库根目录，使 `cloud_app.py`、`requirements.txt`、`requirements.lock.txt` 和 `.streamlit/` 处于根目录。部署候选的关键约定如下：

| 项目 | 云候选值与用途 |
| --- | --- |
| 程序入口 | `cloud_app.py`，不是本地命令中的 `app.py` |
| 部署分支 | `codex/online-review`，应与实际托管仓库分支一致 |
| Python版本 | 创建应用时在 Advanced settings 选择 **3.12**；本地历史验证版本为3.12.14，不能据此宣称云端补丁版本相同 |
| 依赖入口 | `requirements.txt` 引用完整 `requirements.lock.txt`，保持已验证的依赖版本清单 |
| 云专用配置 | `.streamlit/config.toml` 使用 `server.address = "0.0.0.0"`，只供托管容器接收平台流量 |
| 初次AI状态 | 不迁移本地密钥；平台 Secrets 暂留空，页面显示“AI 未配置” |

`0.0.0.0` 是云容器监听约定，不是可访问网址。不要因此修改本机安全设置、给本机8501端口做公网转发，或把 `127.0.0.1:8501` 填到参赛在线链接栏。正式网址须由托管平台在部署成功后提供。[官方部署步骤](https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/deploy)、[官方依赖管理](https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/app-dependencies)

## GitHub与应用分享

1. 在用户自己的GitHub账号下准备托管仓库，**建议选择private**，避免为提供在线体验而无必要地公开源码。只上传前述部署目录的公开应用文件，使用 `codex/online-review` 分支。仓库创建、授权与提交状态须以实际操作为准。
2. 在Streamlit官方托管平台登录并授权访问该仓库。选择实际仓库、`codex/online-review` 分支、`cloud_app.py` 入口；Advanced settings选择Python3.12，Secrets留空，然后部署。
3. 检查平台构建日志，确认依赖安装、应用启动和页面加载均成功。构建失败或返回错误页时，不把已分配域名当成可交付运行结果，也不要展示包含凭据的原始错误内容。
4. 将**应用分享设为公开可访问**，让评委无需项目成员身份即可打开。GitHub仓库private与应用公开分享是两项不同设置；公开应用不要求无条件公开源码。具体分享权限以账号当时可用设置为准。
5. 复制平台提供的真实应用URL，用退出登录的浏览器窗口和外部网络分别核验，再填写参赛表单。记录实际URL、验证时间、入口与分支；在完成前保持本文顶部“未部署”状态。

公开链接的操作与分享设置以 [官方应用分享说明](https://docs.streamlit.io/deploy/streamlit-community-cloud/share-your-app) 为准。不能仅凭本人登录后可打开就声称评委无需登录也能使用。

## AI先保持关闭，后续按需配置

首次云部署不复制本机真实密钥，Secrets留空。CSV导入、字段和单位确认、质量检查、指标、曲线、事件、A/B和报告均由应用本地计算模块运行，不依赖模型密钥；云端首次验收仍需实际点击验证这些功能。无密钥状态必须明确显示“AI 未配置”，不得用离线测试替身冒充在线模型。

若后续决定为公开应用启用AI，由用户在平台的Secrets管理界面填写四项服务端配置，置于**TOML根级**，不要添加 `[LLM]` 表，也不要把Secrets文件提交GitHub：

```toml
LLM_PROVIDER = "qwen"
LLM_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
LLM_MODEL = "qwen-plus"
LLM_API_KEY = "在平台Secrets中填写专用于此服务的完整密钥"
```

上述服务地址与模型沿用已核实的北京配置示例，不代表云端已经连接成功；密钥、地域、业务空间与模型权限须匹配。真实密钥仅在平台管理界面或受控服务端填写，不发给聊天、不放截图或日志中。保存配置后按平台要求重启并检查生效状态。[官方Secrets管理](https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/secrets-management)、[本项目模型配置与接口边界](LLM_CONFIGURATION.md)

启用前需由云端服务账户负责人明确授权，并设置账号额度、用量告警及访问控制。公开访问者的模型请求可能消耗同一个服务账户额度；应用现有单次轮数、工具数和超时限制**不等于全站每日额度或多人总量限制**。没有可接受的总量控制时应继续保持AI关闭。每轮用户仍需查看摘要范围并授权云模型发送；不自动上传整份CSV。

云端模型连通性、授权读取、网络地域、请求超时、证据校验和费用控制均需单独验收。此前本机单实验联调成功不能作为这些项目的通过记录，不能据此声称云端多轮追问或AI对比已验证。

## 上线后需要实际确认

建议优先使用五份自带公开数据：三份标记“解析函数合成数据”，两份PI A/B标记“闭环仿真数据，非实测”。按照 [DEMO](DEMO.md) 完成以下检查，并将真实结果写入验收记录：

- 未登录访问正式URL，确认能加载应用且没有仅成员可见的权限门槛。
- 加载解析示例，正式确认字段、时间单位和物理单位后查看质量、曲线和阶跃指标；换文件或改单位后旧确认和旧结果撤销。
- 加载问题数据并查看阻断提示和证据区间；分别保存PI A/B快照，确认共同观察时长后对比。
- 生成并下载HTML/JSON/CSV及项目包，核对中文、来源标记、区间和配置。没有AI结果的报告应写“AI未参与”。
- 首次无Secrets时AI明确未配置，其余入口可用；没有实际授权与模型执行记录就不声称AI云联调通过。
- 用另一个浏览器会话核对实验数据隔离；记录云端实际Python版本、依赖安装结果及遇到的问题。

应用数据在各自Streamlit会话内存中，不自动保存。刷新、新会话、平台重启或会话回收可能使未下载数据丢失；用户需显式下载项目包。在线上传CSV会传到托管服务器，未加密的项目包包含原始CSV，不应把私人实验当作公开演示数据。

免费托管平台可能存在资源限制、休眠、冷启动和维护中断，不能承诺比赛期间持续可用或所有评审网络可达。提交前至少用一个外部网络打开正式URL并走完核心路径，临近评审时再检查；保留本地已验收源码、PDF和视频作为交付材料。部署成功与外网实测之前，**本项目尚无可提交的在线URL**。
