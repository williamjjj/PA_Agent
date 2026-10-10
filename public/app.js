import { consumeSSE } from "/stream.mjs";

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => Array.from(document.querySelectorAll(selector));
const state = { user: null, settings: null, data: null, record: null, catalog: [], busy: false,
  controller: null, tracking: null, page: 0, epoch: 0, prompts: [], treeTimer: null };
let chart, candles, ema, priceLines = [], toastTimer;
const labels = {
  cycle_position: "市场周期", direction: "市场方向", diagnosis_confidence: "诊断置信度",
  market_phase: "市场阶段", key_signals: "关键信号", htf_context: "高周期背景",
  entry_setup: "入场结构", gate_result: "闸门结果", detected_patterns: "识别形态",
  gate_trace: "闸门路径", bar_by_bar_summary: "逐棒分析", strategy_files_needed: "匹配策略",
  decision: "交易决策", order_direction: "交易方向", order_type: "订单类型",
  entry_price: "入场价", stop_loss_price: "止损价", take_profit_price: "目标一",
  take_profit_price_2: "目标二", reasoning: "判断依据", trade_confidence: "交易置信度",
  diagnosis_confidence_reasoning: "诊断依据", trade_confidence_reasoning: "信心依据",
  estimated_win_rate: "预估胜率", estimated_win_rate_reasoning: "胜率依据",
  key_factors: "关键因素", watch_points: "观察重点", risk_assessment: "风险评估",
  invalidation_condition: "失效条件", diagnosis_summary: "诊断摘要",
  bar_analysis: "K 线分析", decision_trace: "决策路径", terminal: "最终节点",
  next_bar_prediction: "下一根 K 线预期", next_cycle_prediction: "下一市场周期预期",
  entry_basis_bar: "入场依据 K 线", entry_basis_extreme: "入场依据极值", entry_rule: "入场规则",
  structure_levels: "结构价位", question: "判断问题", answer: "判断结果", reason: "依据",
  node_id: "节点", section: "阶段", bar_range: "K 线范围", outcome: "结果", label: "说明",
  bullish: "多头", bearish: "空头", neutral: "中性", spike: "极速趋势",
  normal_channel: "普通通道", micro_channel: "微通道", tight_channel: "窄通道",
  broad_channel: "宽通道", trading_range: "震荡区间", trending_tr: "趋势震荡",
  extreme_tr: "极端震荡", stable: "稳定", transition: "过渡", proceed: "通过", no_trade: "不交易",
};
function el(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined && text !== null) node.textContent = String(text);
  if (className) node.className = className;
  return node;
}
function text(selector, value) { $(selector).textContent = value; }
function display(value) {
  if (value === null || value === undefined) return "—";
  if (typeof value === "boolean") return value ? "是" : "否";
  return labels[value] || String(value);
}
function price(value) { return Number.isFinite(Number(value)) ? Number(value).toLocaleString("en-US", { maximumFractionDigits: 5 }) : "—"; }
function toast(message, error = false) {
  clearTimeout(toastTimer);
  const box = $("#toast"); box.textContent = message; box.className = "toast" + (error ? " error" : "");
  toastTimer = setTimeout(() => box.classList.add("hidden"), 6500);
}
async function api(url, method = "GET", body, signal) {
  const response = await fetch(url, { method, credentials: "same-origin", cache: "no-store", signal,
    headers: { ...(method === "GET" ? {} : { "x-pa-request": "1" }), ...(body ? { "Content-Type": "application/json" } : {}) },
    body: body ? JSON.stringify(body) : undefined });
  if (!response.ok) {
    let detail;
    try { detail = (await response.json()).detail; } catch { /* non-JSON gateway errors */ }
    const error = new Error(typeof detail === "string" ? detail : "请求失败（HTTP " + response.status + "）。");
    if (response.status === 401) { state.user = null; state.settings = null; stopTracking(); text("#account-name", "会话已过期"); openDialog("#account-dialog"); }
    error.status = response.status; throw error;
  }
  return response;
}
async function json(url, method, body) {
  const epoch = state.epoch;
  const data = await (await api(url, method, body)).json();
  if (epoch !== state.epoch) throw new Error("账户已切换，请重新操作。");
  return data;
}
function openDialog(id) { const d = $(id); if (!d.open) d.showModal(); }
function requireUser() {
  if (state.user) return true;
  openDialog("#account-dialog"); return false;
}
function setBusy(busy) {
  state.busy = busy;
  ["#analyze-button", "#market-button", "#import-button", "#demo-button", "#source-select", "#market-symbol",
   "#market-timeframe", "#market-count", "#market-exchange"].forEach((s) => { $(s).disabled = busy; });
  $("#chat-send").disabled = busy || state.record?.result?.status !== "complete";
  $("#cancel-button").classList.toggle("hidden", !busy);
  $("#analysis-progress").classList.toggle("hidden", !busy);
}
function stopTracking() {
  clearInterval(state.tracking); state.tracking = null; $("#tracking").checked = false;
}
function clearResults() {
  clearInterval(state.treeTimer);
  state.record = null;
  $("#analysis-summary").className = "empty-state";
  $("#analysis-summary").replaceChildren(el("span", "⌁"), el("strong", "等待新一轮分析"), el("p", "分析始终使用当前图表中的已收盘 K 线。"));
  for (const id of ["diagnosis", "decision", "prediction"]) $("#tab-" + id).replaceChildren(el("p", "完成分析后显示结果。", "empty-copy"));
  $("#decision-tree").replaceChildren(); $("#raw-result").textContent = "";
  $("#reasoning-stream").textContent = ""; $("#content-stream").textContent = "";
  $("#chat-history").replaceChildren(el("p", "完成分析后可继续追问。", "empty-copy"));
  $("#save-case").disabled = true; $("#export-button").disabled = true; $("#chat-send").disabled = true; $("#play-tree").disabled = true;
  text("#analysis-state", "待分析"); text("#usage", "分析所用 Token 会在此显示");
  for (const stage of [1, 2]) { $("#stage-" + stage).className = "stage"; text("#stage-" + stage + "-status", "待开始"); }
  if (candles) priceLines.forEach((line) => candles.removePriceLine(line));
  priceLines = [];
}
function activateTab(name) {
  $$(".tabs button").forEach((b) => b.setAttribute("aria-selected", String(b.dataset.tab === name)));
  $$(".tab-content").forEach((n) => n.classList.toggle("hidden", n.id !== "tab-" + name));
}
function setupChart() {
  const container = $("#chart");
  chart = LightweightCharts.createChart(container, {
    layout: { background: { color: "#111923" }, textColor: "#8295a9", fontSize: 10 },
    grid: { vertLines: { color: "#1b2735" }, horzLines: { color: "#1b2735" } },
    rightPriceScale: { borderColor: "#263445" }, timeScale: { borderColor: "#263445", timeVisible: true, secondsVisible: false },
    crosshair: { mode: 0 }, autoSize: true,
  });
  candles = chart.addSeries(LightweightCharts.CandlestickSeries, {
    upColor: "#62d6af", downColor: "#e77d80", borderVisible: false, wickUpColor: "#62d6af", wickDownColor: "#e77d80",
  });
  ema = chart.addSeries(LightweightCharts.LineSeries, { color: "#d7b568", lineWidth: 1, priceLineVisible: false, lastValueVisible: false });
  chart.subscribeCrosshairMove((param) => {
    const bar = state.data?.bars.find((b) => b.time === param.time);
    if (bar) text("#ohlc", "K" + bar.seq + "  O " + price(bar.open) + "  H " + price(bar.high) + "  L " + price(bar.low) + "  C " + price(bar.close));
  });
}
function renderChart(data, preserve = false) {
  if (!preserve) clearResults();
  state.data = data;
  if (!chart) setupChart();
  candles.setData(data.bars.map((b) => ({ time: b.time, open: b.open, high: b.high, low: b.low, close: b.close })));
  ema.setData(data.bars.filter((b) => b.ema !== null).map((b) => ({ time: b.time, value: b.ema })));
  chart.timeScale().fitContent();
  const last = data.bars.at(-1), change = (last.close - last.open) / last.open * 100;
  text("#symbol-title", data.symbol); text("#instrument-detail", data.timeframe + " · " + data.bars.length + " 根已收盘 K 线");
  const source = state.catalog.find((s) => s.id === data.source)?.label || (data.source === "demo" ? "模拟示例" : "CSV 导入");
  text("#source-badge", source); text("#latest-price", price(last.close));
  text("#price-change", (change >= 0 ? "+" : "") + change.toFixed(2) + "%");
  $("#price-change").className = change >= 0 ? "positive" : "negative";
  text("#range-price", price(Math.max(...data.bars.map((b) => b.high))) + " / " + price(Math.min(...data.bars.map((b) => b.low))));
  text("#atr-value", last.atr === null ? "—" : price(last.atr));
  text("#data-summary", source + " · " + data.bars.length + " 根");
  text("#source-note", new Date(last.time * 1000).toLocaleString("zh-CN"));
  text("#ohlc", "K1  O " + price(last.open) + "  H " + price(last.high) + "  L " + price(last.low) + "  C " + price(last.close));
}
function download(name, content, type) {
  const link = document.createElement("a"), url = URL.createObjectURL(new Blob([content], { type }));
  link.href = url; link.download = name; link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
}
function csvText(data) {
  return ["time,open,high,low,close,volume", ...data.bars.map((b) => [b.time, b.open, b.high, b.low, b.close, b.volume].join(","))].join("\n");
}
function dataTree(data, title = "详细结果", opened = false) {
  const box = el("details", null, "data-tree"); box.open = opened;
  box.append(el("summary", title));
  if (data === null || typeof data !== "object") { box.append(el("p", display(data))); return box; }
  const dl = el("dl");
  for (const [key, value] of Object.entries(data)) {
    const title = Array.isArray(data) ? "#" + (Number(key) + 1) : (labels[key] || key);
    if (value && typeof value === "object") box.append(dataTree(value, title));
    else { dl.append(el("dt", title), el("dd", display(value))); }
  }
  box.insertBefore(dl, box.children[1] || null); return box;
}
function renderObject(target, obj, keyFields) {
  const container = $(target); container.replaceChildren();
  if (!obj) { container.append(el("p", "本次分析没有生成该阶段结果。", "empty-copy")); return; }
  const grid = el("div", null, "result-fields");
  for (const key of keyFields) {
    if (obj[key] === undefined) continue;
    const box = el("div", null, "result-field");
    box.append(el("span", labels[key] || key), el("strong", display(obj[key]))); grid.append(box);
  }
  container.append(grid, dataTree(obj, "完整结构化结果", false));
}
function renderTree(result) {
  const container = $("#decision-tree"); container.replaceChildren();
  const nodes = [...(result.stage1?.gate_trace || []), ...(result.stage2?.decision_trace || [])];
  nodes.forEach((node, index) => {
    const box = el("details", null, "trace-node"); box.dataset.index = String(index);
    box.append(el("summary", (node.node_id || index + 1) + " · " + (node.question || node.section || "判断节点")),
      el("span", node.skipped ? "已跳过" : display(node.answer), "answer"), el("p", node.reason || node.reasoning || ""));
    if (node.bar_range) box.append(el("small", node.bar_range, "muted"));
    container.append(box);
  });
  if (!nodes.length) container.append(el("p", "本次分析未返回决策路径。", "empty-copy"));
  $("#play-tree").disabled = !nodes.length;
}
function renderChat() {
  const box = $("#chat-history"); box.replaceChildren();
  for (const turn of state.record?.chat || []) {
    box.append(el("div", turn.question, "chat-turn user"));
    const reply = el("div", turn.content, "chat-turn assistant");
    if (turn.reasoning) reply.append(dataTree({ reasoning: turn.reasoning }, "查看推理过程"));
    reply.append(el("small", "Token " + (turn.usage?.total_tokens || 0))); box.append(reply);
  }
  if (!box.childElementCount) box.append(el("p", "可以围绕当前分析继续追问。", "empty-copy"));
  box.scrollTop = box.scrollHeight;
}
function renderRecord(record) {
  state.record = record;
  const r = record.result, good = r.status === "complete", decision = r.stage2?.decision || {};
  text("#analysis-state", good ? "分析完成" : "未完成");
  $("#analysis-summary").className = "empty-state has-result" + (good ? "" : " failed");
  $("#analysis-summary").replaceChildren(el("strong", good ? [display(decision.order_direction), display(decision.order_type)].join(" · ") : "分析未完成"),
    el("p", r.error || decision.reasoning || "详见下方结构化分析结果。"));
  for (const [num, done] of [[1, !!r.stage1], [2, good]]) {
    $("#stage-" + num).className = "stage " + (done ? "done" : "failed");
    text("#stage-" + num + "-status", done ? "已完成并校验" : "未完成");
  }
  text("#usage", "Token " + (r.usage?.total_tokens || 0).toLocaleString() + " · " + (r.meta.model || "") + (r.meta.incremental_bars ? " · 增量 " + r.meta.incremental_bars + " 根" : ""));
  renderObject("#tab-diagnosis", r.stage1, ["cycle_position", "direction", "diagnosis_confidence", "market_phase", "gate_result"]);
  renderObject("#tab-decision", r.stage2, []);
  if (r.stage2) {
    const grid = el("div", null, "result-fields");
    for (const key of ["order_type", "order_direction", "trade_confidence", "entry_price", "stop_loss_price", "take_profit_price"]) {
      if (decision[key] !== undefined) { const item = el("div", null, "result-field"); item.append(el("span", labels[key]), el("strong", display(decision[key]))); grid.append(item); }
    }
    $("#tab-decision").prepend(grid);
  }
  renderTree(r);
  const predictions = $("#tab-prediction"); predictions.replaceChildren();
  for (const key of ["next_bar_prediction", "next_cycle_prediction"]) {
    if (r.stage2?.[key]) predictions.append(dataTree(r.stage2[key], labels[key], true));
  }
  if (!predictions.childElementCount) predictions.append(el("p", "本次未生成预测；下一根 K 线预测可在个人设置中开启。", "empty-copy"));
  text("#raw-result", JSON.stringify(record, null, 2));
  if (record.record) {
    text("#reasoning-stream", [record.record.stage1_response?.reasoning_content, record.record.stage2_response?.reasoning_content].filter(Boolean).join("\n\n"));
    text("#content-stream", [record.record.stage1_response?.content, record.record.stage2_response?.content].filter(Boolean).join("\n\n"));
  }
  $("#export-button").disabled = false; $("#save-case").disabled = !good;
  $("#chat-send").disabled = !good || state.busy;
  renderChat();
  priceLines.forEach((line) => candles.removePriceLine(line)); priceLines = [];
  if (good) for (const [key, color] of [["entry_price", "#63e4ba"], ["stop_loss_price", "#f58e8e"], ["take_profit_price", "#70b4ef"], ["take_profit_price_2", "#9b94ed"]]) {
    const value = Number((r.chart_decision || decision)[key]);
    if (Number.isFinite(value) && value > 0) priceLines.push(candles.createPriceLine({ price: value, color, lineWidth: 1, lineStyle: 2, axisLabelVisible: true, title: labels[key] }));
  }
}
function onStage(stage) {
  const number = /^Stage1/.test(stage) ? 1 : /^Stage2/.test(stage) ? 2 : null;
  if (number) {
    const failed = /Failed/.test(stage), done = /Done/.test(stage);
    $("#stage-" + number).className = "stage " + (failed ? "failed" : done ? "done" : "active");
    const message = failed ? "校验或调用失败" : done ? "已完成并校验" : /Retry/.test(stage) ? "校验重试中" : "模型分析中";
    text("#stage-" + number + "-status", message); text("#analysis-hint", "阶段 " + number + " · " + message);
  }
  if (/Retry/.test(stage) && state.settings?.cancel_keep_analysis_on_retry) stopTracking();
}
async function analyze() {
  if (!requireUser() || state.busy || !state.data) return;
  if (!state.settings?.api_key_configured) { await openSettings(); toast("请先保存个人模型 API Key。", true); return; }
  const epoch = state.epoch, previous = state.record?.id, data = state.data;
  const payload = data.snapshot_id ? { snapshot_id: data.snapshot_id } : { data: { csv: data.csv || csvText(data), symbol: data.symbol, timeframe: data.timeframe, source: data.source } };
  if ($("#incremental").checked && previous) payload.previous_id = previous;
  clearResults(); setBusy(true); state.controller = new AbortController();
  text("#analysis-state", "分析中"); text("#analysis-hint", "连接个人模型服务…"); activateTab("stream");
  let result;
  try {
    const response = await api("/api/analyze", "POST", payload, state.controller.signal);
    await consumeSSE(response, (event) => {
      if (epoch !== state.epoch) return;
      if (event.event === "stage") onStage(event.stage);
      if (event.event === "token") { const output = event.kind === "reasoning" ? $("#reasoning-stream") : $("#content-stream"); output.append(document.createTextNode(event.text)); output.scrollTop = output.scrollHeight; }
      if (event.event === "error") throw new Error(event.message);
      if (event.event === "result") result = event.data;
    }, state.controller.signal);
    if (epoch !== state.epoch) return;
    if (!result) throw new Error("连接已结束，但没有收到最终分析结果；请在分析记录中检查。");
    const record = await json("/api/records/" + result.id);
    if (epoch !== state.epoch) return;
    renderRecord(record); activateTab(result.status === "complete" ? "decision" : "raw");
    if (result.status !== "complete") stopTracking();
    else if ($("#tracking").checked) toast("本轮分析完成，等待下一根 K 线收盘。");
  } catch (error) {
    if (epoch !== state.epoch) return;
    stopTracking(); text("#analysis-state", error.name === "AbortError" ? "已停止" : "分析中断");
    toast(error.name === "AbortError" ? "已停止请求。已完成的阶段可在分析记录中查看。" : error.message, error.name !== "AbortError");
  } finally {
    if (epoch === state.epoch) { state.controller = null; setBusy(false); }
  }
}
async function marketFetch(request, preserve = false) {
  const epoch = state.epoch;
  const result = await json("/api/market", "POST", request);
  if (epoch !== state.epoch) return null;
  renderChart(result, preserve); return result;
}
function sourceChanged() {
  stopTracking();
  const source = state.catalog.find((x) => x.id === $("#source-select").value);
  if (!source) return;
  $("#market-symbol").value = source.symbol;
  $("#market-timeframe").replaceChildren(...source.timeframes.map((v) => { const option = el("option", v); option.value = v; return option; }));
  $("#market-timeframe").value = source.timeframes.includes("15m") ? "15m" : source.timeframes[0];
  $("#exchange-field").classList.toggle("hidden", source.id !== "tradingview");
  text("#source-description", source.id === "mt5" ? "请在已登录 MT5 的 Windows 电脑运行桥接程序，每 60 秒同步到当前账户，详见 README。" : source.id === "tushare" ? "需在个人设置中填写 Tushare Token；分钟行情依账户权限提供。" : source.id === "tradingview" ? "填写准确交易所与品种代码；匿名访问或账户权限可能限制行情。" : source.id === "yfinance" ? "例如 GC=F、BTC-USD、AAPL；期货行情可能延迟，1 分钟历史窗口较短。" : "A 股与期货行情受交易时段和数据源限制，空数据会明确提示。");
}
async function refreshStatus() {
  const data = await json("/api/status");
  state.user = data.user;
  text("#account-name", state.user?.username || "访客模式"); text("#avatar", state.user?.username.slice(0, 1).toUpperCase() || "访");
  text("#account-note", state.user ? "个人账户 · 数据独立" : "登录后使用个人模型");
  $("#logout-button").classList.toggle("hidden", !state.user);
  $("#config-notice").classList.toggle("hidden", data.configured);
  text("#config-notice", data.message || "");
  $('#account-form input[value="register"]').disabled = !data.registration_open;
  if (state.user) {
    state.settings = await json("/api/settings");
    text("#model-badge", state.settings.model + (state.settings.api_key_configured ? " · 已配置" : " · 缺少凭据"));
    $("#market-count").value = state.settings.analysis_bar_count;
  } else { state.settings = null; text("#model-badge", "尚未配置模型"); }
}
const settingsGroups = [
  ["模型连接", [
    ["base_url", "Base URL", "text"], ["model", "模型标识", "text"], ["api_key", "API Key", "password"],
    ["thinking", "开启模型推理（供应商需支持）", "checkbox"], ["reasoning_effort", "推理强度", ["low", "medium", "high", "max"]],
    ["context_window", "上下文窗口 / Token", "number", 8192, 2000000], ["max_tokens", "最大输出 / Token", "number", 1024, 64000],
  ]],
  ["分析与持续跟踪", [
    ["decision_stance", "交易倾向", ["conservative", "balanced", "aggressive", "extreme_aggressive"]],
    ["analysis_bar_count", "默认 K 线数量", "number", 50, 500], ["enable_next_bar_prediction", "生成下一根 K 线预测", "checkbox"],
    ["incremental_max_new_bars", "最多增量 K 线（0 关闭）", "number", 0, 50],
    ["decision_confidence_threshold", "机会提醒置信度阈值", "number", 0, 100],
    ["structure_flip_cooldown_bars", "反向方案冷却 K 线", "number", 1, 50],
    ["cancel_keep_analysis_on_retry", "校验重试时停止自动跟踪", "checkbox"],
  ]],
  ["提示词与校验", [
    ["prompt.experience_max_entries", "经验引用数量（0 关闭）", "number", 0, 10],
    ["prompt.experience_max_chars_per_entry", "单条经验字符上限", "number", 100, 4000],
    ["prompt.stage2_load_full_strategy_library", "加载完整策略库", "checkbox"],
    ["prompt.stage1_inject_pattern_briefs", "注入形态判定提示", "checkbox"],
    ["validation.normalization_mode", "校验模式", ["lenient", "strict"]],
    ["validation.retry_enabled", "失败后自动重试", "checkbox"],
    ["validation.retry_max", "最大格式重试次数", "number", 0, 5],
    ["validation.retry_max_semantic", "最大语义重试次数", "number", 0, 3],
    ["validation.retry_stage2", "允许阶段二重试", "checkbox"],
    ["validation.stage1_coherence_checks", "阶段一一致性检查", "checkbox"],
    ["validation.stage2_coherence_checks", "阶段二一致性检查", "checkbox"],
    ["validation.trace_semantic_checks", "决策路径语义检查", "checkbox"],
    ["validation.strict_bar_by_bar_features", "严格逐棒特征检查", "checkbox"],
    ["validation.disable_truncation_repair", "禁用截断 JSON 修复", "checkbox"],
  ]],
  ["数据源凭据", [
    ["tradingview_username", "TradingView 用户名（可选）", "text"],
    ["tradingview_password", "TradingView 密码（可选）", "password"],
    ["tushare_token", "Tushare Token", "password"], ["kline_adjust", "A 股复权方式", ["qfq", "hfq", "none"]],
  ]],
  ["分析通知", [
    ["notify_enabled", "向已配置机器人发送分析结果", "checkbox"],
    ["notify_on_order_only", "仅在存在交易机会时发送", "checkbox"],
    ["feishu_webhook", "飞书 Webhook", "password"], ["feishu_secret", "飞书签名密钥（可选）", "password"],
    ["pushplus_token", "PushPlus Token（可选）", "password"],
  ]],
];
function getPath(obj, path) { return path.split(".").reduce((v, k) => v?.[k], obj); }
function setPath(obj, path, value) {
  const parts = path.split("."), last = parts.pop(); let target = obj;
  for (const p of parts) target = target[p] ||= {};
  target[last] = value;
}
async function openSettings() {
  if (!requireUser()) return;
  state.settings = await json("/api/settings");
  const container = $("#settings-fields"); container.replaceChildren();
  for (const [title, fields] of settingsGroups) {
    const section = el("section", null, "form-section"), grid = el("div", null, "form-grid");
    section.append(el("h3", title), grid);
    for (const [name, labelText, type, min, max] of fields) {
      const label = el("label", labelText), input = el(Array.isArray(type) ? "select" : "input");
      input.name = name; input.id = "setting-" + name.replaceAll(".", "-");
      if (Array.isArray(type)) {
        for (const v of type) { const option = el("option", display(v)); option.value = v; input.append(option); }
        input.value = getPath(state.settings, name);
      } else {
        input.type = type;
        if (type === "checkbox") { input.checked = !!getPath(state.settings, name); label.className = "check"; }
        else if (type === "password") {
          input.value = ""; input.autocomplete = "new-password";
          input.placeholder = state.settings[name + "_configured"] ? "已保存；留空保留" : "尚未配置";
        } else input.value = getPath(state.settings, name) ?? "";
        if (type === "number") { input.min = min; input.max = max; input.required = true; }
      }
      label.append(input); grid.append(label);
      if (type === "password" && state.settings[name + "_configured"]) {
        const clear = el("label", "清除此凭据", "check secret-clear"), checkbox = el("input"); checkbox.type = "checkbox"; checkbox.name = "clear__" + name; clear.prepend(checkbox); label.append(clear);
      }
    }
    container.append(section);
  }
  text("#settings-error", ""); $("#provider-preset").value = ""; openDialog("#settings-dialog");
}
async function showHistory() {
  if (!requireUser()) return;
  const rows = await json("/api/records?offset=" + state.page * 25);
  const list = $("#history-list"); list.replaceChildren();
  rows.forEach((r) => {
    const row = el("div", null, "history-row"), info = el("div"), actions = el("div");
    info.append(el("strong", r.meta.symbol + " · " + r.meta.timeframe + " · " + (r.status === "complete" ? "已完成" : "未完成")),
      el("p", new Date(r.created * 1000).toLocaleString() + " · " + r.meta.model + " · " + r.meta.source));
    const view = el("button", "查看", "secondary"), remove = el("button", "删除", "quiet");
    view.onclick = () => guard(async () => { if (state.busy) throw new Error("请先停止当前请求。"); stopTracking(); const data = await json("/api/records/" + r.id); renderChart(data.chart); renderRecord(data); $("#history-dialog").close(); });
    remove.onclick = () => guard(async () => { if (!confirm("删除这份分析及其追问记录？")) return; await json("/api/records/" + r.id, "DELETE"); if (state.record?.id === r.id) clearResults(); await showHistory(); });
    actions.append(view, remove); row.append(info, actions); list.append(row);
  });
  if (!rows.length) list.append(el("p", "这一页还没有分析记录。", "empty-copy"));
  $("#history-prev").disabled = state.page === 0; $("#history-next").disabled = rows.length < 25; text("#history-page", "第 " + (state.page + 1) + " 页");
  openDialog("#history-dialog");
}
async function showExperience() {
  if (!requireUser()) return;
  const rows = await json("/api/experience"), list = $("#experience-list"); list.replaceChildren();
  rows.forEach((r) => {
    const row = el("div", null, "history-row"), info = el("div"), remove = el("button", "删除", "quiet");
    info.append(el("strong", display(r.payload.cycle_position) + " · " + (r.payload.case_type === "success" ? "成功" : "失败")),
      el("p", r.payload.content.notes), dataTree(r.payload.content, "案例内容"));
    remove.onclick = () => guard(async () => { if (!confirm("删除这条经验？")) return; await json("/api/experience/" + r.id, "DELETE"); await showExperience(); });
    row.append(info, remove); list.append(row);
  });
  if (!rows.length) list.append(el("p", "在已完成的分析中点击“加入经验库”，保存复盘案例。", "empty-copy"));
  openDialog("#experience-dialog");
}
async function showPrompts() {
  if (!requireUser()) return;
  state.prompts = await json("/api/prompts");
  $("#prompt-select").replaceChildren(...state.prompts.map((p) => { const option = el("option", p.name + (p.modified ? " · 已修改" : "")); option.value = p.name; return option; }));
  $("#prompt-editor").value = state.prompts[0]?.content || ""; openDialog("#prompts-dialog");
}
async function guard(action) {
  try { await action(); } catch (error) { toast(error.message, true); }
}

