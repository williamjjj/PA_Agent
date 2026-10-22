"""Versioned market evidence shared by charts, PA analysis and causal factors."""
from __future__ import annotations

import hashlib
import json

from pa_agent.web import quant
from pa_agent.web.sources import restore_frame

DATASET_VERSION = "pa-ohlcv-v1"


def instrument_for_chart(chart):
    """Older/CSV snapshots have no independently verified instrument metadata."""
    if chart.get("instrument"):
        return dict(chart["instrument"])
    request = chart.get("request", {})
    source = chart["source"]
    venue = request.get("exchange", "") if source == "tradingview" else source
    return {"id": f"{source}:{venue}:{chart['symbol']}", "symbol": chart["symbol"],
            "asset_class": "unspecified", "venue": venue, "chain": None,
            "address": None, "quote_asset": "unspecified", "volume_unit": "provider_reported"}


def dataset_hash(chart):
    # Include the actual chart indicators too: PA must not silently recalculate
    # them with a shorter warm-up window when restoring the snapshot.
    values = {"schema": DATASET_VERSION, "source": chart["source"],
              "instrument": instrument_for_chart(chart), "symbol": chart["symbol"],
              "timeframe": chart["timeframe"], "bars": chart["bars"],
              "adjustment": chart.get("adjustment", "unspecified")}
    encoded = json.dumps(values, sort_keys=True, ensure_ascii=True, allow_nan=False,
                         separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def factor_evidence(chart):
    try:
        evidence = quant.compute_factors(restore_frame(chart))
        # Serialization is part of validity: never persist NaN as numeric evidence.
        json.dumps(evidence, allow_nan=False)
    except Exception:
        evidence = {"feature_version": quant.FEATURE_VERSION, "status": "unavailable",
                    "as_of": None, "bar_count": len(chart["bars"]), "normalization": {},
                    "factors": [], "quality_flags": [{"code": "calculation_failed",
                    "message": "本快照的量化计算未完成；可继续 PA 分析，因子结果暂不可用。"}]}
    flags = list(evidence.get("quality_flags", []))
    if chart["source"] == "demo":
        flags.append({"code": "simulated_data", "message": "此快照为模拟示例，不是真实行情。"})
    if chart["source"] == "csv":
        flags.append({"code": "user_supplied", "message": "此快照来自用户上传，市场身份与数据来源未经独立核验。"})
    return {**evidence, "quality_flags": flags, "source": chart["source"],
            "instrument": instrument_for_chart(chart), "dataset_hash": chart["dataset_hash"],
            "dataset_version": DATASET_VERSION}


def with_snapshot_id(chart, ident):
    """Attach the store-generated ID without modifying any persisted snapshot."""
    chart = {**chart, "instrument": instrument_for_chart(chart)}
    chart.setdefault("dataset_hash", dataset_hash(chart))
    chart.setdefault("dataset_version", DATASET_VERSION)
    evidence = chart.get("quant_evidence")
    if evidence is None:  # Compatible with already saved pre-quant snapshots.
        evidence = factor_evidence(chart)
    return {**chart, "snapshot_id": ident,
            "quant_evidence": {**evidence, "snapshot_id": ident}}


def save_snapshot(store, uid, chart):
    chart = {**chart, "instrument": instrument_for_chart(chart), "dataset_version": DATASET_VERSION}
    chart["dataset_hash"] = dataset_hash(chart)
    chart["quant_evidence"] = factor_evidence(chart)
    ident = store.put(uid, "snapshot", chart, ttl=3600)
    return with_snapshot_id(chart, ident)


def same_market(first, second):
    def identity(chart):
        return (chart["source"], chart["timeframe"], instrument_for_chart(chart)["id"],
                chart.get("adjustment", "unspecified"))
    return identity(first) == identity(second)
