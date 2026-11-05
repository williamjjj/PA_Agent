// The browser submits and observes jobs; workers alone execute formula searches.
export function initMining({ $, $$, el, json, requireUser, toast, download, getEpoch, getUser, openResearch }) {
  const m = { generation: 0, open: false, page: 0, datasets: [], selected: null, job: null,
    detailQuery: 0, listQuery: 0, submitting: false, actionBusy: false, polling: false,
    pending: null, storageOwner: null, timer: null, renderedResult: null };
  const states = { queued: "排队中", running: "运行中", cancel_requested: "等待取消", succeeded: "已完成",
    failed: "失败", cancelled: "已取消", timed_out: "已超时" };
  const terminal = new Set(["succeeded", "failed", "cancelled", "timed_out"]);
  const num = (value) => typeof value === "number" && Number.isFinite(value)
    ? value.toLocaleString("en-US", { maximumSignificantDigits: 8,
      notation: value !== 0 && Math.abs(value) < 1e-7 ? "scientific" : "standard" }) : "—";
  const percent = (value) => typeof value === "number" && Number.isFinite(value)
    ? (value * 100).toLocaleString("en-US", { maximumFractionDigits: 2 }) + "%" : "—";
  const utc = (value) => typeof value === "number" && Number.isFinite(value)
    ? new Date(value * 1000).toISOString().replace("T", " ").replace(/\.\d+Z$/, " UTC") : "—";
  const setText = (selector, value) => { $(selector).textContent = value; };
  const token = () => ({ generation: m.generation, epoch: getEpoch() });
  const current = (t) => t.generation === m.generation && t.epoch === getEpoch();
  let chart, strategy, benchmark;
  function storage(kind, value) {
    if (!m.storageOwner) return null;
    const key = "pa-mining-" + kind + ":" + m.storageOwner;
    try {
      if (value === undefined) return sessionStorage.getItem(key);
      if (value === null) sessionStorage.removeItem(key); else sessionStorage.setItem(key, value);
    } catch { /* Storage restrictions do not prevent using the in-memory queue UI. */ }
    return null;
  }
  function rememberPending(value) {
    m.pending = value; storage("pending", value ? JSON.stringify(value) : null); updateControls();
  }
  function restorePendingForm() {
    const config = m.pending?.body?.config || m.pending?.context?.config;
    if (!config) return;
    const form = $("#mining-form");
    for (const name of ["trials", "seed", "max_seconds", "min_trades"]) if (config[name] !== undefined) form.elements.namedItem(name).value = config[name];
    for (const [name, value] of Object.entries(config.research || {})) {
      const input = form.elements.namedItem(name);
      if (input) input.value = ["entry_quantile", "exit_quantile", "train_fraction", "validation_fraction"].includes(name) ? value * 100 : value;
    }
  }
  function restoreSession() {
    const owner = getUser()?.id;
    if (!owner || owner === m.storageOwner) return;
    m.storageOwner = owner;
    try {
      const pending = JSON.parse(storage("pending") || "null");
      if (pending && /^\/api\/mining\/jobs(?:\/[a-zA-Z0-9-]+\/retry)?$/.test(pending.endpoint)
          && typeof pending.body?.idempotency_key === "string") m.pending = pending;
    } catch { storage("pending", null); }
    m.selected = storage("selected") || null;
    restorePendingForm();
    updateControls();
  }
  function error(message, notify = true) {
    setText("#mining-error", message); if (notify) toast(message, true);
  }
  async function guard(action) {
    const t = token();
    try { await action(); } catch (failure) { if (current(t)) error(failure.message); }
  }
  function updateControls() {
    const locked = m.submitting || !!m.pending;
    $$("#mining-form input, #mining-form select").forEach((input) => { input.disabled = locked; });
    $("#mining-dataset-select").disabled = locked;
    $("#mining-submit").disabled = m.submitting || (!m.pending && !$("#mining-dataset-select").value);
    setText("#mining-submit", m.submitting ? "正在提交…" : m.pending ? "重试上次未确认的提交" : "提交后台任务");
    $("#mining-pending").classList.toggle("hidden", !m.pending || m.submitting);
    for (const selector of ["#mining-cancel", "#mining-retry", "#mining-delete"]) $(selector).disabled = m.actionBusy || m.submitting || !!m.pending;
    if (m.job?.status === "cancel_requested") $("#mining-cancel").disabled = true;
  }
  function clearDetail() {
    m.detailQuery++; m.job = null; m.renderedResult = null;
    $("#mining-job-detail").classList.add("hidden"); $("#mining-result").classList.add("hidden");
    for (const id of ["mining-job-title", "mining-job-meta", "mining-job-state", "mining-progress-text", "mining-job-error", "mining-formula", "mining-selection-summary", "mining-provenance", "mining-warnings", "mining-segment-metrics", "mining-trades"]) $("#" + id).replaceChildren();
    $("#mining-progress").value = 0; strategy?.setData([]); benchmark?.setData([]);
  }
  function reset() {
    clearTimeout(m.timer); m.timer = null; m.generation++; m.listQuery++;
    storage("pending", null); storage("selected", null);
    m.open = false; m.page = 0; m.datasets = []; m.selected = null; m.pending = null;
    m.storageOwner = null; m.submitting = false; m.actionBusy = false; m.polling = false;
    clearDetail(); $("#mining-form").reset();
    $("#mining-panel").classList.add("hidden");
    $("#mining-panel").querySelectorAll("details").forEach((detail) => { detail.open = false; });
    $(".nav-item[data-action=mining]").classList.remove("selected");
    $("#mining-dataset-select").replaceChildren(el("option", "请选择数据集"));
    $("#mining-dataset-select option").value = "";
    $("#mining-dataset-info").replaceChildren();
    $("#mining-jobs-list").replaceChildren(el("p", "登录后查看自己的后台任务。", "empty-copy"));
    setText("#mining-worker-status", "打开面板后检查工作进程。");
    setText("#mining-submit-status", ""); setText("#mining-error", ""); setText("#mining-page", "第 1 页");
    updateControls();
  }
  function renderDataset() {
    const dataset = m.datasets.find((item) => item.id === $("#mining-dataset-select").value);
    const box = $("#mining-dataset-info"); box.replaceChildren();
    if (dataset) {
      box.append(el("strong", dataset.symbol + " · " + dataset.timeframe + " · " + dataset.bar_count + " 根"),
        el("p", utc(dataset.start) + " → " + utc(dataset.end) + "（结束不含）", "muted"));
      const identity = el("details", null, "research-provenance");
      identity.append(el("summary", "数据身份与质量"), el("p", "数据指纹 " + dataset.dataset_hash, "muted"));
      const quality = dataset.quality || {};
      identity.append(el("p", "缺失周期 " + (quality.missing_intervals ?? "—") + " · 零成交量 " + (quality.zero_volume_count ?? "—"), "muted"));
      box.append(identity);
      if (quality.gap_count || quality.missing_intervals || quality.zero_volume_count) box.append(el("p", "此数据集有缺口或零成交量，不满足连续现货搜索条件。请先修复数据。", "research-warning"));
      if (dataset.source === "demo") box.append(el("p", "此数据为模拟示例，不是真实行情。", "muted"));
    }
    updateControls();
  }
  async function refreshDatasets() {
    const t = token(), selected = $("#mining-dataset-select").value;
    const datasets = await json("/api/research/datasets");
    if (!current(t)) return;
    m.datasets = datasets;
    const empty = el("option", datasets.length ? "请选择数据集" : "暂无数据集，请先前往历史研究创建"); empty.value = "";
    $("#mining-dataset-select").replaceChildren(empty, ...datasets.map((dataset) => {
      const option = el("option", dataset.symbol + " · " + dataset.timeframe + " · " + dataset.bar_count + " 根"); option.value = dataset.id; return option;
    }));
    $("#mining-dataset-select").value = m.pending?.body?.dataset_id || m.pending?.context?.dataset_id || selected || datasets[0]?.id || "";
    renderDataset();
  }
  async function refreshStatus() {
    const t = token(), status = await json("/api/mining/status");
    if (!current(t)) return;
    const node = $("#mining-worker-status"); node.classList.toggle("offline", !status.worker_available);
    node.textContent = status.worker_available
      ? "工作进程在线（" + (status.workers_online ?? 1) + "）· 每账户最多 " + (status.limits?.active_jobs ?? 3) + " 个活动任务。"
      : "当前没有在线工作进程。提交后任务会保持排队；工作进程上线后才会运行，网页不会代为执行搜索。";
  }
  function progress(job) {
    if (typeof job.progress === "number") return Math.max(0, Math.min(1, job.progress));
    const completed = job.progress?.completed ?? job.progress?.current, total = job.progress?.total;
    return total > 0 ? Math.max(0, Math.min(1, completed / total)) : 0;
  }
  async function refreshJobs() {
    const t = token(), query = ++m.listQuery, page = m.page;
    const jobs = await json("/api/mining/jobs?offset=" + page * 25);
    if (!current(t) || query !== m.listQuery || page !== m.page) return;
    const rows = jobs.map((job) => {
      const row = el("div", null, "history-row"), info = el("div"), actions = el("div");
      row.classList.toggle("mining-selected-job", job.id === m.selected);
      info.append(el("strong", (job.dataset?.symbol || job.symbol || "研究任务") + " · " + (job.dataset?.timeframe || job.timeframe || "") + " · " + (states[job.status] || job.status)),
        el("p", utc(job.created) + " · 进度 " + percent(progress(job)) + " · 尝试 " + (job.attempts || 0) + " 次"));
      const view = el("button", "查看", "secondary"); view.dataset.jobId = job.id;
      view.onclick = () => guard(async () => { await selectJob(job.id); $("#mining-job-detail").scrollIntoView({ block: "start", behavior: "smooth" }); });
      actions.append(view); row.append(info, actions); return row;
    });
    $("#mining-jobs-list").replaceChildren(...(rows.length ? rows : [el("p", "这一页还没有后台任务。", "empty-copy")]));
    $("#mining-prev").disabled = page === 0; $("#mining-next").disabled = jobs.length < 25;
    setText("#mining-page", "第 " + (page + 1) + " 页");
    if (!m.selected && jobs.length) await selectJob(jobs[0].id);
  }
  async function selectJob(id) {
    m.selected = id; storage("selected", id); clearDetail();
    await refreshDetail();
  }
  async function refreshDetail() {
    if (!m.selected) return;
    const t = token(), id = m.selected, query = ++m.detailQuery;
    let job;
    try { job = await json("/api/mining/jobs/" + id); }
    catch (failure) {
      if (!current(t) || query !== m.detailQuery || id !== m.selected) return;
      if (failure.status === 404) { m.selected = null; storage("selected", null); clearDetail(); return; }
      throw failure;
    }
    if (current(t) && query === m.detailQuery && id === m.selected) renderJob(job);
  }
  function renderJob(job) {
    m.job = job; $("#mining-job-detail").classList.remove("hidden");
    const dataset = job.dataset || {};
    setText("#mining-job-title", (dataset.symbol || job.symbol || "研究任务") + " · " + (dataset.timeframe || job.timeframe || "") + " · 后台搜索");
    setText("#mining-job-meta", "任务 " + job.id + " · 创建 " + utc(job.created) + " · 更新 " + utc(job.updated) + " · 尝试 " + (job.attempts || 0) + " 次");
    setText("#mining-job-state", states[job.status] || job.status);
    $("#mining-progress").value = progress(job);
    setText("#mining-progress-text", job.status === "queued" ? "任务已持久化，等待工作进程领取。可关闭页面后重新查看。"
      : job.status === "cancel_requested" ? "已请求取消，等待工作进程确认；未将请求取消显示为已取消。"
      : "搜索进度 " + percent(progress(job)) + (job.status === "running" ? " · 可以离开页面，任务继续在后台运行。" : ""));
    setText("#mining-job-error", typeof job.error === "string" ? job.error : job.error?.message || "");
    $("#mining-cancel").classList.toggle("hidden", terminal.has(job.status));
    $("#mining-cancel").disabled = job.status === "cancel_requested" || m.actionBusy;
    $("#mining-retry").classList.toggle("hidden", !["failed", "cancelled", "timed_out"].includes(job.status));
    $("#mining-delete").classList.toggle("hidden", !terminal.has(job.status));
    $("#mining-export").classList.toggle("hidden", !job.result);
    if (job.result && m.renderedResult !== job.id + ":" + job.updated) {
      renderResult(job.result, job); m.renderedResult = job.id + ":" + job.updated;
    } else if (!job.result) $("#mining-result").classList.add("hidden");
    updateControls();
    if (job.status === "cancel_requested") $("#mining-cancel").disabled = true;
  }
  function renderResult(result, job) {
    $("#mining-result").classList.remove("hidden");
    const formula = result.formula || {}, research = result.research_result || {}, selection = result.selection || {};
    setText("#mining-formula", typeof formula === "string" ? formula : formula.expression || JSON.stringify(formula.tokens || []));
    const attempted = selection.attempted ?? selection.trials_completed ?? selection.evaluated;
    const valid = selection.valid ?? selection.valid_candidates ?? (typeof attempted === "number" && typeof selection.rejected === "number" ? attempted - selection.rejected : undefined);
    setText("#mining-selection-summary", "训练段选择结果 · 尝试 " + (attempted ?? "—") + " 个候选 · 有效 " + (valid ?? "—") + " 个 · 训练评分 " + num(selection.best_score ?? selection.score ?? selection.fitness) + "。训练成绩包含公式选择偏差；验证与测试段未参与候选评分。");
    const provenance = $("#mining-provenance"); provenance.replaceChildren();
    for (const line of ["词元：" + JSON.stringify(formula.tokens || []), "公式指纹 " + (formula.formula_hash || formula.hash || formula.id || "—"),
      "词表 " + (formula.vocab_version || "—") + " · 因子 " + (formula.feature_version || research.feature_version || "—") + " · 引擎 " + (formula.engine_version || research.engine_version || "—"),
      "训练数据指纹 " + (formula.training_dataset_hash || "—"), "完整数据指纹 " + (job.dataset?.dataset_hash || research.dataset_hash || "—"),
      "随机种子 " + (formula.seed ?? job.config?.seed ?? "—"), "代码版本 " + (job.provenance?.code_revision || "—"), "选择详情 " + JSON.stringify(selection)]) provenance.append(el("p", line, "muted"));
    $("#mining-warnings").replaceChildren(...[...(result.warnings || []), ...(research.warnings || [])].map((warning) => el("p", warning, "research-warning")));
    $("#mining-segment-metrics").replaceChildren(...[["train", "训练 · 样本内选择"], ["validation", "验证段"], ["test", "样本外测试"]].map(([name, label]) => {
      const metrics = research.segments?.[name]?.metrics || {}, row = el("tr");
      for (const value of [label, percent(metrics.total_return), percent(metrics.benchmark_return), percent(metrics.max_drawdown), num(metrics.closed_trades), num(metrics.fees), num(metrics.slippage_cost)]) row.append(el("td", value));
      return row;
    }));
    const test = research.segments?.test, curve = test?.equity_curve || [];
    if (!chart) {
      chart = LightweightCharts.createChart($("#mining-equity-chart"), {
        layout: { background: { color: "#111923" }, textColor: "#8295a9", fontSize: 10 },
        grid: { vertLines: { color: "#1b2735" }, horzLines: { color: "#1b2735" } },
        rightPriceScale: { borderColor: "#263445" }, timeScale: { borderColor: "#263445", timeVisible: true, lockVisibleTimeRangeOnResize: true }, autoSize: true,
      });
      strategy = chart.addSeries(LightweightCharts.LineSeries, { color: "#63e4ba", lineWidth: 2, priceLineVisible: false });
      benchmark = chart.addSeries(LightweightCharts.LineSeries, { color: "#70b4ef", lineWidth: 1, priceLineVisible: false });
    }
    strategy.setData(curve.map((point) => ({ time: point.time, value: point.equity })));
    benchmark.setData(curve.map((point) => ({ time: point.time, value: point.benchmark })));
    chart.timeScale().fitContent();
    const trades = test?.trades || [];
    setText("#mining-ledger-heading", "样本外测试成交记录（" + trades.length + " 笔成交）");
    $("#mining-trades").replaceChildren(...trades.map((trade) => {
      const row = el("tr");
      for (const value of [utc(trade.time), utc(trade.signal_at ?? trade.signal_time), trade.side === "buy" ? "买入" : trade.side === "sell" ? "卖出" : trade.side,
        num(trade.reference_price), num(trade.price), num(trade.quantity), num(trade.fee), num(trade.slippage_cost), num(trade.cash_after), num(trade.position_after), num(trade.pnl), trade.reason === "segment_end" ? "分段结束平仓" : trade.reason === "signal" ? "满足公式规则" : trade.reason]) row.append(el("td", value));
      return row;
    }));
  }
  async function submitPending() {
    if (!m.pending || !requireUser() || m.submitting) return;
    const t = token(), pending = m.pending;
    m.submitting = true; updateControls(); setText("#mining-error", "");
    try {
      const job = await json(pending.endpoint, "POST", pending.body);
      if (!current(t)) return;
      rememberPending(null); m.page = 0; m.selected = job.id; storage("selected", job.id);
      clearDetail(); renderJob(job); setText("#mining-submit-status", "任务已保存。关闭页面不会删除任务，后续可从任务列表继续查看。");
      await refreshJobs(); await refreshDetail();
    } catch (failure) {
      if (!current(t)) return;
      if (failure.status >= 400 && failure.status < 500 && !pending.uncertain) rememberPending(null);
      else if (!pending.uncertain) rememberPending({ ...pending, uncertain: true });
      error(m.pending ? "尚未确认是否创建任务：" + failure.message + " 请使用“重试上次未确认的提交”，避免重复任务。" : failure.message);
    } finally { if (current(t)) { m.submitting = false; updateControls(); } }
  }
  async function refreshAll() {
    const t = token();
    const results = await Promise.allSettled([refreshStatus(), refreshJobs(), refreshDetail()]);
    if (!current(t)) return;
    const failed = results.find((item) => item.status === "rejected");
    if (failed) error(failed.reason.message, false);
  }
  function schedule() {
    clearTimeout(m.timer); m.timer = null;
    if (!m.open || !getUser() || document.hidden) return;
    m.timer = setTimeout(async () => {
      if (!m.open || !getUser() || document.hidden || m.polling) { schedule(); return; }
      const t = token(); m.polling = true;
      try { await refreshAll(); }
      finally { if (current(t)) { m.polling = false; schedule(); } }
    }, 3000);
  }
  async function open() {
    m.open = true; $("#mining-panel").classList.remove("hidden");
    $(".nav-item[data-action=mining]").classList.add("selected"); $(".nav-item[data-action=workspace]").classList.remove("selected");
    history.replaceState(null, "", "#mining");
    $("#mining-panel").scrollIntoView({ block: "start", behavior: "smooth" });
    if (!requireUser()) return;
    restoreSession();
    try { await Promise.all([refreshDatasets(), refreshAll()]); } finally { schedule(); }
  }
  function close() {
    m.open = false; m.generation++; m.polling = false; m.submitting = false; m.actionBusy = false;
    clearTimeout(m.timer); m.timer = null; updateControls();
    $("#mining-panel").classList.add("hidden"); $(".nav-item[data-action=mining]").classList.remove("selected");
    if (location.hash === "#mining") history.replaceState(null, "", location.pathname + location.search);
  }
  $("#mining-refresh").onclick = () => guard(open);
  $("#mining-open-research").onclick = () => guard(openResearch);
  $("#mining-dataset-select").onchange = renderDataset;
  $("#mining-form select[name=direction]").onchange = (event) => {
    $("#mining-form input[name=entry_quantile]").value = event.target.value === "above" ? "80" : "20";
  };
  $("#mining-form").onsubmit = (event) => {
    event.preventDefault(); guard(async () => {
      if (!requireUser() || m.submitting) return;
      restoreSession();
      if (!m.pending) {
        const datasetId = $("#mining-dataset-select").value;
        if (!datasetId) throw new Error("请先选择研究数据集。");
        const form = Object.fromEntries(new FormData(event.target));
        const research = { factor: "DEV", direction: form.direction };
        for (const name of ["initial_capital", "fee_bps", "slippage_bps"]) research[name] = Number(form[name]);
        for (const name of ["entry_quantile", "exit_quantile", "train_fraction", "validation_fraction"]) research[name] = Number(form[name]) / 100;
        if (research.train_fraction + research.validation_fraction > 0.9 + 1e-12) throw new Error("请为测试段保留至少 10% 的数据。");
        if (research.direction === "below" ? research.entry_quantile >= research.exit_quantile : research.entry_quantile <= research.exit_quantile) throw new Error("低位入场的分位须低于退出分位；高位入场则相反。");
        const config = { research };
        for (const name of ["trials", "seed", "max_seconds", "min_trades"]) config[name] = Number(form[name]);
        rememberPending({ endpoint: "/api/mining/jobs", body: { dataset_id: datasetId, config, idempotency_key: crypto.randomUUID() } });
      }
      await submitPending();
    });
  };
  $("#mining-cancel").onclick = () => guard(async () => {
    if (!m.selected || m.actionBusy || !requireUser()) return;
    const t = token(), id = m.selected; m.actionBusy = true; updateControls();
    try {
      await json("/api/mining/jobs/" + id + "/cancel", "POST", {});
      if (current(t) && m.selected === id) { await refreshDetail(); await refreshJobs(); }
    } finally { if (current(t)) { m.actionBusy = false; updateControls(); } }
  });
  $("#mining-retry").onclick = () => guard(async () => {
    if (!m.selected || m.pending || !requireUser()) return;
    restoreSession();
    rememberPending({ endpoint: "/api/mining/jobs/" + m.selected + "/retry", body: { idempotency_key: crypto.randomUUID() }, context: { dataset_id: m.job?.dataset_id, config: m.job?.config } });
    await submitPending();
  });
  $("#mining-delete").onclick = () => guard(async () => {
    if (!m.selected || !requireUser() || !confirm("删除这份已结束的后台任务及其结果？原始研究数据集会保留。")) return;
    const t = token(), id = m.selected;
    await json("/api/mining/jobs/" + id, "DELETE");
    if (!current(t)) return;
    if (m.selected === id) { m.selected = null; storage("selected", null); clearDetail(); }
    await refreshJobs();
  });
  $("#mining-export").onclick = () => guard(async () => {
    if (!m.selected || !requireUser()) return;
    const t = token(), id = m.selected, result = await json("/api/mining/jobs/" + id + "/export");
    if (current(t) && m.selected === id) download("pa-mining-" + id + ".json", JSON.stringify(result, null, 2), "application/json");
  });
  for (const [selector, delta] of [["#mining-prev", -1], ["#mining-next", 1]]) $(selector).onclick = () => guard(async () => {
    if (!requireUser()) return; m.page = Math.max(0, m.page + delta); await refreshJobs();
  });
  document.addEventListener("visibilitychange", schedule);
  window.addEventListener("beforeunload", () => clearTimeout(m.timer));
  reset();
  return { open, close, reset };
}
