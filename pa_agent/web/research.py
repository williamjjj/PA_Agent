"""Deterministic, long/cash research on a frozen continuous-market OHLCV dataset.

No network, database, model fitting on holdouts, or order execution is performed.
The numerical contract and limits are documented in docs/ALPHAGPT_BACKTEST.md.
"""
from __future__ import annotations

import json
import math
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from pa_agent.web.quant import FEATURE_VERSION, _validated_bars, compute_factors
from pa_agent.web.research_data import DATASET_VERSION, research_dataset_hash
from pa_agent.web.snapshots import dataset_hash
from pa_agent.web.sources import restore_frame

ENGINE_VERSION = "pa-research-v1"
_SECONDS = {"1m": 60, "5m": 300, "15m": 900, "30m": 1800,
            "1h": 3600, "4h": 14_400, "1d": 86_400}


class ResearchConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    factor: Literal["RET", "PRESSURE", "FOMO", "DEV", "VOL"] = "DEV"
    direction: Literal["above", "below"] = "below"
    entry_quantile: float = Field(default=0.2, ge=0, le=1)
    exit_quantile: float = Field(default=0.5, ge=0, le=1)
    train_fraction: float = Field(default=0.6, ge=0.1, le=0.8)
    validation_fraction: float = Field(default=0.2, ge=0.1, le=0.8)
    initial_capital: float = Field(default=10_000, gt=0, le=1e9)
    fee_bps: float = Field(default=10, ge=0, le=500)
    slippage_bps: float = Field(default=10, ge=0, le=500)

    @model_validator(mode="after")
    def coherent_ranges(self):
        if self.train_fraction + self.validation_fraction > 0.9 + 1e-12:
            raise ValueError("训练和验证比例合计最多为 90%，测试段至少保留 10%。")
        if (self.direction == "below" and self.entry_quantile >= self.exit_quantile or
                self.direction == "above" and self.entry_quantile <= self.exit_quantile):
            raise ValueError("低位入场需入场分位小于退出分位；高位入场则相反。")
        return self


