# 本地增强版验证记录

基线为本仓库 main 1.68，`222badb`。测试时间为 2026-11-03，Windows x64，Python 3.12.13。

## 已验证

- 桌面完整非 live 回归：1222 passed，7 deselected；包括 Qt 交互、两阶段处理、切换取消、量化和本地 harness。
- 三种 CLI 最小真实应答：Codex、Hermes、腾讯官方完整 CodeBuddy CLI 均成功，不向 PA Agent 填 API Key。
- Codex 使用模拟行情完成两阶段真实分析并保存结果；源码和冻结 EXE 均得到 `Stage1Done`、`Stage2Done`、`RecordSaved`，返回 `ok=true`。
- 腾讯完整 CLI 2.179.3 经用户浏览器授权后真实应答成功；兼容其当前 JSON 数组输出。WorkBuddy 桌面附带的精简 CLI 无独立认证，发行版支持指定完整 CLI 及其配置目录。
- 冻结 EXE 的因子、历史研究、32 候选公式搜索可运行；公式源文件随包分发，使检查点代码指纹在冻结环境也可验证。
- 量化 Qt 后台任务实际完成，界面显示训练、验证和测试三个区间；图形结果人工查看，横轴使用日期。
- 100000 个逐字符片段经合并后少于 30 次界面通知，文本内容保持一致。
- 长单行预览保持 250000 字符上限；20 次追问工作线程结束后 Qt 子对象全部释放。
- 超时、取消和进程错误不会泄露子进程 stderr；请求临时文件和子进程清理通过测试。
- harness 切换保留各自配置；技能安装保留旧版本，重复安装不重复创建备份。
- GitHub Windows 自动回归（提交 `f2d9c5c`）：1222 passed、1 skipped、7 deselected；[运行记录](https://github.com/williamjjj/PA_Agent/actions/runs/41503344879)。
- GitHub Ubuntu 网页自动检查通过：[运行记录](https://github.com/williamjjj/PA_Agent/actions/runs/41501380340)。
- 最终安装版 `doctor --check all`：Codex、WorkBuddy/CodeBuddy CLI、Hermes 均返回正确最小应答。
- 安装器实际静默安装退出码 0，快捷方式和四个技能目录注册成功；已有 46 个历史文件复制后逐个核验 SHA-256。
- 绿色 ZIP 解压后，在 PATH 中移除 Python、清除虚拟环境变量，因子、回测、公式搜索三个命令均成功。归档 CRC 和 EXE 哈希通过核验，归档不含个人数据目录。
- 安装版 GUI 实际启动并记录 `Main window shown`。图形界面源码渲染与后台计算另行检查；未将浏览器工具启动失败计作网页点击验收通过。
- 为避免本机 Windows 对安装目录的执行限制，本机 harness 调用记录指向工作区内独立的私有绿色副本。公开发行包保持无个人配置。

网页合并另见网页分支的 `docs/MAIN_SYNC_20261103.md`。网页本地回归 1190 passed、19 skipped、7 deselected，流解析 4/4。

## 验证边界

7 项 live 测试需要指定外部服务，未用个人 API Key 运行。实际 harness 验证使用明确标记的模拟数据和最小请求，不能证明所有模型、所有行情服务或长期登录有效。
性能测试针对可复现的刷新与资源积累问题，没有把短期压力测试等同于连续数天的稳定性验收。
AlphaGPT 为网页分支的因果计算、回测和有限 CPU 搜索适配；不包含旧 GPU 训练权重或实盘下单。
安装包未申请商业代码签名证书。下载文件以发行附件的 SHA-256 清单核验。
