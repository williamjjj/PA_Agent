import copy
import json
import math

import pytest
from pydantic import ValidationError

from pa_agent.web.research import ResearchConfig, _simulate, run_research
from pa_agent.web.research_data import (
    DATASET_VERSION,
    DatasetImport,
    parse_dataset_csv,
    research_dataset_hash,
)
from pa_agent.web.snapshots import dataset_hash

START = 1_727_683_200


def chart(count=400):
    bars = []
    previous = 100.0
    for index in range(count):
        close = 100 + 12 * math.sin(index / 9) + 3 * math.sin(index / 2)
        bars.append({"time": START + 60 * index, "ts_open": (START + 60 * index) * 1000,
                     "seq": count - index, "open": previous, "close": close,
                     "high": max(previous, close) + 1, "low": min(previous, close) - 1,
                     "volume": 100 + index % 19, "closed": True, "ema": None, "atr": None})
        previous = close
    result = {"symbol": "SYNTHETIC", "timeframe": "1m", "source": "csv",
              "market_kind": "continuous_spot", "captured_at": (START + 60 * count) * 1000,
              "instrument": {"id": "csv:synthetic", "asset_class": "unspecified"},
              "bars": bars}
    return freeze(result)


def freeze(value):
    value["dataset_hash"] = dataset_hash(value)
    return value


def simulate_bars(prices):
    return [{"time": START + 60 * index, "open": opened, "close": close}
            for index, (opened, close) in enumerate(prices)]


def test_known_next_open_fill_costs_and_closed_trade_ledger():
    bars = simulate_bars([(999, 900), (100, 110), (120, 120), (130, 140)])
    config = ResearchConfig(initial_capital=1000, fee_bps=100, slippage_bps=200)
    result = _simulate(bars, [-2, 0, 2, 0], 0, 4, config, -1, 1, 60, 1)
    buy, sell = result["trades"]
    quantity = 1000 / (100 * 1.02 * 1.01)
    final_cash = quantity * 130 * 0.98 * 0.99
    assert buy["bar_index"] == 1
    assert buy["signal_bar_index"] == 0
    assert buy["time"] == buy["signal_time"] + 60 == buy["signal_at"]
    assert buy["reference_price"] == 100
    assert buy["price"] == 102
    assert buy["quantity"] == pytest.approx(quantity)
    assert buy["cash_after"] == 0
    assert buy["position_after"] == pytest.approx(quantity)
    assert sell["bar_index"] == 3
    assert sell["signal_bar_index"] == 2
    assert sell["reference_price"] == 130
    assert sell["price"] == pytest.approx(127.4)
    assert sell["cash_after"] == pytest.approx(final_cash)
    assert sell["position_after"] == 0
    assert sell["pnl"] == pytest.approx(final_cash - 1000)
    assert result["metrics"]["fees"] == pytest.approx(quantity * (102 + 127.4) * 0.01)
    assert result["metrics"]["slippage_cost"] == pytest.approx(quantity * (2 + 2.6))
    assert result["metrics"]["total_return"] == pytest.approx(final_cash / 1000 - 1)
    assert result["metrics"]["closed_trades"] == 1
    assert result["metrics"]["win_rate"] == 1


def test_forced_liquidation_and_drawdown_include_initial_capital_and_all_costs():
    config = ResearchConfig(initial_capital=1000, fee_bps=100, slippage_bps=200)
    result = _simulate(simulate_bars([(100, 100)] * 4), [-2] * 4,
                       0, 4, config, -1, 1, 60, 1)
    buy, sell = result["trades"]
    expected = 1000 / (102 * 1.01) * 98 * 0.99
    assert sell["reason"] == "segment_end"
    assert sell["time"] == START + 4 * 60
    assert sell["signal_time"] is None
    assert sell["signal_bar_index"] is None
    assert result["equity_curve"][0]["equity"] == 1000
    assert result["equity_curve"][1]["equity"] == pytest.approx(buy["quantity"] * 100)
    assert result["metrics"]["ending_equity"] == pytest.approx(expected)
    assert result["metrics"]["max_drawdown"] == pytest.approx(1 - expected / 1000)
    assert result["metrics"]["net_pnl"] == pytest.approx(
        -result["metrics"]["fees"] - result["metrics"]["slippage_cost"])
    assert result["metrics"]["exposure"] == 3 / 4
    assert result["metrics"]["win_rate"] == 0


def test_each_split_starts_flat_and_no_signal_crosses_boundary():
    result = run_research(chart(), ResearchConfig())
    assert result["thresholds"]["fit_segment"] == "train"
    for name, segment in result["segments"].items():
        bounds = result["split"][name]
        assert segment["is_train"] == (name == "train")
        assert segment["evaluation"] == ("in_sample_calibration" if name == "train"
                                          else "out_of_sample")
        assert segment["equity_curve"][0]["equity"] == 10_000
        assert segment["equity_curve"][0]["benchmark"] == 10_000
        assert len(segment["equity_curve"]) == bounds["bar_count"]
        for ledger in (segment["trades"], segment["benchmark_trades"]):
            assert ledger
            assert min(t["bar_index"] for t in ledger) >= bounds["start"] + 1
            assert ledger[-1]["position_after"] == 0
            for trade in ledger:
                if trade["reason"] == "signal":
                    assert trade["signal_bar_index"] >= bounds["start"]
                    assert trade["bar_index"] == trade["signal_bar_index"] + 1
        assert segment["trades"][-1]["cash_after"] == segment["metrics"]["ending_equity"]


