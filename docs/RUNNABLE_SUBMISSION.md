# 可运行源码交付说明

复赛“可运行程序”材料采用 `智调Lab-可运行源码.zip`，提供完整本地 Streamlit 应用源码。应用方案 PDF 与演示 MP4 单独提交，不嵌入源码 ZIP。本项目默认不部署公网；比赛表单是否接受源码附件或要求填写代码托管地址，仍需按实际提交页面确认。没有托管地址时不能把本机 `127.0.0.1` 当作评委可访问的网址。

## 评委首次运行

1. 解压 ZIP，进入 `zhitiao-lab` 文件夹；安装 Python 3.12。源码包不含 Python 解释器或第三方依赖包，首次安装通常需要网络。
2. Windows 在该文件夹的 PowerShell 或 cmd 中运行：

   ```powershell
   .\scripts\install.cmd
   .\scripts\start.cmd
   ```

   无 `py` 启动器时：`scripts\install.cmd "C:\实际路径\Python312\python.exe"`。安装器检查并使用项目独立 `.venv`，失败明确返回错误，不修改系统 Python 或脚本执行策略。

3. 浏览器访问 `http://127.0.0.1:8501/`。端口被占用时运行 `scripts\start.cmd --port 8502` 并访问 8502。`Ctrl+C` 停止。
4. 左侧选择“加载示例”→“一阶解析阶跃响应”→“一键加载示例”，核对字段和单位，确认后查看质量、指标和曲线。接着按 [演示路径](DEMO.md) 查看异常证据、PI A/B 对比和导出。
5. 本地分析无需 API Key；没有配置时 AI 区显示“AI 未配置”。仅在用户自行填写服务端 `.env` 并逐轮授权后才使用云模型。不能把测试替身当作在线 AI 演示。

Linux/macOS 对应命令（代码提供支持，实机验收状态见 [验收记录](ACCEPTANCE.md)）：

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install --only-binary=:all: -r requirements.lock.txt
.venv/bin/python -m pip check
.venv/bin/python scripts/manage.py start
```

安装完成后的本地 CSV 分析、曲线、报告和项目恢复不要求联网。首次依赖安装与真实云 AI 请求需要可用网络。Dockerfile 为可选交付，不代表容器运行已验证；不要自动部署公网，相关边界见 [DEPLOYMENT](DEPLOYMENT.md)。

## 包内内容与排除范围

`scripts/package_submission.py` 使用精确文件名白名单：应用入口；core/ui/agent/reports/projects；公开测试；安装启动脚本；锁定依赖；需求、算法和验收文档；公开示例生成脚本、清单和五份 CSV。包内附 `DELIVERY.txt` 与 `SHA256SUMS.txt`。

- 三份解析函数合成数据：一阶阶跃、非等间隔、数据质量问题。它们不是实测，也不是闭环仿真。
- 两份闭环仿真数据：PI A、PI B。它们来自同一对象和环境，仅 PI 参数不同，非实测。CSV 与已确认 SHA-256 不一致时拒绝打包，保留用户修改。
- 不包含 `.venv`、`.git`、`.env`、`.streamlit/secrets.toml`、任何私人 CSV、会话项目 `.labproj`、work/outputs、日志、缓存以及 PDF/MP4。仅 `.env.example` 空密钥模板进入包。
- 已配置的环境和 `.env` 密钥值只在内存中检查；发现出现在公开文件则拒绝生成，不打印、不脱敏改写源码。该检查用于已知凭据防漏，不是对所有类型未知秘密的真实性认证。
- 归档成员只有 `zhitiao-lab/` 下的相对路径；拒绝链接、重定向路径、越界路径，固定 ZIP 时间戳。同一白名单文件内容和 Python 压缩环境下重复生成字节一致。

## 重新生成交付包

在项目根目录使用已安装依赖的虚拟环境：

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_submission_package.py -q
.\.venv\Scripts\python.exe scripts/package_submission.py
```

输出三个文件：`outputs/submission/智调Lab-可运行源码.zip`、`智调Lab-源码清单.json`、`智调Lab-源码说明.txt`。已有同名文件默认拒绝覆盖；明确更新本脚本的三个产物时加 `--overwrite`。PDF、MP4 和其他已有文件不受影响。先完成源码、文档及测试再最终打包，后续改动不会自动进入已生成 ZIP。

清单保存 ZIP 的 SHA-256 与每个归档条目的路径、长度、SHA-256。解压后的源文件应与清单一致；摘要只核对完整性，不认证作者和实验真实性，不含私人文件路径或密钥摘要。

## 测试、保存和交付边界

```powershell
.\.venv\Scripts\python.exe -m pytest
.\scripts\start.cmd --check-only
```

普通测试离线运行，不使用真实密钥或网络；真实 API 联调独立记录。实际运行数量和浏览器验证见 [ACCEPTANCE](ACCEPTANCE.md)，不能以“ZIP 已生成”替代新机器安装或模型联调验收。

实验数据在当前会话内使用；关机前在左侧“项目 · 保存与恢复”生成并下载 `.labproj`。下次上传、核验、确认替换后恢复。没有自动保存，恢复不会继承活动 AI 授权和聊天证据。项目包含完整原始 CSV 且未加密，私人项目包应自行保存，不随公开源码交付。