$$("[data-close]").forEach((button) => button.onclick = () => button.closest("dialog").close());
$$("[data-tab]").forEach((button) => button.onclick = () => activateTab(button.dataset.tab));
$$("[data-action]").forEach((button) => button.onclick = () => guard(async () => {
  const action = button.dataset.action;
  if (action === "settings") await openSettings();
  if (action === "history") { state.page = 0; await showHistory(); }
  if (action === "experience") await showExperience();
  if (action === "prompts") await showPrompts();
  if (action === "workspace") window.scrollTo({ top: 0, behavior: "smooth" });
}));
$("#account-button").onclick = () => guard(async () => state.user ? openSettings() : openDialog("#account-dialog"));
$("#account-form").onchange = () => { const register = new FormData($("#account-form")).get("mode") === "register"; $("#invite-field").classList.toggle("hidden", !register); $('#account-form input[name="password"]').autocomplete = register ? "new-password" : "current-password"; };
$("#account-form").onsubmit = async (event) => {
  event.preventDefault(); text("#account-error", "");
  const form = new FormData(event.target), button = event.target.querySelector('button[type="submit"]'); button.disabled = true;
  try {
    await json(form.get("mode") === "register" ? "/api/register" : "/api/session", "POST",
      { username: form.get("username"), password: form.get("password"), invite_code: form.get("invite_code") || "" });
    state.epoch++; stopTracking(); renderChart(await json("/api/demo")); await refreshStatus();
    $("#account-dialog").close(); event.target.reset(); toast("已登录 " + state.user.username);
  } catch (error) { text("#account-error", error.message); } finally { button.disabled = false; }
};
$("#logout-button").onclick = () => guard(async () => {
  state.epoch++; state.controller?.abort(); state.controller = null; setBusy(false); stopTracking();
  await json("/api/logout", "POST", {}); state.record = null; state.settings = null; state.prompts = [];
  $("#case-form textarea, #chat-input, #old-password, #new-password").forEach((input) => { input.value = ""; });
  $("#settings-fields").replaceChildren(); $("#prompt-editor").value = ""; $("#history-list").replaceChildren(); $("#experience-list").replaceChildren();
  renderChart(await json("/api/demo")); await refreshStatus(); toast("已退出登录。");
});
$("#settings-form").onsubmit = async (event) => {
  event.preventDefault(); text("#settings-error", "");
  const changes = {}, clear = [];
  for (const [, fields] of settingsGroups) for (const [name, , type] of fields) {
    const input = event.target.elements.namedItem(name);
    setPath(changes, name, type === "checkbox" ? input.checked : type === "number" ? Number(input.value) : input.value.trim());
    if (event.target.elements.namedItem("clear__" + name)?.checked) clear.push(name);
  }
  try { state.settings = await json("/api/settings", "PUT", { settings: changes, clear_secrets: clear }); stopTracking(); await refreshStatus(); $("#settings-dialog").close(); toast("个人设置已保存。"); }
  catch (error) { text("#settings-error", error.message); }
};
$("#provider-preset").onchange = (event) => {
  const presets = { deepseek: ["https://api.deepseek.com/v1", "deepseek-chat"], openai: ["https://api.openai.com/v1", "gpt-4.1"], gateway: ["https://ai-gateway.vercel.sh/v1", "deepseek/deepseek-v4.1-flash"] };
  const values = presets[event.target.value]; if (!values) return;
  $("#setting-base_url").value = values[0]; $("#setting-model").value = values[1];
};
$("#change-password").onclick = () => guard(async () => {
  await json("/api/password", "POST", { old_password: $("#old-password").value, new_password: $("#new-password").value });
  $("#old-password").value = ""; $("#new-password").value = ""; toast("密码已修改，其他登录会话已注销。");
});
$("#source-select").onchange = sourceChanged;
["#market-symbol", "#market-timeframe", "#market-count", "#market-exchange"].forEach((s) => $(s).onchange = stopTracking);
$("#market-form").onsubmit = (event) => {
  event.preventDefault();
  if (!requireUser() || state.busy) return;
  stopTracking();
  guard(async () => { const form = new FormData(event.target); setBusy(true); try { await marketFetch({ source: form.get("source"), symbol: form.get("symbol").trim(), timeframe: form.get("timeframe"), exchange: form.get("exchange").trim() || "OANDA", count: Number(form.get("count")) }); } finally { setBusy(false); } });
};
$("#demo-button").onclick = () => guard(async () => { stopTracking(); renderChart(await json("/api/demo")); });
$("#import-button").onclick = () => { text("#import-error", ""); openDialog("#import-dialog"); };
$("#import-form").onsubmit = async (event) => {
  event.preventDefault(); text("#import-error", "");
  try {
    const form = new FormData(event.target), file = form.get("file");
    if (!file?.size || file.size > 500000) throw new Error("请选择不超过 500 KB 的 CSV 文件。");
    const csv = await file.text(), data = await json("/api/import", "POST", { csv, symbol: form.get("symbol").trim(), timeframe: form.get("timeframe"), source: "csv" });
    stopTracking(); renderChart({ ...data, csv }); $("#import-dialog").close(); toast("已导入 " + data.bars.length + " 根 K 线。");
  } catch (error) { text("#import-error", error.message); }
};
$("#template-button").onclick = () => guard(async () => { const data = await json("/api/demo"); download("pa-agent-example-15m.csv", data.csv, "text/csv"); });
$("#csv-export").onclick = () => { if (state.data) download("pa-agent-" + state.data.symbol.replace(/[^a-z0-9]/gi, "_") + ".csv", csvText(state.data), "text/csv"); };
$("#fit-chart").onclick = () => chart?.timeScale().fitContent();
$("#ema-toggle").onchange = (event) => ema?.applyOptions({ visible: event.target.checked });
$("#analyze-button").onclick = analyze;
$("#cancel-button").onclick = () => { stopTracking(); state.controller?.abort(); };
$("#tracking").onchange = async (event) => {
  if (!event.target.checked) { stopTracking(); return; }
  if (!requireUser() || !state.data?.request || !state.settings?.api_key_configured) {
    stopTracking(); toast("请先获取在线行情并配置模型，再开启持续跟踪。", true); return;
  }
  const request = { ...state.data.request };
  state.tracking = setInterval(() => guard(async () => {
    if (state.busy || document.hidden) return;
    const last = state.data.bars.at(-1).time;
    try {
      setBusy(true); const next = await marketFetch(request, true); setBusy(false);
      if (next && next.bars.at(-1).time > last) await analyze();
    } catch (error) { setBusy(false); stopTracking(); throw error; }
  }), 60000);
  toast("已开启：新 K 线收盘后会自动调用个人模型。保持本页面打开。");
};
$("#export-button").onclick = () => { if (state.record) download("pa-analysis-" + state.record.id + ".json", JSON.stringify(state.record, null, 2), "application/json"); };
$("#play-tree").onclick = () => {
  clearInterval(state.treeTimer); const nodes = $$("#decision-tree .trace-node"); let i = 0;
  nodes.forEach((n) => { n.classList.remove("active"); n.open = false; });
  state.treeTimer = setInterval(() => {
    if (i >= nodes.length) { clearInterval(state.treeTimer); return; }
    nodes.forEach((n) => n.classList.remove("active")); nodes[i].classList.add("active"); nodes[i].open = true; i++;
  }, 850);
};
$("#chat-form").onsubmit = async (event) => {
  event.preventDefault(); if (state.busy || !state.record || !requireUser()) return;
  const question = $("#chat-input").value.trim(); if (!question) return;
  const epoch = state.epoch; state.controller = new AbortController(); setBusy(true);
  const answer = el("div", "", "chat-turn assistant"); $("#chat-history").append(el("div", question, "chat-turn user"), answer);
  let completed;
  try {
    const response = await api("/api/records/" + state.record.id + "/chat", "POST", { question }, state.controller.signal);
    await consumeSSE(response, (event) => {
      if (epoch !== state.epoch) return;
      if (event.event === "token" && event.kind === "content") { answer.append(document.createTextNode(event.text)); $("#chat-history").scrollTop = $("#chat-history").scrollHeight; }
      if (event.event === "chat_result") completed = event.data;
      if (event.event === "error") throw new Error(event.message);
    }, state.controller.signal);
    if (epoch !== state.epoch) return;
    if (!completed) throw new Error("连接已结束，但未收到完整追问结果。");
    state.record.chat.push(completed); renderChat(); text("#raw-result", JSON.stringify(state.record, null, 2)); $("#chat-input").value = "";
  } catch (error) { if (epoch === state.epoch) { renderChat(); toast(error.name === "AbortError" ? "追问已停止，输入已保留。" : error.message, true); } }
  finally { if (epoch === state.epoch) { state.controller = null; setBusy(false); } }
};
$("#save-case").onclick = () => { if (!state.record) return; $('#case-form select[name="cycle_position"]').value = state.record.result.stage1?.cycle_position || "unknown"; openDialog("#case-dialog"); };
$("#case-form").onsubmit = async (event) => {
  event.preventDefault(); text("#case-error", "");
  try { const form = Object.fromEntries(new FormData(event.target)); await json("/api/experience", "POST", { ...form, record_id: state.record.id }); $("#case-dialog").close(); event.target.reset(); toast("已保存复盘案例。"); }
  catch (error) { text("#case-error", error.message); }
};
$("#history-prev").onclick = () => guard(async () => { state.page = Math.max(0, state.page - 1); await showHistory(); });
$("#history-next").onclick = () => guard(async () => { state.page++; await showHistory(); });
$("#prompt-select").onchange = () => { $("#prompt-editor").value = state.prompts.find((p) => p.name === $("#prompt-select").value)?.content || ""; };
$("#prompt-save").onclick = () => guard(async () => {
  const name = $("#prompt-select").value, settings = await json("/api/settings");
  const overrides = { ...settings.prompt_overrides, [name]: $("#prompt-editor").value };
  state.settings = await json("/api/settings", "PUT", { settings: { prompt_overrides: overrides } });
  const item = state.prompts.find((p) => p.name === name); item.content = overrides[name]; item.modified = true; toast("已保存当前账户的提示词。");
});
$("#prompt-reset").onclick = () => guard(async () => {
  const name = $("#prompt-select").value, settings = await json("/api/settings");
  delete settings.prompt_overrides[name]; await json("/api/settings", "PUT", { settings: { prompt_overrides: settings.prompt_overrides } });
  await showPrompts(); $("#prompt-select").value = name; $("#prompt-select").dispatchEvent(new Event("change")); toast("此提示词已恢复默认值。");
});
document.addEventListener("visibilitychange", () => { if (document.hidden && state.treeTimer) clearInterval(state.treeTimer); });
window.addEventListener("beforeunload", () => state.controller?.abort());

(async () => {
  try {
    const [sources, demo] = await Promise.all([json("/api/sources"), json("/api/demo")]);
    state.catalog = sources.sources;
    $("#source-select").replaceChildren(...state.catalog.map((source) => { const option = el("option", source.label + (source.available ? "" : " · 未安装")); option.value = source.id; option.disabled = !source.available; return option; }));
    $("#source-select").value = state.catalog.find((s) => s.available)?.id || "";
    sourceChanged(); renderChart(demo); await refreshStatus();
  } catch (error) { text("#config-notice", "加载失败：" + error.message); $("#config-notice").classList.remove("hidden"); toast(error.message, true); }
})();
