// Historical research stays attached to an immutable dataset, not the live chart.
export function initResearch({ $, $$, el, json, requireUser, toast, download, getEpoch, getChart, freezeChart }) {
  const r = { generation: 0, dataset: null, datasets: [], datasetPage: 0, runPage: 0,
    run: null, selectedRun: null, segment: "test", dataBusy: false, runBusy: false,
    datasetQuery: 0, runQuery: 0, resultQuery: 0 };
  let equityChart, strategySeries, benchmarkSeries, drawdownChart, drawdownSeries;
  const segmentNames = { train: "训练 · 样本内校准", validation: "验证段", test: "样本外测试" };
  const sourceNames = { demo: "模拟研究示例", csv: "用户上传 CSV", birdeye: "Birdeye · Solana" };
  const num = (value) => typeof value === "number" && Number.isFinite(value)
    ? value.toLocaleString("en-US", { maximumSignificantDigits: 8,
      notation: value !== 0 && Math.abs(value) < 1e-7 ? "scientific" : "standard" }) : "—";
  const percent = (value) => typeof value === "number" && Number.isFinite(value)
    ? (value * 100).toLocaleString("en-US", { maximumFractionDigits: 2 }) + "%" : "—";
  const utc = (value) => typeof value === "number" && Number.isFinite(value)
    ? new Date(value * 1000).toISOString().replace("T", " ").replace(/\.\d+Z$/, " UTC") : "—";
  const setText = (selector, value) => { $(selector).textContent = value; };
  const token = () => ({ generation: r.generation, epoch: getEpoch() });
  const current = (t) => t.generation === r.generation && t.epoch === getEpoch();
  function error(message) { setText("#research-error", message); toast(message, true); }
  async function guard(action) {
    const t = token();
    try { await action(); } catch (failure) { if (current(t)) { setText("#research-data-status", "本次操作未完成。"); error(failure.message); } }
  }
  function updateBusy() {
    $("#research-run-button").disabled = r.runBusy || r.dataBusy || !r.dataset;
    $("#research-run-button").textContent = r.runBusy ? "研究运行中…" : "运行并保存研究";
    for (const selector of ["#research-use-chart", "#research-demo", "#research-dataset-select", "#research-csv-form button", "#research-history-form button"]) $(selector).disabled = r.dataBusy;
    $("#research-delete-dataset").disabled = r.dataBusy || !r.dataset;
  }
  function clearResult() {
    r.resultQuery++; r.run = null; r.selectedRun = null;
    $("#research-result").classList.add("hidden");
    for (const id of ["research-result-title", "research-result-summary", "research-warnings", "research-provenance", "research-metrics", "research-trades", "research-cost-rows", "research-segment-range"]) $("#" + id).replaceChildren();
    strategySeries?.setData([]); benchmarkSeries?.setData([]); drawdownSeries?.setData([]);
  }
  function reset() {
    r.generation++; r.datasetQuery++; r.runQuery++;
    r.dataset = null; r.datasets = []; r.datasetPage = 0; r.runPage = 0;
    r.dataBusy = false; r.runBusy = false; r.segment = "test";
    clearResult();
    $("#research-csv-form").reset(); $("#research-history-form").reset(); $("#research-run-form").reset();
    $("#research-panel").classList.add("hidden");
    $(".nav-item[data-action=research]").classList.remove("selected");
    $(".nav-item[data-action=workspace]").classList.add("selected");
    $("#research-panel").querySelectorAll("details").forEach((detail) => { detail.open = false; });
    setText("#research-data-status", ""); setText("#research-error", "");
    setText("#research-datasets-page", "第 1 页"); setText("#research-runs-page", "第 1 页");
    $("#research-runs-list").replaceChildren(el("p", "登录后查看自己的研究记录。", "empty-copy"));
    renderDatasetOptions(); renderDataset(); updateSplitHint(); updateBusy();
  }
  function renderDatasetOptions() {
    const datasets = [...r.datasets];
    if (r.dataset && !datasets.some((item) => item.id === r.dataset.id)) datasets.unshift(r.dataset);
    const placeholder = el("option", "请先载入或选择数据集"); placeholder.value = "";
    $("#research-dataset-select").replaceChildren(placeholder, ...datasets.map((dataset) => {
      const option = el("option", dataset.symbol + " · " + dataset.timeframe + " · " + dataset.bar_count + " 根 · " + (sourceNames[dataset.source] || dataset.source));
      option.value = dataset.id; return option;
    }));
    $("#research-dataset-select").value = r.dataset?.id || "";
  }
  function renderDataset() {
    const box = $("#research-dataset-info"), dataset = r.dataset;
    box.replaceChildren();
    if (!dataset) { box.append(el("p", "选定数据集后，主图变化不会改变本次研究的数据。", "muted")); updateBusy(); return; }
    box.append(el("strong", dataset.symbol + " · " + dataset.timeframe + " · " + dataset.bar_count + " 根"),
      el("p", (sourceNames[dataset.source] || dataset.source) + " · " + utc(dataset.start) + " → " + utc(dataset.end) + "（结束时间不含）", "muted"),
      el("p", "数据集已固定；主图变化不会改变此选择。", "muted"));
    const quality = dataset.quality || {}, details = el("details", null, "research-provenance");
    if (quality.gap_count || quality.missing_intervals || quality.zero_volume_count) box.append(el("p", "当前数据含 " + (quality.gap_count || 0) + " 处内部缺口、" + (quality.missing_intervals || 0) + " 个缺失周期、" + (quality.zero_volume_count || 0) + " 根零成交量 K 线，不满足连续现货回测条件。", "research-warning"));
    details.append(el("summary", "数据质量与身份"), el("p", "缺口 " + (quality.gap_count ?? "—") + " · 缺失间隔 " + (quality.missing_intervals ?? "—") + " · 零成交量 " + (quality.zero_volume_count ?? "—") + " · 重复 " + (quality.duplicate_count ?? "—"), "muted"),
      el("p", "ID " + dataset.id + " · 数据指纹 " + dataset.dataset_hash, "muted"));
    if (dataset.instrument?.address) details.append(el("p", "Solana " + dataset.instrument.address, "muted"));
    for (const flag of quality.flags || []) details.append(el("p", typeof flag === "string" ? flag : flag.message || flag.code, "muted"));
    box.append(details); updateBusy();
  }
  function selectDataset(dataset) {
    r.dataset = dataset || null; clearResult(); renderDatasetOptions(); renderDataset();
  }
  async function refreshDatasets() {
    const t = token(), query = ++r.datasetQuery, page = r.datasetPage;
    const datasets = await json("/api/research/datasets?offset=" + page * 25);
    if (!current(t) || query !== r.datasetQuery || page !== r.datasetPage) return;
    r.datasets = datasets;
    renderDatasetOptions();
    $("#research-datasets-prev").disabled = page === 0;
    $("#research-datasets-next").disabled = datasets.length < 25;
    setText("#research-datasets-page", "第 " + (page + 1) + " 页");
  }
  async function createDataset(body) {
    if (!requireUser() || r.dataBusy) return;
    const t = token(); r.dataBusy = true; updateBusy();
    setText("#research-error", ""); setText("#research-data-status", "正在准备并校验研究数据…");
    try {
      const dataset = await json("/api/research/datasets", "POST", body);
      if (!current(t)) return;
      selectDataset(dataset); r.datasetPage = 0;
      setText("#research-data-status", "已保存 " + dataset.bar_count + " 根 K 线，可设定规则后运行。");
      $("#research-csv-form input[type=file]").value = "";
      await refreshDatasets();
    } finally { if (current(t)) { r.dataBusy = false; updateBusy(); } }
  }
  async function refreshRuns() {
    const t = token(), query = ++r.runQuery, page = r.runPage;
    const runs = await json("/api/research/runs?offset=" + page * 25);
    if (!current(t) || query !== r.runQuery || page !== r.runPage) return;
    const rows = runs.map((run) => {
      const row = el("div", null, "history-row"), info = el("div"), actions = el("div");
      info.append(el("strong", run.symbol + " · " + run.timeframe + " · " + run.factor),
        el("p", utc(run.created) + " · 测试收益 " + percent(run.test_return) + " · 基准 " + percent(run.benchmark_return)));
      const view = el("button", "查看", "secondary"), remove = el("button", "删除", "quiet");
      view.onclick = () => guard(() => loadRun(run.id));
      remove.onclick = () => guard(async () => {
        if (!confirm("删除这份已保存研究？原始数据集会保留。")) return;
        const request = token();
        await json("/api/research/runs/" + run.id, "DELETE");
        if (!current(request)) return;
        if (r.run?.id === run.id || r.selectedRun === run.id) clearResult();
        await refreshRuns();
      });
      actions.append(view, remove); row.append(info, actions); return row;
    });
    $("#research-runs-list").replaceChildren(...(rows.length ? rows : [el("p", "这一页还没有保存的研究。", "empty-copy")]));
    $("#research-runs-prev").disabled = page === 0; $("#research-runs-next").disabled = runs.length < 25;
    setText("#research-runs-page", "第 " + (page + 1) + " 页");
  }
  async function loadRun(id) {
    if (!requireUser()) return;
    const t = token(); clearResult(); r.selectedRun = id;
    const query = ++r.resultQuery;
    setText("#research-data-status", "正在读取已保存结果…");
    let run;
    try { run = await json("/api/research/runs/" + id); }
    catch (failure) { if (current(t) && query === r.resultQuery && r.selectedRun === id) throw failure; return; }
    if (!current(t) || query !== r.resultQuery || r.selectedRun !== id) return;
    r.dataset = run.dataset; renderDatasetOptions(); renderDataset(); renderRun(run);
    setText("#research-data-status", "已读取原结果，未重新运行研究。");
    $("#research-result").scrollIntoView({ block: "start", behavior: "smooth" });
  }
  function metric(label, value) {
    const box = el("div", null, "result-field"); box.append(el("span", label), el("strong", value)); return box;
  }
  function setupCharts() {
    if (equityChart) return;
    const options = { layout: { background: { color: "#111923" }, textColor: "#8295a9", fontSize: 10 },
      grid: { vertLines: { color: "#1b2735" }, horzLines: { color: "#1b2735" } },
      rightPriceScale: { borderColor: "#263445" }, timeScale: { borderColor: "#263445", timeVisible: true, lockVisibleTimeRangeOnResize: true }, autoSize: true };
    equityChart = LightweightCharts.createChart($("#research-equity-chart"), options);
    strategySeries = equityChart.addSeries(LightweightCharts.LineSeries, { color: "#63e4ba", lineWidth: 2, priceLineVisible: false });
    benchmarkSeries = equityChart.addSeries(LightweightCharts.LineSeries, { color: "#70b4ef", lineWidth: 1, priceLineVisible: false });
    drawdownChart = LightweightCharts.createChart($("#research-drawdown-chart"), options);
    drawdownSeries = drawdownChart.addSeries(LightweightCharts.AreaSeries, { lineColor: "#f58e8e", topColor: "#f58e8e11", bottomColor: "#f58e8e44", lineWidth: 1, priceLineVisible: false,
      priceFormat: { type: "custom", minMove: 0.0001, formatter: percent } });
  }
  function renderSegment() {
    const result = r.run?.result, segment = result?.segments?.[r.segment];
    if (!segment) return;
    $$("#research-segments button").forEach((button) => button.setAttribute("aria-selected", String(button.dataset.segment === r.segment)));
    const split = result.split?.[r.segment], metrics = segment.metrics || {};
    setText("#research-segment-range", segmentNames[r.segment] + " · " + utc(split?.start_time) + " → " + utc(split?.end_time) + " · 独立现金起点");
    $("#research-metrics").replaceChildren(...[["净收益率", percent(metrics.total_return)], ["买入持有基准", percent(metrics.benchmark_return)],
      ["最大回撤", percent(metrics.max_drawdown)], ["净盈亏", num(metrics.net_pnl)], ["手续费", num(metrics.fees)],
      ["滑点成本", num(metrics.slippage_cost)], ["已平仓交易", num(metrics.closed_trades)], ["历史获利交易占比", percent(metrics.win_rate)],
      ["持仓时段占比", percent(metrics.exposure)]].map(([label, value]) => metric(label, value)));
    setupCharts();
    const curve = segment.equity_curve || [];
    strategySeries.setData(curve.map((point) => ({ time: point.time, value: point.equity })));
    benchmarkSeries.setData(curve.map((point) => ({ time: point.time, value: point.benchmark })));
    drawdownSeries.setData(curve.map((point) => ({ time: point.time, value: point.drawdown })));
    equityChart.timeScale().fitContent(); drawdownChart.timeScale().fitContent();
    const trades = segment.trades || [];
    setText("#research-ledger-heading", "逐笔成交记录（" + trades.length + " 笔成交）");
    $("#research-ledger-empty").classList.toggle("hidden", trades.length > 0);
    const reasons = { signal: "满足因子规则", entry: "满足入场规则", exit: "满足退出规则", end_of_segment: "分段结束平仓", segment_end: "分段结束平仓", final_liquidation: "分段结束平仓" };
    $("#research-trades").replaceChildren(...trades.map((trade) => {
      const row = el("tr");
      for (const value of [utc(trade.time), utc(trade.signal_at ?? trade.signal_time), trade.side === "buy" ? "买入" : trade.side === "sell" ? "卖出" : trade.side,
        num(trade.reference_price), num(trade.price), num(trade.quantity), num(trade.fee), num(trade.slippage_cost), num(trade.cash_after), num(trade.position_after), num(trade.pnl), reasons[trade.reason] || trade.reason]) row.append(el("td", value));
      return row;
    }));
  }
  function renderRun(run) {
    r.run = run; r.selectedRun = run.id; r.segment = "test";
    $("#research-result").classList.remove("hidden");
    const result = run.result, dataset = run.dataset, cfg = result.config || {};
    setText("#research-result-title", dataset.symbol + " · " + dataset.timeframe + " · " + cfg.factor + " 研究");
    setText("#research-result-summary", "保存于 " + utc(run.created) + " · 阈值来自训练段；各段独立计算净收益。初始资金 " + num(cfg.initial_capital));
    const warnings = result.warnings || [], notes = el("details", null, "research-provenance");
    notes.append(el("summary", "模型假设与结果说明（" + warnings.length + " 项）"), ...warnings.map((warning) => el("p", warning, "research-warning")));
    $("#research-warnings").replaceChildren(notes);
    const provenance = $("#research-provenance"); provenance.replaceChildren();
    for (const line of ["数据来源：" + (sourceNames[dataset.source] || dataset.source) + " · 数据集 " + dataset.id,
      "数据指纹：" + dataset.dataset_hash, "引擎 " + result.engine_version + " · 因子 " + result.feature_version,
      "训练有效样本 " + num(result.thresholds?.training_samples) + " · 入场阈值 " + num(result.thresholds?.entry) + " · 退出阈值 " + num(result.thresholds?.exit),
      "方向 " + (cfg.direction === "above" ? "高于入场阈值" : "低于入场阈值") + " · 入场分位 " + percent(cfg.entry_quantile) + " · 退出分位 " + percent(cfg.exit_quantile),
      "手续费 " + num(cfg.fee_bps) + " bps · 滑点 " + num(cfg.slippage_bps) + " bps · 训练 / 验证 " + percent(cfg.train_fraction) + " / " + percent(cfg.validation_fraction)]) provenance.append(el("p", line, "muted"));
    $("#research-cost-rows").replaceChildren(...(result.cost_sensitivity || []).map((item) => {
      const row = el("tr"); row.append(el("td", num(item.multiplier) + " ×"), el("td", percent(item.validation_return)), el("td", percent(item.test_return))); return row;
    }));
    renderSegment();
  }
  function updateSplitHint() {
    const form = $("#research-run-form"), train = Number(form.elements.train_fraction.value), validation = Number(form.elements.validation_fraction.value);
    setText("#research-split-hint", "训练 " + train + "% · 验证 " + validation + "% · 样本外测试 " + (100 - train - validation) + "%；1 bps = 0.01%。");
  }
  async function open() {
    $("#research-panel").classList.remove("hidden");
    $(".nav-item[data-action=research]").classList.add("selected");
    $(".nav-item[data-action=workspace]").classList.remove("selected");
    $("#research-panel").scrollIntoView({ block: "start", behavior: "smooth" });
    if (!requireUser()) return;
    await Promise.all([refreshDatasets(), refreshRuns()]);
  }
  function close() {
    r.generation++; r.resultQuery++; r.dataBusy = false; r.runBusy = false; updateBusy();
    $("#research-panel").classList.add("hidden");
    $(".nav-item[data-action=research]").classList.remove("selected");
    $(".nav-item[data-action=workspace]").classList.add("selected");
  }
  $("#research-refresh").onclick = () => guard(open);
  $("#research-demo").onclick = () => guard(() => createDataset({ demo: true }));
  $("#research-download-demo").onclick = () => guard(async () => {
    const t = token(), demo = await json("/api/research/demo");
    if (current(t)) download("pa-research-example-" + demo.timeframe + ".csv", demo.csv, "text/csv");
  });
  $("#research-use-chart").onclick = () => guard(async () => {
    if (!requireUser() || r.dataBusy) return;
    const t = token(), data = getChart();
    if (!data) throw new Error("请先载入主图行情，或使用研究示例。");
    if (data.bars.length < 300) throw new Error("当前图表只有 " + data.bars.length + " 根 K 线；研究至少需要 300 根。请载入更多行情、上传研究 CSV 或使用 600 根研究示例。");
    await freezeChart(data, getEpoch());
    if (current(t)) await createDataset({ snapshot_id: data.snapshot_id, market_kind: "continuous_spot" });
  });
  $("#research-csv-form").onsubmit = (event) => {
    event.preventDefault(); guard(async () => {
      if (!requireUser() || r.dataBusy) return;
      const t = token(), form = new FormData(event.target), file = form.get("file");
      if (!file?.size || file.size > 2_000_000) throw new Error("请选择不超过 2 MB 的 CSV 文件。");
      const csv = await file.text();
      if (current(t)) await createDataset({ data: { csv, symbol: form.get("symbol").trim(), timeframe: form.get("timeframe"), market_kind: "continuous_spot" } });
    });
  };
  $("#research-history-form").onsubmit = (event) => {
    event.preventDefault(); guard(async () => {
      const form = new FormData(event.target), start = Date.parse(form.get("start") + "Z") / 1000, end = Date.parse(form.get("end") + "Z") / 1000;
      if (!Number.isFinite(start) || !Number.isFinite(end) || start >= end) throw new Error("请填写有效的 UTC 时间范围，结束时间须晚于开始时间。");
      await createDataset({ history: { instrument_id: form.get("instrument_id").trim(), symbol: form.get("symbol").trim(), chain: "solana", timeframe: form.get("timeframe"), start, end } });
    });
  };
  $("#research-dataset-select").onchange = () => {
    const id = $("#research-dataset-select").value;
    selectDataset(r.datasets.find((item) => item.id === id) || (r.dataset?.id === id ? r.dataset : null));
  };
  $("#research-delete-dataset").onclick = () => guard(async () => {
    if (!r.dataset || !confirm("删除这个研究数据集？已被研究记录引用的数据集需要先删除关联记录。")) return;
    const t = token(), id = r.dataset.id;
    await json("/api/research/datasets/" + id, "DELETE");
    if (!current(t)) return;
    if (r.dataset?.id === id) selectDataset(null);
    await refreshDatasets();
  });
  for (const [kind, pageKey, refresh] of [["datasets", "datasetPage", refreshDatasets], ["runs", "runPage", refreshRuns]]) {
    $("#research-" + kind + "-prev").onclick = () => guard(async () => { if (!requireUser()) return; r[pageKey] = Math.max(0, r[pageKey] - 1); await refresh(); });
    $("#research-" + kind + "-next").onclick = () => guard(async () => { if (!requireUser()) return; r[pageKey]++; await refresh(); });
  }
  $("#research-run-form").oninput = updateSplitHint;
  $("#research-run-form select[name=direction]").onchange = (event) => {
    $("#research-run-form input[name=entry_quantile]").value = event.target.value === "above" ? "80" : "20";
  };
  $("#research-run-form").onsubmit = (event) => {
    event.preventDefault(); guard(async () => {
      if (!requireUser() || !r.dataset || r.runBusy || r.dataBusy) return;
      const config = Object.fromEntries(new FormData(event.target));
      for (const key of Object.keys(config)) if (!["factor", "direction"].includes(key)) config[key] = Number(config[key]);
      for (const key of ["entry_quantile", "exit_quantile", "train_fraction", "validation_fraction"]) config[key] /= 100;
      if (config.train_fraction + config.validation_fraction > 0.9 + 1e-12) throw new Error("请为样本外测试保留至少 10% 的数据，并满足服务端最小样本要求。");
      if (config.direction === "below" ? config.entry_quantile >= config.exit_quantile : config.entry_quantile <= config.exit_quantile) throw new Error("低位入场的入场分位须低于退出分位；高位入场则须高于退出分位。");
      const t = token(), datasetId = r.dataset.id; clearResult(); const query = ++r.resultQuery;
      r.runBusy = true; updateBusy(); setText("#research-error", ""); setText("#research-data-status", "正在按时间切分并运行研究…");
      try {
        const run = await json("/api/research/runs", "POST", { dataset_id: datasetId, config });
        if (!current(t)) return;
        if (r.dataset?.id === datasetId && query === r.resultQuery) {
          renderRun(run); setText("#research-data-status", "研究已保存；结果使用固定数据集和本次参数。");
          $("#research-result").scrollIntoView({ block: "start", behavior: "smooth" });
        }
        r.runPage = 0; await refreshRuns();
      } catch (failure) { if (current(t) && r.dataset?.id === datasetId && query === r.resultQuery) throw failure; }
      finally { if (current(t)) { r.runBusy = false; updateBusy(); } }
    });
  };
  $$("#research-segments button").forEach((button) => { button.onclick = () => { r.segment = button.dataset.segment; renderSegment(); }; });
  $("#research-export").onclick = () => guard(async () => {
    if (!r.run || !requireUser()) return;
    const t = token(), id = r.run.id, result = await json("/api/research/runs/" + id + "/export");
    if (current(t) && r.run?.id === id) download("pa-research-" + id + ".json", JSON.stringify(result, null, 2), "application/json");
  });
  reset();
  return { open, close, reset };
}
