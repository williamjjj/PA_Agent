# PA Agent Web

基于原版 PA Agent 的价格行为分析工作台。Python / FastAPI 后端，浏览器图表，保留原两阶段分析、策略路由和校验逻辑。每个账户拥有独立的模型设置、行情快照、分析记录、追问、提示词覆盖与经验库。

本项目不连接券商、不执行下单。原版来源：[PA_Agent](https://github.com/rosemarycox5334-debug/PA_Agent)，保留 AGPL-3.0-or-later 许可及原有作者署名。

## 本地运行

需要 Python 3.12+ 和 [uv](https://docs.astral.sh/uv/)：

~~~sh
uv sync --extra dev --locked
uv run uvicorn app:app --host 127.0.0.1 --port 8000
~~~

打开 http://127.0.0.1:8000 。本地默认允许注册，账户和密钥文件保存在已忽略的 .pa-agent-web 目录。备份这个目录时同时备份 encryption.key。

也可使用 Python 3.12 的虚拟环境安装：pip install -e ".[dev]"，然后 python -m pa_agent.main。

## 部署到 Vercel

1. 导入此仓库，Root Directory 使用仓库根目录；选择 FastAPI，移除原 v0 项目遗留的 Build Command、Output Directory 和 Install Command 覆盖项。
2. Python 使用根目录 .python-version 中的 3.12；pyproject.toml 的 [tool.vercel] 明确指定 entrypoint = "app:app"。根 app.py 导出 ASGI app，不会启动桌面窗口。
3. 配置下面的环境变量，然后重新部署。Preview 与 Production 分别配置；如不希望预览访问正式账户，应使用不同的数据库与密钥。
4. 检查 /api/health 返回 ok，并确认 /api/status 的 configured 为 true，再注册账户。
5. 每个用户登录后，在「个人设置」填写自己的 Base URL、模型标识和 API Key。

| 环境变量 | 用途 |
| --- | --- |
| DATABASE_URL | 必填。持久 PostgreSQL 连接串，建议使用数据库供应商提供的连接池地址和 TLS 参数 |
| PA_WEB_ENCRYPTION_KEY | 必填。Fernet 密钥，所有账户的模型/数据源/通知凭据在数据库内加密保存 |
| PA_WEB_INVITE_CODE | 推荐。设置后使用邀请码注册 |
| PA_WEB_REGISTRATION | 设为 1 可开放注册；Vercel 默认关闭注册。设置邀请码也会启用注册 |
| PA_WEB_DATA_DIR | 仅本地使用的 SQLite 数据目录 |

生成加密密钥（不要提交到 Git）：

~~~sh
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
~~~

必须保留同一加密密钥。直接更换会使已保存凭据无法解密。数据库账户需能创建 web_users、web_sessions、web_items、web_limits 表；首次连接自动创建。现阶段不提供忘记密码邮件流程；已登录用户可修改密码并注销其他会话。

旧 PA_WEB_ACCESS_SECRET 共用口令和平台共用模型配置已停用。网页不会读取其他账户或环境变量中的模型 API Key。没有数据库配置时，首页、示例图表和 CSV 导入仍可打开，但账号功能返回配置错误；Vercel 不会自动退回临时 SQLite。

public 目录由 Vercel 静态托管；本地使用相同的根路径。vercel.json 将 prompt_engineering 打包进函数，并将一次请求上限设为 240 秒。模型客户端保留网络超时和通知余量，过长的分析会终止并保存已完成阶段。此应用需要持久 PostgreSQL，不能只依赖函数内存或 /tmp 保存账号和记录。

## 数据源

| 来源 | 使用方式 |
| --- | --- |
| Yahoo Finance | 例如 GC=F、ES=F、BTC-USD、AAPL；1 分钟仅请求近期历史，期货行情可能延迟 |
| TradingView | 明确选择交易所，例如 OANDA + XAUUSD、BINANCE + BTCUSDT；可选个人账号凭据，数据权限取决于供应商 |
| 东方财富 / AkShare | A 股代码例如 600519；可选择复权方式 |
| 东方财富期货 | 例如 RB0；按接口支持选择周期 |
| Tushare Pro | 在个人设置中保存 Token；分钟行情需要相应权限 |
| MT5 本地桥接 | Windows 终端提供已收盘 K 线，网页分析在云端执行，见下一节 |
| CSV | time,open,high,low,close,volume 六列，50–500 根已收盘 K 线，时间升序且不重复；支持秒、毫秒、ISO 时间；无时区按 UTC 处理 |
| 模拟示例 | 可直接查看和下载；清楚标注为模拟，不作为在线行情回退 |

获取在线行情后，服务器为当前账户保存一小时的不可变快照。分析引用同一份快照，避免图表与模型输入不一致。行情失败时显示错误，不会用示例数据冒充真实行情。

## MT5 桥接

Vercel 的 Linux 函数无法连接用户电脑上的 Windows MT5 终端。请在已登录 MT5 的 Windows 电脑上安装可选桥接依赖：

~~~powershell
uv sync --extra mt5-bridge
uv run python -m pa_agent.bridge --url https://你的域名 --username 你的网页用户名 --symbol XAUUSD --timeframe 15m --count 100
~~~

启动后交互输入网页账户密码，密码只保留在进程内存。程序每 60 秒上传已收盘 K 线。网页选择「MT5 本地桥接」，输入相同品种、周期和数量，再获取行情。桥接超过三分钟未更新会显示离线；每个账户使用一个活动桥接品种。程序不上传 MT5 交易账号或模型 API Key，不会下单。实际 MT5 终端连接需要在 Windows 环境验证。

## 功能与迁移范围

| 原桌面功能 | 网页实现 |
| --- | --- |
| 两阶段分析、策略路由 | 复用原 orchestrator、PromptAssembler、JsonValidator 和策略文件 |
| JSON / 语义 / 一致性校验、截断修复、重试 | 在个人设置中独立配置；重试也受单次请求总预算限制 |
| 增量分析 | 相同市场、周期、交易所，重叠 OHLCV 完全一致且新 K 线数量不超上限时复用记录 |
| 持续跟踪 | 页面可见时每 60 秒刷新，新增已收盘 K 线后自动分析；错误、取消、切换数据源后停止 |
| 决策树 | 显示真实 gate_trace / decision_trace，展开节点查看依据，支持播放判断路径 |
| 未来预期 | 展示下一根 K 线与下一市场周期预测，下一根预测可关闭 |
| 图表 | K 线、EMA20、ATR14、缩放和十字线；显示入场/止损/目标价，保留有效前案的连续性价位 |
| 分析后追问 | 流式回答及推理，保存到当前分析；最近 20 轮参与后续上下文，单记录最多 100 轮 |
| Token 用量与原始记录 | 查看实际 Prompt、响应、校验异常及用量，导出完整 JSON |
| 经验库 | 保存成功/失败案例与复盘；按市场周期引用当前账户案例 |
| 提示词编辑 | 覆盖所选原版提示词文件，只影响当前账户，可恢复默认 |
| 飞书 / PushPlus | 用户主动启用后向自己的机器人推送；飞书使用自定义机器人 Webhook 与可选签名密钥 |
| 桌面窗口 / Qt 线程 / 托盘 / 本地 Agent | 删除；浏览器及 HTTP 模型服务承担对应交互，Cursor/TRAE/Qoder/WorkBuddy 本地进程登录不能搬入云函数 |

这不是后台常驻交易程序。关闭网页或后台休眠后，持续跟踪不再执行；如果需要全天无人值守，需另接持久任务队列/常驻 worker。该项目不声称把 Windows 进程、托盘、系统声音和本地桌面 Agent 直接迁移到 Vercel。

## 账户与部署边界

- 密码使用 Argon2；会话为数据库中的随机令牌哈希，8 小时过期，生产 Cookie 使用 HttpOnly、Secure、SameSite=Lax。
- 所有私有记录操作均按账户与记录 ID 查询；密码修改注销其他会话。
- 凭据接口仅返回“是否已配置”，空白保留原值，勾选清除才删除。
- 自定义模型地址只允许公共 HTTPS 443 地址；解析后固定连接 IP，保留 Host/TLS SNI，禁止重定向和环境代理。
- 使用数据库原子租约控制每个账户同时一个 AI 请求；注册、登录和数据抓取有跨实例限流。
- 每次调用都创建独立模型客户端、数据源和提示词对象，不使用共享用户凭据或 Tushare 全局 Token。

## 验证

~~~sh
uv run pytest tests -m "not live"
node --test tests/web/test_stream.mjs
uv run ruff check pa_agent/web tests/web pa_agent/bridge.py --select E9,F63,F7,F82
~~~

测试不使用真实模型凭据。完整旧测试套件存在已在原分支复现的失败，不能将其视为全绿；详情见 [验证记录](docs/WEB_VALIDATION.md)。Web CI 严格检查新增账户/数据/流式功能与相关核心流程，并单独保留完整旧套件报告。

线上模型、行情供应商权限和 Windows MT5 连接需要使用各自账户验证。许可证见 [LICENSE](LICENSE)。
