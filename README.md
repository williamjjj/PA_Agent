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

## 部署到自己的服务器

仓库提供 Docker Compose 配置，在同一台服务器运行 Web、独立后台 Worker 和持久 PostgreSQL。初始化私密配置、HTTPS 反向代理、备份及升级步骤见 [单机部署说明](docs/DEPLOYMENT.md)。默认只监听服务器回环地址，对外试用地址由实际域名和 TLS 代理配置确定。

## 部署到 Vercel

本次选择不部署常驻 Worker 的网页试用方案，账号关联、网页分支、数据库与 Preview 配置见 [Vercel 试用指南](docs/VERCEL_TRIAL.md)。

1. 导入此仓库，Root Directory 使用仓库根目录；选择 FastAPI，移除原 v0 项目遗留的 Build Command、Output Directory 和 Install Command 覆盖项。
2. Python 使用根目录 .python-version 中的 3.12；pyproject.toml 的 [tool.vercel] 明确指定 entrypoint = "app:app"。根 app.py 导出 ASGI app，不会启动桌面窗口。
3. 配置下面的环境变量，然后重新部署。Preview 与 Production 分别配置；如不希望预览访问正式账户，应使用不同的数据库与密钥。
4. 检查 /api/health 返回 ok，并确认 /api/status 的 configured 为 true，再注册账户。
5. 每个用户登录后，在「个人设置」填写自己的 Base URL、模型标识和 API Key。

填写地址和 Key 后，可点击「检测连接并获取模型」，从当前服务返回的模型列表选择；这一步不会保存设置或调用付费生成接口。部分网关不提供模型列表，可手动填写供应商给出的标识再保存。支持 OpenAI 兼容 Chat Completions；DeepSeek 思考开关、推理强度及兼容网关的输出预算会按服务格式发送。

| 环境变量 | 用途 |
| --- | --- |
| DATABASE_URL | 必填。持久 PostgreSQL 连接串，建议使用数据库供应商提供的连接池地址和 TLS 参数 |
| PA_WEB_ENCRYPTION_KEY | 必填。Fernet 密钥，所有账户的模型/数据源/通知凭据在数据库内加密保存 |
| PA_WEB_INVITE_CODE | 首次初始化时导入的邀请码；之后由管理员在网页管理 |
| PA_WEB_REGISTRATION | 首次初始化时设为 1 可开放注册；Vercel 默认关闭。已有数据库策略不会被环境变量覆盖 |
| PA_WEB_ADMIN_USERNAME | 首次创建独立管理员的用户名；不会提升已有普通账户 |
| PA_WEB_ADMIN_PASSWORD | 初始管理员密码，12–128 字符；创建成功后不会在重启时重置密码 |
| PA_WEB_DATA_DIR | 仅本地使用的 SQLite 数据目录 |

生成加密密钥（不要提交到 Git）：

~~~sh
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
~~~

必须保留同一加密密钥。直接更换会使已保存凭据无法解密。数据库账户需能创建 web_users、web_admins、web_registration_policy、web_sessions、web_items、web_limits、web_research_datasets、web_research_runs、web_mining_jobs、web_mining_workers 表；首次连接自动创建。新增表不会清空已有账户。现阶段不提供忘记密码邮件流程；已登录用户可修改密码并注销其他会话。

管理员使用独立账号登录，在「邀请码管理」查看、复制、修改、生成邀请码或停用注册。
普通用户无法访问管理接口；管理员也不能读取其他用户的私有数据。
首次设置、已有数据库升级与邀请码发放说明见 [管理员指南](docs/ADMIN.md)。

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
| Birdeye · Solana | 在个人设置中保存自己的 Birdeye API Key；填写完整 Solana 代币合约地址，显示名称单独填写；支持 1m、5m、15m、30m、1h、4h、1d，需账户拥有 OHLCV 接口权限 |
| MT5 本地桥接 | Windows 终端提供已收盘 K 线，网页分析在云端执行，见下一节 |
| CSV | time,open,high,low,close,volume 六列，50–500 根已收盘 K 线，时间升序且不重复；支持秒、毫秒、ISO 时间；无时区按 UTC 处理 |
| 模拟示例 | 可直接查看和下载；清楚标注为模拟，不作为在线行情回退 |

