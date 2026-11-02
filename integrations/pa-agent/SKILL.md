---
name: pa-agent
description: 调用本机 PA Agent 获取价格行为分析、AlphaGPT 因果因子、历史回测和有预算的公式搜索。适用于用户要求使用 PA Agent 分析已收盘行情或 OHLCV 文件，不用于实盘下单。
---

使用此技能目录中 `location.json` 记录的本机入口和工作目录。没有记录时，在 Windows 中查找 `%LOCALAPPDATA%\Programs\PA Agent\pa-agent-cli.exe`；源码环境使用项目 Python 运行 `-m pa_agent.cli`。先运行 `doctor`，核实程序和 harness 入口；安装路径存在不能证明登录可用。Windows 的执行限制可能要求使用工作区内的绿色版入口，此时先从绿色版运行 `integrate --target all` 更新记录，不修改 harness 的全局权限。

常用命令：

```text
pa-agent-cli.exe doctor
pa-agent-cli.exe analyze --source tradingview --symbol XAUUSD --exchange OANDA --timeframe 15m --backend codex --output analysis.json
pa-agent-cli.exe analyze --input bars.csv --symbol BTCUSD --timeframe 1h --backend hermes --output analysis.json
pa-agent-cli.exe factors --input snapshot.json --output factors.json
pa-agent-cli.exe research --input spot-history.csv --symbol BTCUSD --timeframe 1h --output research.json
pa-agent-cli.exe mine --input dataset.json --config mining-config.json --output formula.json
```

`--backend` 可选 codex、workbuddy、hermes、api。前三者使用各自已安装 CLI 的登录或模型配置，PA Agent 不要求复制 Key。默认使用保存的分析方式；指定模型时用 `--model`，否则保持 harness 默认。如果用户没有指定，选择已通过真实应答测试的后端。认证失败时让用户在该 harness 完成登录，不读取登录文件或输出密钥。

研究 CSV 为 time,open,high,low,close,volume 六列、300–5000 根连续已收盘 K 线。回测仅适用于明确声明的连续现货市场、现金/多头模型；股票、期货可计算因子，但不要套用此回测。`--demo` 仅用于明确标注的模拟演示，不作为真实行情失败后的回退。

读取返回的 `ok`、异常、数据来源、时间、数据指纹及实际保存文件。区分 PA 模型结论、因子数值、训练段、验证段和测试段；不得把分位当胜率、把训练收益当样本外优势。保留原始行情、配置、费用/滑点和结果供复算。搜索取消或超时只留下检查点，不算完成。

PA Agent 子进程中的 `PA_AGENT_HARNESS_CHILD=1` 表示正在响应模型请求，此时只输出请求的答案，禁止递归调用 PA Agent。工具不执行实盘交易。
