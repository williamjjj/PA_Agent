"""Causal, market/timeframe-scoped feedback from consecutive incremental analyses."""
from __future__ import annotations

import hashlib
import json
import math
import sqlite3
import time
from pathlib import Path

MIN_REVIEWS = 10


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


def market_key(symbol, timeframe, context):
    identity = {"symbol": symbol, "timeframe": timeframe,
                "source": context.get("source", "unknown"),
                "exchange": context.get("exchange", ""),
                "adjustment": context.get("adjustment", "provider")}
    return hashlib.sha256(_json(identity).encode()).hexdigest(), identity


def _direction(value):
    return {"bullish": 1, "up": 1, "看涨": 1, "上涨": 1,
            "bearish": -1, "down": -1, "看跌": -1, "下跌": -1,
            "neutral": 0, "sideways": 0, "震荡": 0, "盘整": 0}.get(str(value))


class ReviewStore:
    """Small SQLite sidecar; never rescans multi-megabyte analysis files."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS streams (key TEXT PRIMARY KEY, state TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS evaluations (
                    id TEXT PRIMARY KEY, key TEXT NOT NULL, created INTEGER NOT NULL, payload TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS evaluation_stream ON evaluations(key, created);
                CREATE TABLE IF NOT EXISTS summaries (
                    id TEXT PRIMARY KEY, key TEXT NOT NULL, created INTEGER NOT NULL, payload TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS summary_stream ON summaries(key, created);
            """)

    def _connect(self):
        return sqlite3.connect(self.path, timeout=5)

    @staticmethod
    def _state(db, key):
        row = db.execute("SELECT state FROM streams WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else {"pending": None, "consecutive": 0, "recent": []}

    @staticmethod
    def _save(db, key, state):
        db.execute("INSERT OR REPLACE INTO streams VALUES (?,?)", (key, _json(state)))

    def evaluate(self, frame, context, *, as_of_ms=None):
        """Use new closed-bar endpoints available at the next analysis submission."""
        from pa_agent.quant.quant import _bar_end_ms
        from pa_agent.quant.markets import infer_market

        now = int(time.time() * 1000) if as_of_ms is None else as_of_ms
        key, identity = market_key(frame.symbol, frame.timeframe, context)
        with self._connect() as db:
            state = self._state(db, key)
            state["identity"] = identity
            pending = state.get("pending")
            result = {"identity": identity, "consecutive": state["consecutive"],
                      "required": MIN_REVIEWS, "status": "waiting_for_previous_analysis"}
            if not pending:
                return result
            cutoff = min(now, frame.snapshot_ts_local_ms)
            bars = sorted((b for b in frame.bars if b.closed), key=lambda b: b.ts_open)
            session_calendar = (frame.timeframe in {"1d", "3d", "1w", "1M"}
                and infer_market(identity["source"], frame.symbol, identity["exchange"]) != "continuous_spot")
            eligible = [b for b in bars if b.ts_open > pending["anchor_ts"] and b.ts_open < cutoff
                        and (session_calendar or pending["completed_at_ms"] < _bar_end_ms(b, frame.timeframe) <= cutoff)]
            if not eligible:
                return {**result, "status": "waiting_for_new_closed_bars"}
            if not any(b.ts_open == pending["anchor_ts"] for b in bars):
                state.update(pending=None, consecutive=0, recent=[])
                self._save(db, key, state)
                return {**result, "consecutive": 0, "status": "history_no_longer_overlaps"}
            reference = pending["reference_price"]
            change = eligible[-1].close / reference - 1
            atr = pending.get("atr") or reference * .001
            move_atr = (eligible[-1].close - reference) / atr
            actual = 1 if move_atr > .15 else -1 if move_atr < -.15 else 0
            expected = pending["expected_direction"]
            ident = hashlib.sha256((pending["record_id"] + key).encode()).hexdigest()
            evaluation = {"id": ident, "identity": identity, "previous_record_id": pending["record_id"],
                "prediction_basis": pending["prediction_basis"], "expected_direction": expected,
                "actual_direction": actual, "direction_agrees": actual == expected if expected is not None else None,
                "reference_price": reference, "reference_quote_at_ms": pending["quote_at_ms"],
                "analysis_completed_at_ms": pending["completed_at_ms"], "evaluated_at_ms": now,
                "first_bar_ts": eligible[0].ts_open, "last_bar_ts": eligible[-1].ts_open,
                "new_closed_bars": len(eligible), "last_close": eligible[-1].close,
                "return": change, "move_atr": move_atr, "cycle_position": pending["cycle_position"],
                "neutral_band_atr": .15,
                "method": "completion_quote_to_next_analysis_closed_price"}
            inserted = db.execute("INSERT OR IGNORE INTO evaluations VALUES (?,?,?,?)",
                                  (ident, key, now, _json(evaluation))).rowcount
            if inserted:
                state["consecutive"] += 1
                state["recent"] = (state["recent"] + [evaluation])[-MIN_REVIEWS:]
            state["pending"] = None
            if inserted and state["consecutive"] % MIN_REVIEWS == 0:
                summary = self._summarize(identity, state["recent"], now)
                db.execute("INSERT OR IGNORE INTO summaries VALUES (?,?,?,?)",
                           (summary["id"], key, now, _json(summary)))
            self._save(db, key, state)
            return {**result, "status": "evaluated", "consecutive": state["consecutive"],
                    "evaluation": evaluation}

    @staticmethod
    def _summarize(identity, rows, now):
        scored = [r for r in rows if r["direction_agrees"] is not None]
        agreement = sum(r["direction_agrees"] for r in scored)
        wrong_bull = sum(r["expected_direction"] == 1 and r["actual_direction"] == -1 for r in rows)
        wrong_bear = sum(r["expected_direction"] == -1 and r["actual_direction"] == 1 for r in rows)
        lessons = [f"{identity['symbol']} / {identity['timeframe']}：最近连续 10 次增量分析与随后实盘走势对比。",
            f"可比较方向 {len(scored)} 次，其中观察窗口方向一致 {agreement} 次；偏多后下跌 {wrong_bull} 次，偏空后上涨 {wrong_bear} 次。",
            "比较以分析完成时的实际报价为起点，以下一轮分析时新增已收盘 K 线的末价为终点；震荡带为上一轮 ATR 的 0.15 倍。"]
        if wrong_bull > wrong_bear:
            lessons.append("本批次偏多观点的反向变化更多：后续偏多结论应重点复核阻力、突破跟随和失败信号。")
        elif wrong_bear > wrong_bull:
            lessons.append("本批次偏空观点的反向变化更多：后续偏空结论应重点复核支撑、下跌跟随和失败信号。")
        else:
            lessons.append("本批次未显示明确的单方向偏差，继续依据当前结构核对双向证据。")
        lessons.append("这是同品种同周期的阶段性复盘证据。优先核对当前 K 线，不复用旧价位、不跳过现有决策规则。")
        ids = [r["id"] for r in rows]
        return {"id": hashlib.sha256(_json(ids).encode()).hexdigest(), "identity": identity,
                "created_at_ms": now, "sample_count": len(rows), "evaluation_ids": ids,
                "directional_samples": len(scored), "direction_agreements": agreement,
                "text": "\n".join(lessons), "scope": "symbol_source_exchange_timeframe"}

    def register(self, record, frame, context, quote, *, completed_at_ms=None, incremental=False):
        """Freeze the issued prediction and a contemporaneous observed quote."""
        now = int(time.time()*1000) if completed_at_ms is None else completed_at_ms
        key, identity = market_key(frame.symbol, frame.timeframe, context)
        valid_quote = (quote.get("symbol") == frame.symbol and quote.get("timeframe") == frame.timeframe
                       and quote.get("source") == identity["source"]
                       and quote.get("exchange", "") == identity["exchange"]
                       and 0 <= now - quote.get("observed_at_ms", 0) <= 30_000)
        price = quote.get("price", 0)
        if not valid_quote or not isinstance(price, (float, int)) or not math.isfinite(price) or price <= 0:
            return {"status": "waiting_for_fresh_completion_quote", "required": MIN_REVIEWS}
        stage2, stage1 = record.stage2_decision or {}, record.stage1_diagnosis or {}
        prediction, basis = stage2.get("next_bar_prediction") or {}, "next_bar_prediction"
        if _direction(prediction.get("direction")) is None:
            prediction, basis = stage2.get("next_cycle_prediction") or {}, "next_cycle_prediction"
        if _direction(prediction.get("direction")) is None:
            prediction, basis = stage1, "diagnosis_direction_comparison"
        if _direction(prediction.get("direction")) is None:
            return {"status": "no_comparable_direction", "required": MIN_REVIEWS}
        atr = frame.indicators.atr14[0] if frame.indicators.atr14 else None
        if atr is not None and (not math.isfinite(atr) or atr <= 0):
            atr = None
        # Bars that closed while the model was thinking precede its issued
        # prediction. Exclude them using the live completion observation.
        anchor = max(frame.bars[0].ts_open, quote.get("latest_closed_ts", 0))
        pending = {"record_id": str(record.meta.timestamp_local_ms), "anchor_ts": anchor,
                   "completed_at_ms": now, "quote_at_ms": quote["observed_at_ms"], "reference_price": price,
                   "atr": atr, "expected_direction": _direction(prediction.get("direction")),
                   "prediction_basis": basis, "cycle_position": str(stage1.get("cycle_position", ""))}
        with self._connect() as db:
            state = self._state(db, key)
            state["identity"] = identity
            old = state.get("pending")
            if old and old["anchor_ts"] >= pending["anchor_ts"]:
                return {"status": "duplicate_snapshot_not_counted", "consecutive": state["consecutive"], "required": MIN_REVIEWS}
            if not incremental or old:
                state.update(consecutive=0, recent=[])
            state["pending"] = pending
            self._save(db, key, state)
            return {"status": "prediction_registered", "consecutive": state["consecutive"], "required": MIN_REVIEWS,
                    "completed_at_ms": now, "reference_quote": {"price": price, "observed_at_ms": quote["observed_at_ms"]}}

    def latest_summary(self, symbol, timeframe, context, *, as_of_ms):
        key, _ = market_key(symbol, timeframe, context)
        with self._connect() as db:
            row = db.execute("SELECT payload FROM summaries WHERE key=? AND created<=? ORDER BY created DESC LIMIT 1",
                             (key, as_of_ms)).fetchone()
            return json.loads(row[0]) if row else None

    def report(self):
        with self._connect() as db:
            states = [json.loads(row[0]) for row in db.execute("SELECT state FROM streams")]
            summaries = [json.loads(row[0]) for row in db.execute("SELECT payload FROM summaries ORDER BY created DESC LIMIT 50")]
            reviews = [json.loads(row[0]) for row in db.execute("SELECT payload FROM evaluations ORDER BY created DESC LIMIT 500")]
        return {"minimum_reviews": MIN_REVIEWS, "streams": states, "summaries": summaries, "reviews": reviews}