获取在线行情后，服务器为当前账户保存一小时的不可变快照。分析引用同一份快照，避免图表与模型输入不一致。行情失败时显示错误，不会用示例数据冒充真实行情。

## AlphaGPT 量化因子面板

1. 注册并登录账户，载入在线行情、CSV 或模拟示例。
2. 点击「计算量化因子」。无需大模型 Key，可查看对数收益、蜡烛压力代理、量能加速度、均价偏离、对数成交量，以及历史曲线和逐根数值。
3. 配置个人模型后运行 PA 分析；两者使用同一个快照、数据指纹和原始指标。分析记录保存当时的因子证据，重新打开历史记录不会悄悄按新代码重算。

使用 Birdeye 时，在「个人设置 → 数据源凭据」填写 Birdeye API Key，再选择 Birdeye 数据源并输入合约地址。默认示例地址为 Wrapped SOL；显示名称仅作为标签，同名代币不会共用市场身份。凭据只属于当前用户，接口不会返回密钥，也不会自动继承服务器环境变量中的 Key。

面板显示原始值、标准化值、较上根变化和历史分位。归一化只用当前与此前最多 60 根观测，至少需要 20 个有效因子值；预热不足显示为空。OHLCV 不含历史流动性和 FDV，因此 LIQ 明确显示不可用。缺口不填充，当前流动性不回填历史；历史分位不是胜率或收益预测。

CSV 和示例在第一次计算/分析时冻结到当前账户。快照一小时后过期，需要重新获取行情才能发起新分析；记录内已保存的因子仍可查看和导出。旧版记录没有因子证据时会明确标注未保存。价格显示与 PA 提示词支持低价代币的有效数字。

特征版本 `alphagpt-causal-v1` 更改了上游计算口径，不能直接复用旧 AlphaGPT 训练公式。具体定义、缺口与预热规则见 [因子说明](docs/ALPHAGPT_FACTORS.md)，来源许可见 [AlphaGPT 归属](third_party/AlphaGPT/README.md)。

## 历史研究与回测

1. 登录后打开「历史研究」，可以先载入 600 根模拟研究示例，无需模型或行情 API Key。
2. 使用真实数据时，上传六列 OHLCV CSV（300～5000 根、最多 2 MB），或用个人 Birdeye Key 获取 Solana 历史行情。也可复制当前账户的 Birdeye/CSV/示例快照，至少需要 300 根。研究时间按 UTC，结束边界不包含在内。
3. 选定单因子、入场/退出分位、时间切分和费用后，运行研究。默认训练 60%、验证 20%、测试 20%；只用训练段拟合阈值，收盘信号在下一根开盘成交。
4. 分别查看各段的净收益、买入持有基准、回撤、费用、权益曲线与逐笔成交，比较 0/1/2 倍成本。保存记录可重新打开，并导出包含原始 K 线、配置、版本和完整结果的 JSON。

当前模型是 24 小时连续现货的现金/多头模型，支持 1m 至 1d 固定周期；不适用于股票交易日历、期货或做空。同时间且 OHLCV 完全一致的 CSV 行去重，冲突行拒绝；缺口、请求首尾缺失或零成交量均阻止回测，不补造行情。CSV 的市场身份由用户声明，系统不会将其标为已核验加密资产。

每个账户最多保存 20 个固定数据集、100 份研究记录；重复导入相同数据不增加占用。有记录引用的数据集须先删除记录，才能删除数据。数据不会随主图更新或临时快照过期而变化。研究与 PA 请求共享账户并发限制。

重复查看测试结果后调参仍会引入选择偏差。原 AlphaGPT 训练代码未改动。计算方法与限制见 [回测说明](docs/ALPHAGPT_BACKTEST.md)。

## 后台公式挖掘

