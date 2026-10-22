# AlphaGPT 接入 PA Agent 网页版：代码审查与实施建议

## 阶段 3 实施进度（2026-10-22）

新增独立 CPU Worker 与数据库持久队列。网页提交有预算限制的公式搜索任务，支持进度、取消、幂等重试、检查点恢复和账户内产物导出。每个账户最多一个正在执行的挖掘任务，使用与普通 PA 请求不同的租约；公式、数据血缘、随机种子、词表/特征/引擎版本和代码指纹均保存。

候选为受限深度、命名 token 的因果 RPN 公式；资格和评分只用训练段，冻结获选者后再报告验证/测试。崩溃恢复有次数与累计时间限制，过期 Worker 不能覆盖新 Worker 的结果。词表或代码变更拒绝继续旧任务，不默默按新实现解释旧公式。

此版交付后台自动挖掘流程，计算器采用确定性的 CPU 公式搜索；没有迁移原 AlphaGPT 的 Transformer 训练、旧权重或 GPU 作业。原训练流程仍需另行修复并按新契约验证，不能直接接到网页执行。常驻 Worker 与 Vercel API 分开部署，使用方法见 [README](../README.md#后台公式挖掘)，细节见 [挖掘说明](ALPHAGPT_MINING.md)。

## 阶段 2 实施进度（2026-10-22）

PA 网页新增「历史研究」：个人 CSV/Birdeye 历史数据或现有快照转为固定数据集，按时间划分训练、验证、测试，训练段拟合单因子阈值，输出下一根开盘成交的费用后权益、逐笔账本、回撤、买入持有基准和固定成本情景。已保存结果按账户隔离，导出包含原始 OHLCV，可独立复算。

集成路径不再使用旧 AlphaGPT 的回填和 fitness 统计。历史缺口和零成交量会阻止评估；相同 CSV 行去重，冲突行拒绝；每账户内容哈希去重与记录引用采用数据库约束及事务。请求范围首尾缺失会保留并拒绝回测，不能以截短数据绕过。

本阶段限定连续现货、现金/多头、300～5000 根固定周期 K 线，不自动选参或挖掘公式。原 AlphaGPT 仓库的训练/数据代码保持原样，其既有问题没有被宣称修复；后续训练必须使用新的数据与因果特征契约。使用方法见 [README](../README.md#历史研究与回测)，计算口径见 [回测说明](ALPHAGPT_BACKTEST.md)，验证与未验证范围见 [验证记录](WEB_VALIDATION.md)。

## 阶段 1 实施进度（2026-10-13）

同快照因子面板已实现：个人 Birdeye Key、独立 Solana 合约身份、已收盘行情适配、带数据指纹的用户快照、因果因子和历史曲线、PA 记录内固定保存的量化证据。计算因子不需要 LLM Key，账户切换会清空原账户面板。历史记录重放保存值，快照过期不影响查看已有证据。

当前可从 OHLCV 计算五项因子；第六项 LIQ 因缺少历史流动性/FDV 而明确不可用。缺口、预热、未知市场日历分别标记，不以当前数据回填。新增适配器不依赖 PyTorch，未改动 AlphaGPT 原训练代码；原仓库的回测和训练问题仍须在后续阶段修正。`alphagpt-causal-v1` 与旧公式的特征口径不同，不能直接复用旧训练公式。

阶段 1 已通过离线数值、快照隔离及浏览器验证；Birdeye 的真实接口和套餐授权尚未使用有效账户验证。本次没有运行完整训练、调用付费模型或启动交易。使用说明见 [README](../README.md#alphagpt-量化因子面板)，计算口径见 [因子说明](ALPHAGPT_FACTORS.md)，具体验证结果见 [验证记录](WEB_VALIDATION.md)。

以下保留初始代码审查与路线；前三阶段以本文顶部的限定模型和当前代码为准。Transformer/GPU 训练及跨市场能力仍未接入。

## 整合方向

可以整合。建议将 PA Agent 保持为统一的网页入口、账户系统和价格行为分析器，将 AlphaGPT 的因子计算、公式执行、历史研究作为独立量化模块接入。最合适的首版是“同一行情上的 PA 分析 + 量化因子面板”，随后再增加经过修正的回测与异步因子挖掘。

AlphaGPT 的核心是用本地 PyTorch 模型生成因子公式，再通过回测奖励训练公式生成器；它不是用户填写 API Key 即可调用的外部聊天模型。用户自己的大模型 API 用于 PA 的分析与解释，AlphaGPT 训练需要另外分配服务器计算资源。

## 审查范围与证据

- AlphaGPT：`d851f2221dcaf4d53a707344f68ae6801e3e5af5`。
- PA Agent 网页版：`origin/codex/web-multiuser-vercel`，已核实提交前缀 `388ec29`。以下 `pa_agent/web/` 引用来自此分支。
- 整合评估仅阅读代码，并执行两项小型合成数据检查；没有请求外部行情 API、训练完整模型、调用付费 LLM 或启动交易。
- 下文为初始审查时的设计建议；已实现的前三阶段以本文顶部进度及当前代码为准，原 Transformer 后台训练接口尚未实现。

## 能复用什么

| 能力 | 已有实现 | 在 PA 网页版的落点 | 改造程度 |
|---|---|---|---|
| 行情采集 | `AlphaGPT/data_pipeline/providers/birdeye.py:26`、`:60` | 为 `pa_agent/web/sources.py:17` 的数据源目录及请求模型添加 Birdeye；将行情转成 `KlineFrame` | 中等：分页、限流、周期、币种与地址标识需要统一 |
| 六个基础因子 | `model_core/factors.py:158`；收益、流动性、K 线强弱、量能变化、均值偏离、成交量 | 图表旁展示数值、变化、历史分位和缺失标记 | 中等：必须先修正时间因果性；`pressure` 是 K 线实体/振幅代理量，并非真实买卖订单流 |
| 因子公式语言与执行 | `model_core/vocab.py:6`、`model_core/ops.py:25`、`model_core/vm.py:11` | 公式详情、已有公式在当前快照上的评分 | 中等：版本化词表，严格校验非负整数 token、长度、栈和数值有效性 |
| 12 维扩展因子 | `model_core/factors.py:95` | 后续可选因子库 | 当前主流程仍是六维；不能直接替换词表后沿用旧公式 |
| 回测评分 | `model_core/backtest.py:9` | 策略研究页的计算内核起点 | 较大：现有输出是研究用 fitness 和平均累计净对数收益近似，缺完整交易账本、权益曲线与样本外评估 |
| 自动因子挖掘 | `model_core/engine.py:12`、`:59` | 后台训练任务、进度页、公式版本库 | 较大：任务队列、数据快照、资源限制、验证集和产物管理 |
| 扫描与展示 | `strategy_manager/runner.py:151`、`dashboard/visualizer.py` | 只读候选标的列表与因子图表 | 可复用计算思想，不能直接启动当前实盘 runner |
| 交易执行 | `execution/trader.py:18`、`:50` | 首轮整合范围之外 | 独立产品工作：钱包隔离、签名授权、成交确认、对账和交易控制 |

`sigmoid` 把公式输出映射到 0～1，代码没有概率校准。页面应称“因子分数”，不能标成上涨概率、胜率或置信度。PA 的模型判断与量化分数应分别标注来源。

## 网页版已经具备的接入基础

该 Web 分支已有 FastAPI 登录、个人设置、每用户分析记录与经验库：`pa_agent/web/app.py:65` 读取会话用户；`store.py:24`、`:38` 的账户和条目表以 `user_id` 隔离；`store.py:83` 加密设置；`settings.py:89` 对浏览器只返回密钥是否已配置。

`pa_agent/web/analysis.py` 的 `analyze()` 已经把网页请求适配到原有 `TwoStageOrchestrator`，并通过 `AccountPrompts` 和 `AccountExperience` 隔离个人提示词与案例。整合不需要重写 PA 两阶段分析。首版可给分析记录加一个独立 `quant_evidence` 字段：保存公式版本、数据快照版本、因子值、计算时点和质量标记，网页同时展示；需要 LLM 解读时，再将有限的、可追溯的量化摘要加入提示词。量化模块失败应返回明确的缺失原因，不能用零分冒充有效结果。

需要同步修改的具体接口：

1. `web/sources.py:29` 的 `SourceRequest` 目前没有 Birdeye；`:30` 将 symbol 限制为 32 字符，无法接收常见的 44 字符 Solana mint 地址。建议新增独立 `instrument_id` / `chain` 字段，显示符号只作为标签。
2. `web/settings.py:11` 的 `SECRET_FIELDS` 尚无 Birdeye Key。若采用用户自带行情账户，应将 `birdeye_api_key` 纳入加密、修改、删除、脱敏、费用归属和请求限流。不要让所有租户自动继承管理员的环境变量凭据。
3. `app.py:347` 的 `/api/analyze` 当前要求个人 LLM Key；独立的因子查看和研究任务不必依赖 LLM。建议新增 `/api/quant/signals` 与 `/api/research/jobs`，按登录用户授权，而不是把训练绑到聊天接口。
4. 小型结果可以沿用 `store.put(uid, kind, payload)`；大型历史行情、训练产物应有专用表与对象存储，避免把整个训练张量塞进 `web_items` JSON。

## 不能直接用于可信回测的问题

以下是可定位到当前代码的限制，涉及结果解释和历史评估正确性，并非已证实的收益结论。

| 问题 | 代码证据 | 影响与修正方向 |
|---|---|---|
| 全时间轴归一化使用未来数据 | `factors.py:178` 的 median/MAD；`ops.py:15` 的 JUMP 全序列均值和标准差 | 历史信号会随未来数据变化；改为只使用当时可见历史的滚动/扩展窗口，并明确预热期 |
| 首尾循环连接 | `factors.py:171` 与 `:34` 使用 `torch.roll`；扩展因子也有同类写法 | 最早时点会引用样本末端；用显式滞后和有效掩码，丢弃预热数据 |
| 当前快照回填历史 | `providers/birdeye.py:63`、`:84` 将缺失历史 liquidity/fdv 替换为当前值 | 流动性过滤和历史成本可能含未来信息；保存 `observed_at` 与真实历史来源，无历史值时标记缺失，不能将快照当历史实测 |
| 同一数据筛选最优公式 | `engine.py:22` 载入一次；`:100` 同一数据评估并在 `:103` 选优 | 没有独立验证/测试切分；需要按时间切分、前推验证、训练窗口限定归一化，记录所有筛选次数 |
| 选币偏差 | `data_manager.py:25` 以当前热门币筛选；`data_loader.py:17` 没有历史时点可得的标的池 | 历史结果可能排除已经退市或失去热度的币；保存按时点的标的池和退出记录 |
| 缺口被填成数据 | `data_loader.py:34` 对所有列前向填充再填零 | 未上市期、停更期和缺量不应变成可交易样本；价格、成交量、流动性分别制定规则并保留掩码 |
| 历史请求不完整的风险 | `providers/birdeye.py:66` 单次请求整个区间，没有分页；`:99` 在持有 semaphore 时递归重试 429 | 可能截断历史、无限重试或耗尽 semaphore；按接口上限分窗、检查覆盖率、在许可释放后有限重试并遵循 Retry-After |
| 重复数据导致整批丢弃 | `db_manager.py:70` 用 COPY；`:77` 捕获 UniqueViolation 后忽略 | 任一重复行可使整批写入失败，随后日志仍可能把尝试数量当插入数量；改为暂存表加 upsert，并返回实际写入数量 |
| 多周期/来源会撞主键 | `db_manager.py:32` 主键只有 `(time,address)` | 不能混存不同链、交易池、周期和报价币种；引入规范的市场标识和复合唯一键 |
| 费用与阈值不统一 | `model_core/config.py:12` 与 `backtest.py:6` 的最小流动性不同；费用也分别硬编码 | 需要按市场统一配置、保存配置版本；现有简化价格冲击不是成交模拟或真实执行成本 |
| 回测统计含义有限 | `backtest.py:23` 汇总净对数收益；`:24` 将单根净收益低于 -5% 的次数称 big_drawdowns | 不是峰谷最大回撤，不能直接展示为账户收益率/标准回撤；补足权益曲线、交易账本、基准及费用敏感性分析 |
| 看板浮盈口径错误 | `dashboard/data_service.py:49` 与 `dashboard/app.py:64` 用最高价估算浮盈 | 最高价只适合追踪止盈参考；持仓估值应使用有时效标记的当前可成交价格，并正确处理部分卖出后的成本 |

合成数据复核：在现有 AlphaGPT 虚拟环境运行 CPU 检查，保持前四根 OHLCV 完全一致，只改变后四根，前四根因子出现变化，最大差值约 `7.3322`；第二根 RET 特征由 `2.1073` 变为 `0`。另一组固定历史前缀、改变后半段的输入令 JUMP 的首点从 `1.2485` 变为 `0`。这证实当前实现不满足“未来数据变化不影响过去信号”的因果性要求。该检查仅验证代码性质，不涉及真实行情或投资效果。

DexScreener 在此仓库中不是可用的历史替代后端：`providers/dexscreener.py:10` 的发现方法与 `:50` 的历史方法均返回空列表；DataManager 主流程固定调用 Birdeye。GeckoTerminal 也尚未实现。注册 Birdeye 账户后，仍需实际验证所购套餐对所需接口和区间的授权。

## 共用数据与多用户边界

建议共用一套标准行情结构，通过两个适配器分别提供给 PA 和 AlphaGPT：

```text
登录用户 → PA Web API → 权限与个人配置
                        ├─ 行情服务 → 不可变行情快照 → PA 两阶段分析 → 个人报告
                        └─ 研究任务队列 → 独立 CPU/GPU Worker → 因子/回测/公式产物
                                                   ↓
                                     相同用户的量化面板与报告证据
```

最少需要这些对象：

| 对象 | 建议字段与约束 |
|---|---|
| `instrument` | `id, asset_class, venue, chain, base_asset, quote_asset, pool_address, symbol, volume_unit`；地址和显示名称分开，避免同名币或同币不同池混淆 |
| `candle` | `instrument_id, provider, timeframe, ts_open_utc_ms, open, high, low, close, volume, closed, observed_at, quality_flags`；唯一键至少含市场、来源、周期、开盘时间 |
| `fundamentals_snapshot` | `instrument_id, observed_at, liquidity_usd, fdv_usd, source, historical_or_current`；与 OHLCV 分开，按可知时点关联 |
| `dataset_version` | `id, owner_id/visibility, market_scope, start, end, snapshot_hash, coverage, provenance, adjustments`；研究引用不可变版本 |
| `formula_version` | `id, owner_id, token_names/tokens, vocab_version, feature_version, code_revision, training_dataset_id, hyperparameters, seed, validation_metrics`；禁止只保存裸整数数组 |
| `research_job` | `id, owner_id, type, status, input_ids, limits, created_at, heartbeat, progress, error_code, output_ids`；所有读取/取消/重试均检查 owner |
| `analysis_quant_evidence` | `analysis_id, owner_id, snapshot_id, formula_version, as_of, factors, score, quality_flags`；固定证据，不能被后续重算悄悄替换 |

PA 的 `KlineBar.ts_open` 是 UTC 毫秒、列表按新到旧；AlphaGPT 张量按旧到新、形状 `[标的数, 因子数, 时间]`。适配器必须反转顺序、统一周期与计价单位、只纳入已收盘数据，并给 EMA/ATR 和量化因子留足预热样本。PA 用于分析的 50～500 根图表窗口不能直接代替跨标的、长时间训练集。

租户 ID 从验证后的会话取值，不能信任请求自带 `owner_id`。研究记录、公式、上传 CSV、导出文件、日志和任务事件均要隔离。共享公共行情缓存只在来源许可允许且不泄露私人数据的前提下开放；用户自己的数据源 Key 与限额保持独立。Worker 从任务授权上下文取得必要凭据，不把 Key 写入队列 payload、公式 JSON 或普通任务日志。

## 算力与部署

Web 分支的 `app.py:300` 使用 `asyncio.to_thread` 在 SSE 请求期运行分析，断线后取消；`vercel.json` 的 `maxDuration` 是 240 秒。这个机制适合受控的交互请求，不适合完整训练与持续行情采集。训练任务应进入持久队列，由独立的常驻 Worker 执行；网页只提交任务、显示进度和读取结果。即使网页继续部署在 Vercel，Worker 也可以部署在单独云主机或容器中。

`ModelConfig` 默认 `BATCH_SIZE=8192`、`TRAIN_STEPS=1000`、公式长度 12，`engine.py:87` 对批内每条公式逐个评估。完整训练的成本由币种数、K 线数、公式数和算子开销共同决定，不能根据一次 CPU 冒烟检查推断可支持多少用户。已有公式对少量标的推理可先测 CPU；完整训练先做小预算剖析，再决定是否使用 GPU。

建议限制每用户排队数量、同时任务数、最大币种与时间跨度、CPU/GPU 内存及运行时长，并支持取消、超时、重试、幂等提交、心跳和断点记录。现有全局 `best_meme_strategy.json`、`training_history.json`、`portfolio_state.json`、`STOP_SIGNAL` 不能被多用户共用；产物路径应由服务端生成并包含租户和任务标识。

## 市场适用性与执行边界

AlphaGPT 主流程固定 `CHAIN="solana"`、默认一分钟数据，因子与费用模型偏向链上币；PA 则覆盖黄金、外汇、A 股和期货。价格/量能类算子可复用，但不能把 Solana 的 liquidity/FDV 阈值、24 小时交易、链上滑点直接套到 A 股或黄金。跨市场需要分别处理交易日历、停牌、复权、涨跌停、T+1、合约乘数、换月、成交量单位和资金费用；无适用定义的因子应禁用，而不是填零。

`StrategyRunner` 在构造时创建交易组件，在循环中自动调用 `SolanaTrader.buy/sell`。PA 目前是分析辅助工具，因此整合只调用显式的研究/信号函数，不以启动 runner 的方式获取分数。本次没有启用交易。若以后确需实盘，应单独设计每用户钱包或外部签名、授权范围、订单幂等、成交回报、账户对账和暂停机制；LLM 结论本身不能成为绕过这些检查的授权。

## 建议实施顺序与粗估工作量

以下为一名熟悉 Python、Web 和量化数据的工程师的粗略人日估计；假设当前多用户 Web 基础可用、已有合法行情访问、无需重新设计整站。它们不是上线承诺，等待外部账号、数据补齐、部署审批或采购算力的时间另计。

| 阶段 | 交付与验收 | 粗估 |
|---|---|---|
| 1. 同一行情与个人量化面板 | Birdeye 个人设置、标准市场 ID、行情适配、因果特征修正；同快照上同时展示 PA 与因子，因子失败不会伪造结论；两用户互不可读 | 5～8 人日 |
| 2. 可复核历史研究 | 修复历史回填/缺口/重复写入；时间切分、成本配置、账本/权益曲线；样本外结果与基准明确分开，数据不足时拒绝评估 | 7～12 人日 |
| 3. 后台自动挖掘 | 持久任务、限额、进度/取消、公式版本、数据血缘和恢复；重启不丢任务、不跨租户泄漏、不阻塞普通 PA 分析 | 8～15 人日 |
| 4. 跨市场适配 | 按一个具体新增市场补齐字段、交易规则、费用与数据质量测试 | 每市场另估，通常至少 5～10 人日 |

前三阶段合计约 20～35 人日，是初始设计阶段的粗估，部分可并行。阶段 1～3 已按顶部范围实现，其中第三阶段采用有界 CPU 搜索，原 Transformer/GPU 训练另行验证。实盘执行仍不在此整合范围内。

关键验证包括：未来数据变化不改变历史信号；历史快照在更新后仍可复现；不同周期/来源互不覆盖；A 用户不能读取、取消或导出 B 用户的任务与数据；队列重试不重复执行或重复收费；回测只在明确的样本外窗口评价；网页断开和 Worker 重启不导致失控任务。实时接口、训练时长与数据套餐限制需要后续在实际运行环境验证。

## 开源许可

AlphaGPT 的 `LICENSE` 是 Apache-2.0；PA Agent 的 `LICENSE` 是 AGPL-3.0，`pyproject.toml` 声明 `AGPL-3.0-or-later`。整合时保留 AlphaGPT 的许可与归属信息；修改 PA 并对外提供网络服务时，需要处理 AGPL 对远程用户获取对应源码的要求。服务拆分本身不能作为忽略许可义务的依据。若计划以闭源商业服务发布，应先核实相关授权和产品边界，再确定发布方案。
