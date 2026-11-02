# 本地增强版

基线：用户仓库 `williamjjj/PA_Agent`，main 提交 `222badb6c34afae8daa59a47cbee1cdd59db05dd`（1.68）。
量化来源：同仓库 `codex/web-multiuser-vercel`，`fc634fb` 所含的 AlphaGPT 集成。
来源审查还核对了用户提供的 `williamjjj/AlphaGPT`；保留 Apache-2.0 归属与许可。

## 本地 harness

「AI 模型设置」的分析方式下拉框支持 API、Codex、WorkBuddy、Hermes。
本地方式通过正式 CLI 调用其现有账户，提示词走标准输入，不拼接到 shell 命令。
支持取消、限时、输出上限，Windows Job Object 负责清理子进程；本地调用失败不会悄悄改用 API。
有些 CLI 只公开最终答案，界面等待后显示完整答案，不制造推理过程或未报告的 token 用量。
选择本地方式时隐藏 API 表单，切换不同 harness 会保留各自的路径、模型与配置目录。
`pa-agent-cli doctor` 检查安装；`pa-agent-cli doctor --check all` 向三个已配置账户各发一次最小请求验证登录。

Codex 使用 `exec --json`；Hermes 使用 `chat --query-file - --oneshot --quiet`。
WorkBuddy 的正式命令来自 CodeBuddy CLI 家族，使用 `--print --output-format json`。
桌面专用精简构建可能要求由桌面宿主提供认证，独立运行会提示登录；此时应选择可独立登录的完整 CLI。
GUI 登录与 CLI 登录是否共享取决于 harness 版本，不读取或复制桌面登录文件来绕过该要求。

官方接口资料：
- [Codex 非交互执行](https://developers.openai.com/codex/noninteractive)
- [WorkBuddy / CodeBuddy 无头模式](https://www.workbuddy.ai/docs/cli/headless)
- [Hermes 命令参考](https://hermes-agent.nousresearch.com/docs/reference/cli-commands)

## AlphaGPT

菜单「AlphaGPT 量化研究」提供五项 OHLCV 因子、历史曲线、单因子回测和 CPU 公式搜索。
LIQ 缺少历史流动性资料时显示不可用。研究仅接受连续现货、300–5000 根完整已收盘 K 线；
CSV 六列为 time,open,high,low,close,volume。模拟示例有明确标记，行情失败不会替换成模拟数据。
手续费和滑点以基点（bp）输入；阈值与候选选择仅使用训练段，验证、测试段单独报告。
公式搜索每个候选后保存检查点，取消/超时不发布未完成结果。数据、配置、公式和结果可导出复算。

这是网页分支已有的因果适配功能，不是旧 AlphaGPT GPU 模型或实盘交易系统的完整迁移。
具体计算口径见 ALPHAGPT_FACTORS.md、ALPHAGPT_BACKTEST.md、ALPHAGPT_MINING.md。

## 性能调整

- 在工作线程中合并连续输出片段，按 50 ms 或 4096 字符批量通知界面，并在阶段边界刷新。
- 实时预览最多保留 250000 字符、6000 个文本块，禁用撤销栈；完整记录仍保存到磁盘。
- 追问线程结束后释放 Qt 对象；分析线程在原生 finished 信号后清理，避免过早销毁。
- API 调用在正常、取消和异常退出时关闭客户端连接池。
- 行情重试等待可被取消，指数退避计算有界。
- 量化计算在后台工作线程中进行，支持进度与取消。

这些改动针对代码中可复现的积累与刷新开销。短期压力测试不等于连续数天实盘运行保证。

## 构建与数据

在 Windows 10/11 x64、Python 3.12 环境安装项目及开发依赖、PyInstaller，使用官方签名的
Inno Setup 编译器，然后运行 `scripts/build_windows.ps1`。两个 EXE 共用随包运行时。
PyInstaller 规则显式排除开发机器上 Poppler 的同名 ICU，使用 Windows 自带 ICU，避免 Qt 加载错误。
调用外部 harness 时按 [PyInstaller 官方说明](https://pyinstaller.org/en/stable/common-issues-and-pitfalls.html#launching-external-programs-from-the-frozen-application)
暂时恢复 Windows DLL 搜索顺序，并清理子进程中指向随包运行库的 PATH，避免影响其独立 Python/Node 环境。
发行构建的 Python 依赖精确版本见 `packaging/requirements-build.txt`。
发行目录包括安装包、绿色 ZIP、SHA256SUMS 和 manifest。

安装器按用户安装，不要求管理员权限。卸载不删除分析数据；绿色版移动时应保留 data 文件夹。
已有本地配置在升级前另行备份，个人配置不会进入公共分支或发行包。
