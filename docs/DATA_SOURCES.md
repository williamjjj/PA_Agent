# 本地版数据源与资讯接入（1.68.2）

主界面「数据来源」右侧新增 **新增 / 管理**，顶部也有 **管理数据源**。
行情用于图表、价格行为分析和 AlphaGPT 因子；新闻、公告、宏观、财务等资料在 **资讯与 MCP** 中查询，选择后可附加到本品种的分析。

## 股票、期货与虚拟币行情

1. 打开「管理数据源」，勾选需要的内置行情服务：MT5、TradingView、A股专属源、AkShare、东方财富期货、Tushare、Yahoo Finance。
2. TradingView 账号或 Tushare Token 在同一窗口填写，其他已有设置保留。
3. 点击「新增数据源」，选择币安现货、OKX 现货/合约、本地 CSV、自定义 HTTP/JSON 或 MCP 模板。
4. 配置品种、周期后点击「测试读取」。成功后「保存数据源」，再在管理窗口点击「保存并应用」。
5. 返回主界面选择数据来源，再点击「获取数据」。配置保存在本机，重启后仍可使用。

币安现货默认 `BTCUSDT`，使用官方公开行情地址，无需交易 Key。OKX 默认 `BTC-USDT`，永续可填 `BTC-USDT-SWAP`，单次最多 300 根。
Yahoo Finance 可输入 `AAPL`、`0700.HK`、`GC=F` 等服务支持的代码。数据覆盖、交易时段及可读历史长度遵循各服务的实际返回。

CSV 默认六列：`time,open,high,low,close,volume`。支持 UTF-8（含 BOM）、Unix 秒/毫秒/微秒和带时区的 ISO 时间；无时区时间按 UTC 处理。文件对应固定品种与周期，可在编辑窗口更改。可用字段映射适配不同列名。乱序行情会排序，重复时间、无效价格或非有限数值会明确报错。未收盘 K 线保留标记，分析仍按既有规则使用已收盘数据。

AlphaGPT 的历史回测继续使用原来的连续现货、持有多头或现金模型；股票和期货的数据可用于图表、PA 分析和因子计算。

## 东方财富妙想

已有本机 Codex MCP 配置时，在管理窗口点击「从 Codex 导入 MCP」，只选择需要导入的服务，例如 `mx-ds-mcp`。环境变量请求头保留为引用，避免重复填写。

也可以新增「东方财富妙想 MCP」：

- 地址：`https://mxapi.eastmoney.com/mxds/mcp`
- 授权头名称：`em_api_key`
- 授权头内容：填写自己的妙想 API Key
- 点击「读取工具列表」，选择新闻、公告、宏观、股票或其他数据工具。
- 新闻工具默认 `mx_finance_search_news`，参数为 `{"query":"{query}"}`。

用「资讯与 MCP」输入自然语言问题。资讯窗口显示可阅读的标题、摘要、时间与来源链接，另有原始数据页。结果保存在本机数据目录的 `information` 文件夹。

参考：[东方财富官方 MCP 接入指南](https://mxapi.eastmoney.com/mxds/doc/mcp-install.md)。

## 金十数据

选择「金十数据 MCP · 快讯与资讯」或「金十数据 MCP · K线」模板：

- 地址：`https://mcp.jin10.com/mcp`
- 授权头名称：`Authorization`
- 授权头内容：`Bearer ` 后面接自己的金十 MCP Token
- 点击「读取工具列表」，按服务返回的参数说明配置工具。
- 官方工具包括 `get_quote`、`get_kline`、`list_flash`、`search_flash`、`list_news`、`search_news`、`get_news`。
- K线模板的返回数组路径是 `data.klines`；具体工具参数以当前服务列出的 JSON Schema 为准。

Token 在[金十官方智能开放平台](https://mcp.jin10.com/app/doc.html)申请。它属于数据服务授权；Codex 分析仍复用本机登录。

## 通用 API / MCP

支持 HTTP GET/POST JSON、MCP Streamable HTTP、SSE 与本地 stdio 程序。
连接名称、URL、请求头、环境变量请求头、超时、MCP 工具名和工具参数均可编辑。可导入含 `mcpServers` 或 `mcp_servers` 的 JSON/TOML 配置。导入后可重新测试或修改，不改动原来的 MCP 配置文件。

HTTP URL、JSON 请求体和 MCP 参数可使用 `{symbol}`、`{timeframe}`、`{limit}`、`{query}`。URL 参数会转义；JSON 中单独的 `"{limit}"` 会替换为整数。

HTTP 行情接口示例：

```text
https://your-service.example/ohlcv?symbol={symbol}&interval={timeframe}&limit={limit}
```

对象数组的字段映射示例：

```json
{"time":"timestamp","open":"open","high":"high","low":"low","close":"close","volume":"volume"}
```

对于数组行，用列号字符串映射，例如币安：

```json
{"time":"0","open":"1","high":"2","low":"3","close":"4","volume":"5"}
```

返回数据路径可写 `data.items`、`data.0.items` 等；响应根节点即为数组时留空。
MCP 行情也需要映射成 OHLCV 数组；自然语言财务、研报、新闻结果可以保留为资讯，无需转换为 K 线。

## 将资讯交给 Codex 分析

1. 主界面选择要分析的品种。
2. 打开「资讯与 MCP」，选择来源，输入问题并点击「查询」。
3. 阅读结果，点击「加入本品种分析资料」。最多保留最近 4 份，送入模型的正文总量上限为 24000 字符。
4. 返回主界面获取行情并提交分析。已选资料按品种匹配进入模型提示，来源、查询时间和实际送入内容写入分析记录。
5. 不再需要的资料可通过「清除本品种已附加资料」移除。附加选择只在本次程序运行中保留，避免下次启动误用旧资讯；查询结果文件仍保留。

界面查询时间与资讯事件时间分别保留。外部内容作为参考资料处理，资讯不会写入 AlphaGPT 因子或历史回测的数据集。

## 命令行

```text
pa-agent-cli sources
pa-agent-cli sources --id custom:... --tools
pa-agent-cli sources --id custom:... --test
pa-agent-cli sources --id custom:... --query "黄金最近一周重要新闻" --output information.json
pa-agent-cli analyze --source custom:... --symbol BTCUSDT --timeframe 1h --backend codex --information information.json --output analysis.json
```

源码、安装版和绿色版共用相同逻辑。公开安装包不包含个人数据源、Token 或查询结果。

协议与行情格式参考：[官方 MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk/tree/v1.30.0)、[币安公开行情接口](https://github.com/binance/binance-spot-api-docs/blob/master/faqs/market_data_only.md)、[OKX API 文档](https://app.okx.com/docs-v5/en/)。
