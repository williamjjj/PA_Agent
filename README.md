# PA Agent — AI K线分析辅助工具（桌面端）

**交流 QQ 群：922829084**

---

面向主观交易者的 **价格行为（Price Action）** AI 辅助决策工具。从 **MT5 / TradingView / yfinance / AkShare** 读取 K 线，将结构化 K 线数据与预计算特征送入大模型做**两阶段分析**（市场诊断 → 交易决策），**不是**截图识图，**不连接券商、不执行下单**。

---

## 主要功能

- 📈 **多数据源**：MT5（Windows）、TradingView（全平台）、yfinance（期货/加密货币）、AkShare（A 股）
- 🧠 **两阶段 AI 分析**：市场诊断 → 策略路由 → 交易决策（限价/突破/市价或不下单）
- 🔄 **增量分析与持续跟踪**：新增 K 线时复用上次结论；开启 `keep_analysis` 后新 K 线收盘自动触发新一轮分析
- 🌳 **决策树可视化**：赛博科幻风格可交互流程图，自动播放闸门→策略路径动画
- 🔮 **未来走势预期**：AI 预测下一根 K 线方向和下一个市场周期位置
- 💬 **分析后自由追问**：完整对话会话管理器，实时推理流 + Token 进度条，对话历史持久化
- 📚 **经验库**：按周期位置检索历史案例供分析参考
- 📝 **完整落盘**：Prompt、原始响应、诊断/决策 JSON、Token 用量、追问记录
- 🛡️ **可配置校验体系**：JSON 校验、一致性检查、语义校验、截断修复、失败自动重试
- 🔒 **模型设置** 保存在本机配置目录；本地 harness 模式不需要向 PA Agent 提供 API Key

---

## 本地增强版（1.68.1）