def test_test_future_changes_do_not_change_train_thresholds_or_validation_results():
    original = chart()
    result = run_research(original, ResearchConfig())
    mutated = copy.deepcopy(original)
    boundary = result["split"]["test"]["start"]
    for index, bar in enumerate(mutated["bars"][boundary:]):
        for key in ("open", "high", "low", "close"):
            bar[key] *= 5 + index / 10
        bar["volume"] *= 50
    other = run_research(freeze(mutated), ResearchConfig())
    assert result["dataset_hash"] != other["dataset_hash"]
    assert result["thresholds"] == other["thresholds"]
    assert result["segments"]["train"] == other["segments"]["train"]
    assert result["segments"]["validation"] == other["segments"]["validation"]
    assert result["segments"]["test"] != other["segments"]["test"]


def test_validation_and_test_never_supply_threshold_calibration_values():
    original = chart()
    result = run_research(original, ResearchConfig(factor="VOL"))
    mutated = copy.deepcopy(original)
    for bar in mutated["bars"][result["split"]["train"]["end"]:]:
        bar["volume"] = 1e15
    other = run_research(freeze(mutated), ResearchConfig(factor="VOL"))
    assert result["thresholds"] == other["thresholds"]
    assert result["segments"]["train"] == other["segments"]["train"]
    # VOL has nineteen warmup nulls, all calibration samples must be within train.
    assert result["thresholds"]["training_samples"] == result["split"]["train"]["bar_count"] - 19


def test_cost_scenarios_are_fixed_and_use_identical_frozen_rule_and_benchmark():
    result = run_research(chart(), ResearchConfig())
    free, base, doubled = result["cost_sensitivity"]
    assert [case["multiplier"] for case in result["cost_sensitivity"]] == [0, 1, 2]
    for name, segment in result["segments"].items():
        assert free[f"{name}_return"] > base[f"{name}_return"] > doubled[f"{name}_return"]
        assert base[f"{name}_return"] == segment["metrics"]["total_return"]
        assert base[f"{name}_benchmark_return"] == segment["metrics"]["benchmark_return"]
        assert free[f"{name}_benchmark_return"] > doubled[f"{name}_benchmark_return"]
    assert base["fee_bps"] == base["slippage_bps"] == 10
    assert doubled["fee_bps"] == doubled["slippage_bps"] == 20


def test_all_ledgers_reconcile_to_cash_returns_and_metrics():
    source = chart()
    unchanged = copy.deepcopy(source)
    result = run_research(source, ResearchConfig())
    assert source == unchanged
    json.dumps(result, allow_nan=False)
    for segment in result["segments"].values():
        for trades, metrics in ((segment["trades"], segment["metrics"]),
                                (segment["benchmark_trades"], segment["benchmark_metrics"])):
            cash, position = metrics["initial_capital"], 0.0
            for trade in trades:
                if trade["side"] == "buy":
                    cash -= trade["price"] * trade["quantity"] + trade["fee"]
                    position += trade["quantity"]
                else:
                    cash += trade["price"] * trade["quantity"] - trade["fee"]
                    position -= trade["quantity"]
                assert cash == pytest.approx(trade["cash_after"], abs=1e-8)
                assert position == pytest.approx(trade["position_after"])
            assert position == 0
            assert cash == pytest.approx(metrics["ending_equity"])
            assert sum(t["fee"] for t in trades) == pytest.approx(metrics["fees"])
            assert sum(t["slippage_cost"] for t in trades) == pytest.approx(metrics["slippage_cost"])
            assert sum(t["pnl"] for t in trades if t["pnl"] is not None) == pytest.approx(metrics["net_pnl"])


@pytest.mark.parametrize("count", [0, 299, 5001])
def test_insufficient_or_excessive_bar_counts_reject(count):
    with pytest.raises(ValueError, match="300 至 5000"):
        run_research(chart(count), ResearchConfig())


def test_split_minimum_sizes_are_enforced_even_when_total_count_is_valid():
    with pytest.raises(ValueError, match="训练至少需 120"):
        run_research(chart(300), ResearchConfig(train_fraction=0.1, validation_fraction=0.4))
    with pytest.raises(ValueError, match="各至少需 60"):
        run_research(chart(300), ResearchConfig(train_fraction=0.7, validation_fraction=0.1))