def _quantile(values: list[float], probability: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def _validate_chart(chart: dict) -> tuple[list[dict], int]:
    if chart.get("market_kind") != "continuous_spot":
        raise ValueError("回测仅支持明确声明为 continuous_spot 的连续交易现货模型。")
    if chart.get("source") not in {"birdeye", "csv", "demo"}:
        raise ValueError("此阶段只接受 Birdeye 或已声明连续现货假设的 CSV/示例数据。")
    seconds = _SECONDS.get(chart.get("timeframe"))
    if seconds is None:
        raise ValueError("研究回测仅支持 1m 至 1d 的固定周期，不支持周线/月线或交易日历市场。")
    bars = chart.get("bars")
    if not isinstance(bars, list) or not 300 <= len(bars) <= 5000:
        raise ValueError("研究需要 300 至 5000 根已收盘 K 线。")
    captured = chart.get("captured_at")
    if (not isinstance(captured, (int, float)) or isinstance(captured, bool) or
            not math.isfinite(captured) or captured <= 0):
        raise ValueError("研究数据必须保存有效的采集时刻。")
    try:
        actual_hash = (research_dataset_hash(chart) if chart.get("dataset_version") == DATASET_VERSION
                       else dataset_hash(chart))
    except (TypeError, ValueError, KeyError):
        raise ValueError("研究数据内容无效，无法核验快照指纹。") from None
    if chart.get("dataset_hash") != actual_hash:
        raise ValueError("研究数据指纹不匹配，请重新创建不可变数据集。")
    previous = None
    for bar in bars:
        try:
            timestamp, opened, volume = bar["time"], bar["ts_open"], bar["volume"]
            if (any(not isinstance(v, (int, float)) or isinstance(v, bool) or
                    not math.isfinite(v) for v in (timestamp, opened, volume)) or
                    not math.isclose(timestamp, opened / 1000, rel_tol=0, abs_tol=1e-6)):
                raise ValueError
        except (KeyError, TypeError, ValueError):
            raise ValueError("研究 K 线时间或成交量无效。") from None
        if volume <= 0:
            raise ValueError("研究数据含零或负成交量；本阶段拒绝假定这些 K 线可以成交。")
        if previous is not None and not math.isclose(timestamp - previous, seconds,
                                                     rel_tol=0, abs_tol=1e-6):
            raise ValueError("连续现货研究要求周期完整且时间升序；存在缺口、重复或未排序 K 线。")
        previous = timestamp
    quality = chart.get("quality", {})
    if quality.get("missing_intervals", 0) != 0 or quality.get("gap_count", 0) != 0:
        raise ValueError("研究数据未覆盖完整请求范围，不能忽略首尾缺失区间后回测。")
    if (quality.get("requested_start", bars[0]["time"]) != bars[0]["time"] or
            quality.get("requested_end", bars[-1]["time"] + seconds) != bars[-1]["time"] + seconds or
            quality.get("bar_count", len(bars)) != len(bars) or
            quality.get("start", bars[0]["time"]) != bars[0]["time"] or
            quality.get("end", bars[-1]["time"] + seconds) != bars[-1]["time"] + seconds or
            quality.get("interval_seconds", seconds) != seconds):
        raise ValueError("研究数据的范围或 K 线数量与请求不一致，未生成回测。")
    return bars, seconds


def _positive_finite(value: float) -> float:
    if not math.isfinite(value) or value <= 0:
        raise ValueError("价格或资金尺度超出可可靠计算范围，研究未产生有效结果。")
    return value


def _simulate(bars: list[dict], values: list[float | None], start: int, end: int,
              config: ResearchConfig, entry: float, exit_: float, seconds: int,
              multiplier: int, *, benchmark: bool = False) -> dict:
    fee_rate = config.fee_bps / 10_000 * multiplier
    slip_rate = config.slippage_bps / 10_000 * multiplier
    cash, quantity, entry_cost = config.initial_capital, 0.0, 0.0
    peak = config.initial_capital
    trades: list[dict] = []
    curve: list[dict] = []
    invested_bars = 0
    total_fees = total_slippage = 0.0
    closed_pnls: list[float] = []

    def fill(side: str, index: int, reference: float, reason: str,
             *, final: bool = False):
        nonlocal cash, quantity, entry_cost, total_fees, total_slippage
        price = reference * (1 + slip_rate if side == "buy" else 1 - slip_rate)
        pnl = None
        if side == "buy":
            entry_cost = cash
            traded_quantity = _positive_finite(cash / (price * (1 + fee_rate)))
            fee = traded_quantity * price * fee_rate
            quantity = traded_quantity
            cash = 0.0
        else:
            traded_quantity = quantity
            fee = traded_quantity * price * fee_rate
            cash = _positive_finite(cash + traded_quantity * price - fee)
            quantity = 0.0
            pnl = cash - entry_cost
            closed_pnls.append(pnl)
        slippage_cost = traded_quantity * abs(price - reference)
        total_fees += fee
        total_slippage += slippage_cost
        signal_index = index - 1 if reason == "signal" else None
        trades.append({
            "time": bars[index]["time"] + (seconds if final else 0),
            "signal_time": bars[signal_index]["time"] if signal_index is not None else None,
            "signal_at": bars[signal_index]["time"] + seconds if signal_index is not None else None,
            "bar_index": index, "signal_bar_index": signal_index,
            "side": side, "reference_price": reference, "price": price,
            "quantity": traded_quantity, "fee": fee, "slippage_cost": slippage_cost,
            "cash_after": cash, "position_after": quantity, "reason": reason,
            "pnl": pnl,
        })

    for index in range(start, end):
        bar = bars[index]
        # Each split starts flat. Its first close can signal only its second open.
        if index > start:
            previous = values[index - 1]
            if benchmark:
                if index == start + 1:
                    fill("buy", index, bar["open"], "benchmark_entry")
            elif previous is not None:
                enter = previous <= entry if config.direction == "below" else previous >= entry
                leave = previous >= exit_ if config.direction == "below" else previous <= exit_
                if quantity == 0 and enter:
                    fill("buy", index, bar["open"], "signal")
                elif quantity > 0 and leave:
                    fill("sell", index, bar["open"], "signal")
        if quantity > 0:
            invested_bars += 1
        # Liquidation is a predeclared horizon rule, not a last-bar signal.
        if index == end - 1 and quantity > 0:
            fill("sell", index, bar["close"], "segment_end", final=True)
        equity = _positive_finite(cash + quantity * bar["close"])
        peak = max(peak, equity)
        curve.append({"time": bar["time"] + seconds, "equity": equity,
                      "drawdown": max(0.0, 1 - equity / peak)})
    ending = curve[-1]["equity"]
    return {
        "metrics": {
            "initial_capital": config.initial_capital, "ending_equity": ending,
            "total_return": ending / config.initial_capital - 1,
            "max_drawdown": max(point["drawdown"] for point in curve),
            "net_pnl": ending - config.initial_capital,
            "fees": total_fees, "slippage_cost": total_slippage,
            "closed_trades": len(closed_pnls),
            "win_rate": (sum(pnl > 0 for pnl in closed_pnls) / len(closed_pnls)
                         if closed_pnls else None),
            "exposure": invested_bars / (end - start),
        },
        "equity_curve": curve,
        "trades": trades,
    }


def run_research(chart: dict, config: ResearchConfig, *,
                 values: list[float | None] | None = None, factor_label: str | None = None) -> dict:
    """Run one user-declared rule, with frozen train-only quantile thresholds."""
    bars, seconds = _validate_chart(chart)
    count = len(bars)
    train_end = int(count * config.train_fraction)
    validation_end = train_end + int(count * config.validation_fraction)
    bounds = {"train": (0, train_end), "validation": (train_end, validation_end),
              "test": (validation_end, count)}
    if train_end < 120 or validation_end - train_end < 60 or count - validation_end < 60:
        raise ValueError("按当前比例切分后，训练至少需 120 根，验证与测试各至少需 60 根。")
    if values is None:
        try:
            evidence = compute_factors(restore_frame(chart))
        except (TypeError, KeyError, OverflowError):
            raise ValueError("研究数据不是有效的已收盘 OHLCV 快照。") from None
        factor = next(item for item in evidence["factors"] if item["id"] == config.factor)
        values = [point["normalized"] for point in factor["series"]]
    elif len(values) != count or any(value is not None and
                                     (not isinstance(value, (int, float)) or
                                      isinstance(value, bool) or not math.isfinite(value))
                                     for value in values):
        raise ValueError("研究公式序列必须与 K 线对齐且仅含有限数或空值。")
    else:
        # Supplied formula values must not bypass the ordinary closed/OHLC gate.
        _validated_bars(restore_frame(chart))
    training = [value for value in values[:train_end]
                if value is not None and math.isfinite(value)]
    if len(training) < 60:
        raise ValueError("训练段至少需要 60 个有效的归一化因子值，未使用验证或测试数据补足。")
    entry = _quantile(training, config.entry_quantile)
    exit_ = _quantile(training, config.exit_quantile)
    if math.isclose(entry, exit_, rel_tol=0, abs_tol=1e-12):
        raise ValueError("训练因子分位阈值重合，无法定义有效的入场/退出区间；请换用更充分的数据或因子。")

    ranges = {name: {"start": start, "end": end, "end_exclusive": end,
                     "bar_count": end - start, "start_time": bars[start]["time"],
                     "end_time": bars[end - 1]["time"] + seconds}
              for name, (start, end) in bounds.items()}
    labels = {"train": "训练段（样本内阈值校准）", "validation": "验证段（样本外）",
              "test": "测试段（样本外）"}
    segments = {}
    sensitivity = []
    for multiplier in (0, 1, 2):
        scenario = {"multiplier": multiplier, "fee_bps": config.fee_bps * multiplier,
                    "slippage_bps": config.slippage_bps * multiplier}
        for name, (start, end) in bounds.items():
            result = _simulate(bars, values, start, end, config, entry, exit_, seconds, multiplier)
            benchmark = _simulate(bars, values, start, end, config, entry, exit_, seconds,
                                  multiplier, benchmark=True)
            scenario[f"{name}_return"] = result["metrics"]["total_return"]
            scenario[f"{name}_benchmark_return"] = benchmark["metrics"]["total_return"]
            if multiplier == 1:
                result["metrics"]["benchmark_return"] = benchmark["metrics"]["total_return"]
                result["metrics"]["benchmark_ending_equity"] = benchmark["metrics"]["ending_equity"]
                for point, reference in zip(result["equity_curve"], benchmark["equity_curve"],
                                            strict=True):
                    point["benchmark"] = reference["equity"]
                segments[name] = {"label": labels[name], "is_train": name == "train",
                                  "evaluation": "in_sample_calibration" if name == "train"
                                  else "out_of_sample", **result,
                                  "benchmark_trades": benchmark["trades"],
                                  "benchmark_metrics": benchmark["metrics"]}
        sensitivity.append(scenario)

    warnings = [
        "这是预先声明规则的历史研究，不是收益预测或自动交易；未搜索或选择最优参数。",
        "训练段阈值使用整个训练段拟合，因此其收益是样本内校准结果，不能当作样本外业绩。",
        "验证与测试段不参与阈值拟合；反复查看结果再改配置仍会产生人为选择偏差。",
        "模型假设连续现货、可分割数量、100% 多头或现金；不模拟杠杆、做空、资金费率、税费或最小下单量。",
        "成交用下一根开盘价加固定滑点和手续费；OHLCV 无法验证订单簿深度或实际成交容量。",
        "最大回撤仅按收盘权益及初始资金计算，可能低估盘中回撤；段末按预设规则以收盘价强制平仓。",
        "任意零成交量或缺失周期都会拒绝研究；未用当前流动性回填历史，也未补造 K 线。",
        "买入持有基准也从每段第二根开盘入场，按相同费用在最后收盘退出；各段独立从现金开始。",
    ]
    if chart["source"] in {"csv", "demo"}:
        warnings.append("连续现货市场身份由用户声明；上传数据来源未经独立核验，示例数据不是真实行情。")
    if any(segment["metrics"]["closed_trades"] == 0 for segment in segments.values()):
        warnings.append("至少一段没有完成交易；该段胜率为空，收益与因子有效性不能据此等同。")
    result = {
        "engine_version": ENGINE_VERSION, "feature_version": FEATURE_VERSION,
        "config": {**config.model_dump(), "factor": factor_label or config.factor},
        "dataset_hash": chart["dataset_hash"],
        "thresholds": {"entry": entry, "exit": exit_, "training_samples": len(training),
                       "fit_segment": "train", "quantile_method": "linear"},
        "split": {"method": "chronological", "bar_count": count, **ranges},
        "segments": segments, "cost_sensitivity": sensitivity, "warnings": warnings,
    }
    # Numeric errors never become persisted evidence that JSON consumers cannot read.
    try:
        json.dumps(result, allow_nan=False)
    except (ValueError, OverflowError):
        raise ValueError("研究计算超出有效数值范围，未生成结果。") from None
    return result