[下载安装包与绿色版](https://github.com/williamjjj/PA_Agent/releases/tag/desktop-1.68.1-20261103) · [实测记录](docs/LOCAL_VALIDATION_20261103.md)

此分支以 `williamjjj/PA_Agent` 的 `222badb`（1.68）为基础，并移植
`codex/web-multiuser-vercel` 的 AlphaGPT 因果因子、历史回测和 CPU 公式搜索。

- **免填 API 的分析方式**：在「AI 模型设置」选择 Codex、WorkBuddy/CodeBuddy CLI 或 Hermes。
  使用该 harness 自己的登录和模型设置；不等于离线推理或无限额度。可指定实际 CLI 入口，模型留空沿用默认。
  WorkBuddy 桌面专用精简 CLI 可能不支持独立登录，需要可独立登录的完整 CLI；路径检测不代表认证成功。
- **AlphaGPT 量化研究**：菜单「AlphaGPT 量化研究」可计算当前行情因子，导入 CSV，
  运行历史回测、挖掘与恢复公式，查看权益曲线并导出包含数据指纹的完整结果。
  沿用网页分支的因果计算与样本外口径；不包含旧 Transformer/GPU 训练、实盘下单或旧权重转换。
- **在 harness 内调用**：`pa-agent-cli.exe integrate --target all` 安装 PA Agent 技能，
  然后在对应工具里请求使用 PA Agent。命令行也支持 `doctor / analyze / factors / research / mine`。
- **安装版**：双击 `PA-Agent-1.68.1-Setup-x64.exe`，按当前用户安装并创建快捷方式。
- **绿色版**：解压 `PA-Agent-1.68.1-Portable-x64.zip`，双击 `PA-Agent.exe`。
  运行时与依赖已包含，无需安装 Python、激活虚拟环境或另行安装依赖。

安装版数据保存在 `%LOCALAPPDATA%\PA Agent`；绿色版保存在程序旁的 `data`。
源码运行继续使用项目内原有数据。`PA_AGENT_DATA_DIR` 可显式指定数据目录。
更新与卸载保留运行数据；分发包不包含个人账户、行情记录或密钥。

使用、构建和验证边界见 [本地版说明](docs/LOCAL_RELEASE.md)。

## 环境要求

| 项目     | 要求                                                                    |
| -------- | ----------------------------------------------------------------------- |
| 操作系统 | Windows 10 / 11（主支持）、macOS 12+（TradingView 数据源）              |
| Python   | 3.11+                                                                    |
| 数据源   | MT5 / TradingView / yfinance / AkShare **至少配置一种**                  |
| 网络     | 可访问所配置的 AI API（如 DeepSeek、PackyAPI 等）                        |

---

## 详细说明

完整操作界面说明见 [`PA_Agent使用文档.md`](PA_Agent使用文档.md)，配置字段说明见 [`config/README.md`](config/README.md)。

---

**免责声明**：本工具仅供学习与研究，不构成投资建议。交易有风险，决策后果自负。

本项目采用 [GNU Affero General Public License v3.0 (AGPL-3.0)](LICENSE) 发布。

---

## 群友反馈榜单

感谢群友的使用反馈与鼓励，以下为群友评价截图（按时间从早到晚排列）：

<p align="center">
  <img src="qunyou/BD58CB2D6E4F45CC17CF832C506A982C.png" alt="群友反馈" width="480" />
</p>
<p align="center">
  <img src="qunyou/653EC872A0D6883A34B7B37B692C8B1D.png" alt="群友反馈" width="480" />
</p>
<p align="center">
  <img src="qunyou/QQ20260619-205140.png" alt="群友反馈" width="480" />
</p>
<p align="center">
  <img src="qunyou/QQ20260619-235505.png" alt="群友反馈" width="480" />
</p>
<p align="center">
  <img src="qunyou/QQ20260620-150714.png" alt="群友反馈" width="480" />
</p>
<p align="center">
  <img src="qunyou/QQ20260620-150833.png" alt="群友反馈" width="480" />
</p>
<p align="center">
  <img src="qunyou/QQ20260620-220824.png" alt="群友反馈" width="480" />
</p>
<p align="center">
  <img src="qunyou/QQ20260623-125929.png" alt="群友反馈" width="480" />
</p>
<p align="center">
  <img src="qunyou/91003065F07407E92B50964AE7F8A944.png" alt="群友反馈" width="480" />
</p>
<p align="center">
  <img src="qunyou/QQ20260624-191001.png" alt="群友反馈" width="480" />
</p>
<p align="center">
  <img src="qunyou/QQ20260628-014043.png" alt="群友反馈" width="480" />
</p>
<p align="center">
  <img src="qunyou/QQ20260628-213700.png" alt="群友反馈" width="480" />
</p>
<p align="center">
  <img src="qunyou/QQ20260629-163821.png" alt="群友反馈" width="480" />
</p>
<p align="center">
  <img src="qunyou/QQ20260701-212522.png" alt="群友反馈" width="480" />
</p>
<p align="center">
  <img src="qunyou/BB4AE8110A7011426BD29D5CE8B5F73B.png" alt="群友反馈" width="480" />
</p>
<p align="center">
  <img src="qunyou/F383D366F2254692418DB18AAA617ACE.png" alt="群友反馈" width="480" />
</p>
<p align="center">
  <img src="qunyou/AD48DF6289CB6A9D51FE0B8EE2EC38C2.jpg" alt="群友反馈" width="480" />
</p>
<p align="center">
  <img src="qunyou/F61C8DCDB67924B64B33403D20047E0B.png" alt="群友反馈" width="480" />
</p>
<p align="center">
  <img src="qunyou/QQ_1783089951396.png" alt="群友反馈" width="480" />
</p>

---

## 打赏与支持

如果你觉得这个程序对你有帮助的话，可以打赏激励作者继续优化程序，感谢你的支持和鼓励！

（作者会优先解决打赏人的问题，因为人太多了！回复不过来！）

<p align="center">
  <img src="赞助码.jpeg" alt="打赏二维码" width="420" />
</p>
