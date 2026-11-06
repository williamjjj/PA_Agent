import copy
import math

import pytest

from pa_agent.quant.local import run_job
from pa_agent.quant.markets import TIMEFRAME_SECONDS
from pa_agent.quant.mining import MiningConfig, MiningSession
from pa_agent.quant.quant import compute_factors
from pa_agent.quant.research import ResearchConfig, _simulate, run_research
from pa_agent.quant.research_data import DatasetImport, parse_dataset_csv, dataset_from_snapshot
from pa_agent.quant.sources import restore_frame


def dataset(kind="stock", timeframe="5m", gaps=True):
    step = TIMEFRAME_SECONDS[timeframe]
    start = 1_577_836_800  # 2020: even 300 weekly candles stay in the past.
    start -= start % step
    rows = ["time,open,high,low,close,volume"]
    previous = 100.
    ts = start
    for index in range(300):
        close = 100 + 8*math.sin(index/9) + math.sin(index/2)
        rows.append(f"{ts},{previous},{max(previous,close)+1},{min(previous,close)-1},{close},{100+index%23}")
        previous = close
        ts += step
        if gaps and index and index % 50 == 0:
            ts += 2 * 86400  # Preserved session/weekend breaks, never filled.
    return parse_dataset_csv(DatasetImport(csv="\n".join(rows),symbol="TEST",timeframe=timeframe,market_kind=kind))


@pytest.mark.parametrize("kind", ["stock", "futures", "forex"])
@pytest.mark.parametrize("timeframe", ["5m", "1h", "1d", "1w"])
def test_research_accepts_trading_sessions_and_multiple_timeframes(kind, timeframe):
    chart = dataset(kind, timeframe)
    result = run_research(chart, ResearchConfig())
    assert result["market_kind"] == kind and result["timeframe"] == timeframe
    assert result["split"]["bar_count"] == 300
    assert result["segments"]["test"]["evaluation"] == "out_of_sample"
    assert chart["quality"]["calendar_verified"] is False
    assert chart["quality"]["missing_intervals"] is None


def test_lag_factors_include_observed_session_return_without_future_leakage():
    chart = dataset()
    factors = compute_factors(restore_frame(chart), market_kind="stock")
    ret = next(f for f in factors["factors"] if f["id"] == "RET")["series"]
    assert ret[51]["value"] is not None
    changed = copy.deepcopy(chart)
    for bar in changed["bars"][220:]:
        for key in ("open", "high", "low", "close"):
            bar[key] *= 2
    other = compute_factors(restore_frame(changed), market_kind="stock")
    assert all(a["series"][:220] == b["series"][:220] for a,b in zip(factors["factors"],other["factors"]))


def test_actual_pa_source_can_backtest_and_mine_without_csv_roundtrip(tmp_path):
    original = dataset("futures")
    original["source"] = "tradingview"
    chart = dataset_from_snapshot(original, market_kind="futures")
    result = run_job("research", chart, {"position_mode":"long_short"}, tmp_path)
    assert result["dataset"]["source"] == "tradingview"
    session = MiningSession(chart, MiningConfig(trials=8,min_trades=1))
    while not session.done:
        session.step()
    result = session.finish()
    assert result["research_result"]["market_kind"] == "futures"
    assert result["research_result"]["split"]["test"]["bar_count"] == 60


def test_short_contract_pnl_uses_multiplier_and_next_open():
    bars = [{"time":i*60,"open":p,"close":p,"volume":100} for i,p in enumerate([100,100,90,80])]
    config = ResearchConfig(initial_capital=1000,fee_bps=0,slippage_bps=0,
        position_mode="short_only",contract_multiplier=10,quantity_step=1)
    result = _simulate(bars,[-2,0,2,0],0,4,config,-1,1,60,1)
    first, last = result["trades"]
    assert first["side"] == "sell" and first["quantity"] == 1
    assert first["bar_index"] == 1 and first["signal_bar_index"] == 0
    assert last["side"] == "buy" and last["pnl"] == 200
    assert result["metrics"]["ending_equity"] == 1200


def test_whole_lots_and_zero_volume_do_not_create_fills():
    bars = [{"time":i*60,"open":10.,"close":10.,"volume":v} for i,v in enumerate([10,0,10,10])]
    config = ResearchConfig(initial_capital=500,fee_bps=0,slippage_bps=0,quantity_step=100)
    result = _simulate(bars,[-2,-2,2,0],0,4,config,-1,1,60,1)
    assert result["trades"] == [] and result["metrics"]["ending_equity"] == 500


def test_cli_snapshot_market_override_changes_identity(tmp_path):
    import json
    from types import SimpleNamespace
    from pa_agent.cli import _load_chart
    from pa_agent.quant.snapshots import dataset_hash
    chart = dataset("futures")
    path = tmp_path / "snapshot.json"
    path.write_text(json.dumps(chart))
    args = SimpleNamespace(demo=False, input=str(path), source=None, market_kind="stock", exchange="")
    loaded = _load_chart(args, research=True)
    assert loaded["market_kind"] == "stock"
    assert "dataset_version" not in loaded
    assert loaded["dataset_hash"] == dataset_hash(loaded)
    result = run_job("research", loaded, {}, tmp_path / "research")
    assert result["result"]["market_kind"] == "stock"