def test_constant_factor_thresholds_do_not_generate_artificial_trades():
    source = chart()
    for bar in source["bars"]:
        for name in ("open", "high", "low", "close"):
            bar[name] = 100
    with pytest.raises(ValueError, match="阈值重合"):
        run_research(freeze(source), ResearchConfig())


@pytest.mark.parametrize("mutation,match", [
    ("zero_volume", "零或负成交量"), ("gap", "周期完整"), ("duplicate", "周期完整"),
    ("weekly", "不支持周线/月线"), ("calendar_market", "连续交易现货"),
    ("unsupported_source", "Birdeye"), ("forming", "仅接受已收盘"),
    ("timestamp_unclosed", "尚未收盘"), ("time_mismatch", "时间或成交量无效"),
    ("no_captured_at", "采集时刻"),
])
def test_unsupported_or_unreliable_datasets_reject(mutation, match):
    source = chart()
    if mutation == "zero_volume":
        source["bars"][-1]["volume"] = 0
    elif mutation == "gap":
        for bar in source["bars"][10:]:
            bar["time"] += 60
            bar["ts_open"] += 60_000
        source["captured_at"] += 60_000
    elif mutation == "duplicate":
        source["bars"][11] = source["bars"][10]
    elif mutation == "weekly":
        source["timeframe"] = "1w"
    elif mutation == "calendar_market":
        source["market_kind"] = "equity"
    elif mutation == "unsupported_source":
        source["source"] = "eastmoney"
    elif mutation == "forming":
        source["bars"][-1]["closed"] = False
    elif mutation == "timestamp_unclosed":
        source["captured_at"] -= 1
    elif mutation == "time_mismatch":
        source["bars"][5]["time"] += 1
    elif mutation == "no_captured_at":
        source.pop("captured_at")
    with pytest.raises(ValueError, match=match):
        run_research(freeze(source), ResearchConfig())


def test_mutated_snapshot_hash_is_rejected():
    source = chart()
    source["bars"][5]["volume"] += 1
    with pytest.raises(ValueError, match="指纹不匹配"):
        run_research(source, ResearchConfig())


def test_research_dataset_hash_schema_is_accepted():
    source = chart()
    source["dataset_version"] = DATASET_VERSION
    source["quality"] = {"start": START, "end": START + 400 * 60}
    source["dataset_hash"] = research_dataset_hash(source)
    result = run_research(source, ResearchConfig())
    assert result["dataset_hash"] == source["dataset_hash"]


def test_imported_300_bar_dataset_runs_through_real_ingestion_and_research_contract():
    source = chart(300)
    rows = ["time,open,high,low,close,volume"]
    rows.extend(",".join(str(bar[key]) for key in ("time", "open", "high", "low", "close", "volume"))
                for bar in source["bars"])
    imported = parse_dataset_csv(DatasetImport(csv="\n".join(rows), timeframe="1m"))
    result = run_research(imported, ResearchConfig())
    assert result["dataset_hash"] == imported["dataset_hash"]
    assert [result["split"][name]["bar_count"] for name in ("train", "validation", "test")] == [180, 60, 60]


@pytest.mark.parametrize("quality", [
    {"missing_intervals": 20}, {"requested_start": START - 60},
    {"requested_end": START + 60 * 401}, {"bar_count": 600},
    {"gap_count": 1}, {"interval_seconds": 900},
])
def test_missing_requested_leading_or_trailing_coverage_rejects_even_if_rows_contiguous(quality):
    source = chart()
    source["quality"] = quality
    with pytest.raises(ValueError, match="请求"):
        run_research(source, ResearchConfig())


@pytest.mark.parametrize("changes", [
    {"unknown": 1}, {"factor": "LIQ"}, {"direction": "above"},
    {"entry_quantile": 0.5, "exit_quantile": 0.5},
    {"train_fraction": 0.8, "validation_fraction": 0.2},
    {"train_fraction": 0.09}, {"initial_capital": 0}, {"initial_capital": float("inf")},
    {"fee_bps": -1}, {"fee_bps": 501}, {"slippage_bps": 501},
    {"entry_quantile": float("nan")},
])
def test_config_rejects_invalid_rules_and_nonfinite_numbers(changes):
    with pytest.raises(ValidationError):
        ResearchConfig(**changes)


def test_above_direction_inverts_entry_exit_without_search():
    result = run_research(chart(), ResearchConfig(direction="above", entry_quantile=0.8))
    assert result["thresholds"]["entry"] > result["thresholds"]["exit"]
    assert result["config"]["entry_quantile"] == 0.8
    assert len(result["cost_sensitivity"]) == 3


def test_no_trades_has_null_win_rate_and_cash_equity():
    config = ResearchConfig(initial_capital=1000)
    result = _simulate(simulate_bars([(100, 100)] * 4), [2] * 4,
                       0, 4, config, -1, 1, 60, 1)
    assert result["trades"] == []
    assert result["metrics"]["win_rate"] is None
    assert result["metrics"]["total_return"] == 0
    assert result["metrics"]["exposure"] == 0