登录后打开「后台挖掘」，选择已保存的研究数据集，设定候选数量、随机种子、费用和时间预算后提交。网页可关闭；任务、进度和获选公式保存在数据库中，可重新打开、取消或导出。提交结果不确定时，使用页面的「重试上次未确认的提交」，它复用同一个幂等标识，避免重复入队。

首版是可复现的 CPU 公式搜索：从五项因果特征和受限算子生成命名公式，只在训练段比较费用后收益与回撤，选定后才计算验证段和测试段。默认尝试 32 个候选，最多 128 个；默认累计计算预算 60 秒，最多 120 秒。每账户最多 3 个活动任务、50 个总任务，同时只执行一个挖掘任务，使用独立的任务租约，不占用普通 PA 分析名额。

Web 服务不会自动启动挖掘线程。另开终端或在常驻服务器运行：

~~~sh
uv run python -m pa_agent.web.mining_worker
~~~

Worker 与 Web 必须使用同一数据库和加密配置；本地用同一 `PA_WEB_DATA_DIR`，生产用同一 `DATABASE_URL` 与 `PA_WEB_ENCRYPTION_KEY`。`--once` 只处理至多一个任务后退出，适合验证。Worker 离线时网页明确显示等待，任务保持排队。Vercel 仅托管网页/API，Worker 需要单独常驻进程，不依赖函数请求结束后的线程。

每个候选完成后保存检查点；Worker 中断后由租约过期触发有限次恢复。取消、超时和丢失租约的进程不能写入成功产物。代码/词表版本变化会拒绝旧任务的继续计算，可用新任务重新提交。获选公式、训练数据与完整数据指纹、配置、种子和代码指纹一起导出。

这不是旧 AlphaGPT 的 Transformer/GPU 训练。训练段经过搜索选择，不能当作收益保证；反复查看测试结果后改种子或参数仍会产生偏差。详细计算和恢复规则见 [挖掘说明](docs/ALPHAGPT_MINING.md)。

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
- 所有私有记录操作均按账户与记录 ID 查询；密码修改注销其他会话，并使改密前已经验证但尚未发放的登录会话失效。
- 浏览器请求携带当前页面的账户 ID，并由服务器与 Cookie 身份比较。跨标签页切换账户会清空旧输入和分析；旧页面的请求不能被转交给新账户。该 ID 不能替代登录认证。
- 凭据接口仅返回“是否已配置”；同一模型服务留空保留原值，勾选清除才删除。更换模型服务域名时必须重新填写 Key 或明确清除旧 Key，检测模型列表也遵循此规则。
- 自定义模型地址只允许公共 HTTPS 443 地址；解析后固定连接 IP，保留 Host/TLS SNI，禁止重定向和环境代理。
- 使用数据库原子租约控制每个账户同时一个 AI 或研究请求；注册、登录和数据抓取有跨实例限流。
- 每次调用都创建独立模型客户端、数据源和提示词对象，不使用共享用户凭据或 Tushare 全局 Token。

## 验证

~~~sh
uv run pytest tests -m "not live"
node --test tests/web/test_stream.mjs
uv run ruff check pa_agent/web tests/web pa_agent/bridge.py --select E9,F63,F7,F82
~~~

测试不使用真实模型凭据。本次完整离线回归为 **996 项通过、0 项失败、7 项 live 测试未运行**；其中 Web 套件 357 项全部通过，第三阶段新增 93 项检查。此前失败的预测属性测试已按既有并列规则修正，并修复方向大小写/空格规范化缺陷；没有跳过失败用例。因果性、实际 Worker 中断恢复、浏览器及 PostgreSQL 并发验证见 [验证记录](docs/WEB_VALIDATION.md)。

AlphaGPT 前三阶段已接入因子面板、历史研究与后台 CPU 公式挖掘。跨市场和 GPU 训练的边界见 [整合评估](docs/ALPHAGPT_INTEGRATION.md)。

线上模型、行情供应商权限和 Windows MT5 连接需要使用各自账户验证。许可证见 [LICENSE](LICENSE)。
