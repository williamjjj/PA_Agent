from types import SimpleNamespace

from pa_agent.data.base import IndicatorBundle, KlineBar, KlineFrame
from pa_agent.records.rolling_review import ReviewStore

START = 1_700_000_100_000
CONTEXT = {"source": "tradingview", "exchange": "NASDAQ"}


def frame(index, symbol="QQQ", timeframe="5m"):
    end = START + index * 300_000
    bars = tuple(KlineBar(seq=i+1, ts_open=end-(i+1)*300_000, open=100+index,
                         high=102+index, low=99+index, close=101+index, volume=100,
                         closed=True) for i in range(30))
    return KlineFrame(symbol, timeframe, bars, IndicatorBundle((100.,)*30, (1.,)*30), end)


def register(store, index, context=CONTEXT):
    f = frame(index)
    record = SimpleNamespace(meta=SimpleNamespace(timestamp_local_ms=f.snapshot_ts_local_ms),
                             stage1_diagnosis={"direction": "bullish", "cycle_position": "trend"},
                             stage2_decision={"next_bar_prediction": {"direction": "bullish"}})
    quote = {"symbol": "QQQ", "timeframe": "5m", **context,
             "observed_at_ms": f.snapshot_ts_local_ms+1000, "price": 101+index}
    return store.register(record, f, context, quote, completed_at_ms=f.snapshot_ts_local_ms+1000,
                          incremental=index > 0)


def test_ten_consecutive_completed_reviews_and_causal_summary(tmp_path):
    store = ReviewStore(tmp_path/"learning.sqlite3")
    register(store, 0)
    for index in range(1, 11):
        f = frame(index)
        result = store.evaluate(f, CONTEXT, as_of_ms=f.snapshot_ts_local_ms)
        assert result["consecutive"] == index
        summary = store.latest_summary("QQQ", "5m", CONTEXT, as_of_ms=f.snapshot_ts_local_ms)
        assert (summary is not None) == (index == 10)
        register(store, index)
    assert summary["sample_count"] == summary["direction_agreements"] == 10
    assert len(set(summary["evaluation_ids"])) == 10
    assert store.latest_summary("QQQ", "5m", CONTEXT, as_of_ms=summary["created_at_ms"]-1) is None
    assert store.latest_summary("QQQ", "1h", CONTEXT, as_of_ms=10**15) is None
    assert store.latest_summary("TSLA", "5m", CONTEXT, as_of_ms=10**15) is None
    assert store.latest_summary("QQQ", "5m", {**CONTEXT,"exchange":"NYSE"}, as_of_ms=10**15) is None


def test_no_new_closed_bars_or_duplicate_result_cannot_inflate_count(tmp_path):
    store = ReviewStore(tmp_path/"learning.sqlite3")
    register(store, 0)
    assert register(store, 0)["status"] == "duplicate_snapshot_not_counted"
    f = frame(0)
    assert store.evaluate(f, CONTEXT, as_of_ms=f.snapshot_ts_local_ms+2000)["consecutive"] == 0
    f = frame(1)
    assert store.evaluate(f, CONTEXT, as_of_ms=f.snapshot_ts_local_ms)["consecutive"] == 1
    assert store.evaluate(f, CONTEXT, as_of_ms=f.snapshot_ts_local_ms)["consecutive"] == 1
    assert len(store.report()["reviews"]) == 1


def test_reference_quote_is_required_and_must_match_market(tmp_path):
    store = ReviewStore(tmp_path/"learning.sqlite3")
    f = frame(0)
    record = SimpleNamespace(meta=SimpleNamespace(timestamp_local_ms=START),
                             stage1_diagnosis={"direction":"bearish"}, stage2_decision={})
    assert store.register(record,f,CONTEXT,{},completed_at_ms=START+1000)["status"] == "waiting_for_fresh_completion_quote"
    assert not store.report()["streams"]


def test_fresh_full_analysis_resets_consecutive_batch(tmp_path):
    store = ReviewStore(tmp_path / "learning.sqlite3")
    register(store, 0)
    store.evaluate(frame(1), CONTEXT, as_of_ms=frame(1).snapshot_ts_local_ms)
    f = frame(1)
    record = SimpleNamespace(meta=SimpleNamespace(timestamp_local_ms=f.snapshot_ts_local_ms),
        stage1_diagnosis={"direction":"bullish"}, stage2_decision={})
    quote = {"symbol":"QQQ", "timeframe":"5m", **CONTEXT,
             "price":102, "observed_at_ms":f.snapshot_ts_local_ms+1000}
    result = store.register(record,f,CONTEXT,quote,completed_at_ms=f.snapshot_ts_local_ms+1000,incremental=False)
    assert result["consecutive"] == 0


def test_orchestrator_injects_only_completed_same_scope_summary(tmp_path, monkeypatch):
    from pa_agent.orchestrator.two_stage import TwoStageOrchestrator
    import pa_agent.config.paths as paths
    from pa_agent.config.settings import Settings
    monkeypatch.setattr(paths, "DATA_ROOT", tmp_path)
    store = ReviewStore(tmp_path / "learning" / "reviews.sqlite3")
    register(store, 0)
    for index in range(1,11):
        store.evaluate(frame(index),CONTEXT,as_of_ms=frame(index).snapshot_ts_local_ms)
        register(store,index)
    engine = TwoStageOrchestrator.__new__(TwoStageOrchestrator)
    engine._learning_context, engine._settings = CONTEXT, Settings()
    record = SimpleNamespace(learning={})
    engine._prepare_review(record,frame(10),True)
    messages = [{"role":"user","content":"original"}]
    assert len(engine._with_review(messages)) == 2
    assert record.learning["summary_used"]["sample_count"] == 10
    engine._prepare_review(record,frame(10,timeframe="1h"),True)
    assert engine._with_review(messages) == messages


def test_legacy_experience_does_not_mix_timeframes_before_top_five(tmp_path):
    import json
    from pa_agent.records.experience_reader import ExperienceReader
    directory = tmp_path / "trend" / "success_cases"
    directory.mkdir(parents=True)
    names = [f"2026-01-01_12-00-{i:02d}_QQQ_1h.json" for i in range(1,8)]
    names += ["2026-01-01_12-00-00_QQQ_5m.json"]
    for name in names: (directory/name).write_text(json.dumps({"direction":"bullish"}))
    entries = ExperienceReader(tmp_path).read_for_stage2("trend",symbol="QQQ",timeframe="5m")
    assert len(entries) == 1 and entries[0].filename.endswith("QQQ_5m.json")


def test_bars_closed_during_model_run_are_not_scored_as_future(tmp_path):
    store = ReviewStore(tmp_path / "learning.sqlite3")
    submitted, completed = frame(0), frame(3)
    record = SimpleNamespace(meta=SimpleNamespace(timestamp_local_ms=START),
        stage1_diagnosis={"direction":"bullish"}, stage2_decision={})
    quote = {"symbol":"QQQ", "timeframe":"5m", **CONTEXT, "price":104,
        "observed_at_ms":completed.snapshot_ts_local_ms+1000,
        "latest_closed_ts":completed.bars[0].ts_open}
    store.register(record,submitted,CONTEXT,quote,completed_at_ms=quote["observed_at_ms"])
    assert store.evaluate(completed,CONTEXT,as_of_ms=quote["observed_at_ms"])["consecutive"] == 0
    evaluated = store.evaluate(frame(4),CONTEXT,as_of_ms=frame(4).snapshot_ts_local_ms)
    assert evaluated["evaluation"]["new_closed_bars"] == 1


def test_closed_stock_daily_bar_uses_observed_session_not_24_hour_end(tmp_path):
    store = ReviewStore(tmp_path / "learning.sqlite3")
    opened = 1_700_000_000_000
    def day(n):
        bars = tuple(KlineBar(seq=i+1,ts_open=opened+(n-i)*86400000,open=100,high=103,
            low=99,close=101+n,volume=100,closed=True) for i in range(2))
        return KlineFrame("QQQ","1d",bars,IndicatorBundle((100.,100.),(1.,1.)),
                          opened+n*86400000+7*3600000)
    initial = day(0)
    record = SimpleNamespace(meta=SimpleNamespace(timestamp_local_ms=opened),
        stage1_diagnosis={"direction":"bullish"},stage2_decision={})
    quote = {"symbol":"QQQ","timeframe":"1d",**CONTEXT,"price":101,
        "observed_at_ms":initial.snapshot_ts_local_ms,"latest_closed_ts":opened}
    store.register(record,initial,CONTEXT,quote,completed_at_ms=initial.snapshot_ts_local_ms)
    result = store.evaluate(day(1),CONTEXT,as_of_ms=day(1).snapshot_ts_local_ms)
    assert result["consecutive"] == 1
