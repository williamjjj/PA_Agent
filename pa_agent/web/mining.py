"""Resumable bounded CPU search: candidates are selected on training data only."""
from __future__ import annotations

import copy
import hashlib
import json
import math

from pydantic import BaseModel, ConfigDict, Field

from pa_agent.web.formula import (
    FEATURES,
    VOCAB_VERSION,
    candidate_tokens,
    evaluate_formula,
    validate_tokens,
)
from pa_agent.web.quant import FEATURE_VERSION, compute_factors
from pa_agent.web.research import ENGINE_VERSION as RESEARCH_ENGINE_VERSION
from pa_agent.web.research import (
    ResearchConfig,
    _quantile,
    _simulate,
    _validate_chart,
    run_research,
)
from pa_agent.web.sources import restore_frame

SEARCH_VERSION = MINING_VERSION = "pa-rpn-search-v1"
CHECKPOINT_VERSION = "pa-mining-checkpoint-v1"


class MiningConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    seed: int = Field(default=42, ge=0, le=2**31 - 1, strict=True)
    trials: int = Field(default=32, ge=8, le=128, strict=True)
    max_seconds: int = Field(default=60, ge=10, le=120, strict=True)
    research: ResearchConfig = Field(default_factory=ResearchConfig)
    min_trades: int = Field(default=1, ge=1, le=20, strict=True)


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=True,
                                     allow_nan=False, separators=(",", ":")).encode()).hexdigest()


class MiningSession:
    """One bounded step per candidate; worker owns time, cancellation and persistence.

    Only training feature slices enter candidate evaluation. Holdout metrics are
    first computed in finish(), after all candidates and the best candidate are
    frozen. An interrupted search retains its training-only checkpoint.
    """

    def __init__(self, chart: dict, config: MiningConfig, checkpoint: dict | None = None):
        self.chart = copy.deepcopy(chart)
        self.config = MiningConfig.model_validate(config.model_dump())
        self.bars, self.seconds = _validate_chart(self.chart)
        self.train_end = int(len(self.bars) * config.research.train_fraction)
        validation_count = int(len(self.bars) * config.research.validation_fraction)
        if (self.train_end < 120 or validation_count < 60 or
                len(self.bars) - self.train_end - validation_count < 60):
            raise ValueError("公式研究需要训练至少 120 根，验证和测试各至少 60 根。")
        evidence = compute_factors(restore_frame(self.chart))
        self.features = {item["id"]: [point["normalized"] for point in item["series"]]
                         for item in evidence["factors"] if item["id"] in FEATURES}
        self.training_features = {name: values[:self.train_end]
                                  for name, values in self.features.items()}
        training_hash = _digest({
            "source": chart["source"], "instrument": chart["instrument"],
            "market_kind": chart["market_kind"], "timeframe": chart["timeframe"],
            "bars": [[bar[key] for key in ("ts_open", "open", "high", "low", "close", "volume")]
                     for bar in self.bars[:self.train_end]],
        })
        self.identity = {
            "checkpoint_version": CHECKPOINT_VERSION, "search_version": SEARCH_VERSION,
            "vocab_version": VOCAB_VERSION, "feature_version": FEATURE_VERSION,
            "research_engine_version": RESEARCH_ENGINE_VERSION,
            "dataset_hash": chart["dataset_hash"], "training_dataset_hash": training_hash,
            "config": self.config.model_dump(), "config_hash": _digest(self.config.model_dump()),
        }
        self.next_index, self.rejected, self.ranked = 0, 0, []
        self._finished = None
        if checkpoint is not None:
            self._restore(checkpoint)

    def _restore(self, checkpoint):
        try:
            body = {key: value for key, value in checkpoint.items() if key != "checksum"}
            if checkpoint.get("checksum") != _digest(body):
                raise ValueError
            if any(checkpoint.get(key) != value for key, value in self.identity.items()):
                raise ValueError
            next_index, rejected, ranked = (checkpoint["next_index"], checkpoint["rejected"],
                                            checkpoint["ranked"])
            if (type(next_index) is not int or not 0 <= next_index <= self.config.trials or
                    type(rejected) is not int or not 0 <= rejected <= next_index or
                    not isinstance(ranked, list) or len(ranked) > min(10, next_index - rejected)):
                raise ValueError
            for item in ranked:
                index = item["candidate_index"]
                if (type(index) is not int or not 0 <= index < next_index or
                        item["tokens"] != candidate_tokens(self.config.seed, index) or
                        not math.isfinite(item["fitness"])):
                    raise ValueError
            if checkpoint.get("best") != (ranked[0] if ranked else None):
                raise ValueError
            self.next_index, self.rejected, self.ranked = next_index, rejected, copy.deepcopy(ranked)
        except (ValueError, TypeError, KeyError, AttributeError, OverflowError):
            raise ValueError("公式挖掘检查点损坏，或与当前数据、配置、计算版本不一致。") from None

    @property
    def done(self):
        return self.next_index >= self.config.trials

    def checkpoint(self):
        payload = {**self.identity, "next_index": self.next_index, "rejected": self.rejected,
                   "ranked": self.ranked, "best": self.ranked[0] if self.ranked else None}
        return copy.deepcopy({**payload, "checksum": _digest(payload)})

    def _candidate(self, index):
        tokens = candidate_tokens(self.config.seed, index)
        values = evaluate_formula(tokens, self.training_features)
        training = [value for value in values if value is not None]
        if len(training) < 60:
            raise ValueError("训练段有效公式值不足。")
        entry = _quantile(training, self.config.research.entry_quantile)
        exit_ = _quantile(training, self.config.research.exit_quantile)
        if math.isclose(entry, exit_, rel_tol=0, abs_tol=1e-12):
            raise ValueError("训练段公式阈值重合。")
        simulation = _simulate(self.bars[:self.train_end], values, 0, self.train_end,
                               self.config.research, entry, exit_, self.seconds, 1)
        metrics = simulation["metrics"]
        if metrics["closed_trades"] < self.config.min_trades:
            raise ValueError("训练段完成交易数不足。")
        fitness = metrics["total_return"] - 0.5 * metrics["max_drawdown"]
        if not math.isfinite(fitness):
            raise ValueError("训练评分不是有限数。")
        return {"candidate_index": index, "tokens": tokens,
                "expression": validate_tokens(tokens), "fitness": fitness,
                "thresholds": {"entry": entry, "exit": exit_, "training_samples": len(training)},
                "train_metrics": metrics}

    def step(self):
        if self.done:
            return self.checkpoint()
        if self._finished is not None:
            raise ValueError("此公式搜索已经结束，不能继续评估候选。")
        try:
            candidate = self._candidate(self.next_index)
        except (ValueError, OverflowError, ZeroDivisionError):
            self.rejected += 1
        else:
            if not any(item["tokens"] == candidate["tokens"] for item in self.ranked):
                self.ranked.append(candidate)
                self.ranked.sort(key=lambda item: (-item["fitness"], item["candidate_index"]))
                self.ranked = self.ranked[:10]
        self.next_index += 1
        return self.checkpoint()

    def finish(self):
        if self._finished is not None:
            return copy.deepcopy(self._finished)
        if not self.done:
            raise ValueError("候选搜索尚未完成，只有训练检查点，不能生成最终公式产物。")
        if not self.ranked:
            raise ValueError("已评估候选中没有满足训练样本和交易数量要求的公式。")
        selected = copy.deepcopy(self.ranked[0])
        values = evaluate_formula(selected["tokens"], self.features)
        research_result = run_research(self.chart, self.config.research, values=values,
                                        factor_label="FORMULA")
        if any(research_result["thresholds"][key] != value
               for key, value in selected["thresholds"].items()):
            raise ValueError("获选公式的训练阈值复核不一致，未选择其他候选替代。")
        warnings = [
            "公式由训练段多次尝试后选出，训练评分存在搜索选择偏差；不是样本外优势证明。",
            "候选资格与排序仅使用训练段；验证和测试只在冻结获选公式后计算，不用于替换获选者。",
            "这是有预算的确定性 CPU 公式搜索，不是旧 AlphaGPT Transformer 训练或实盘交易。",
            "重复查看样本外结果再修改配置、种子或数据，会造成新的选择偏差。",
        ]
        research_result["warnings"][0] = warnings[0]
        research_result["formula_quality"] = {
            name: {"valid_values": sum(value is not None for value in values[span["start"]:span["end"]]),
                   "null_values": sum(value is None for value in values[span["start"]:span["end"]])}
            for name, span in research_result["split"].items()
            if name in ("train", "validation", "test")
        }
        formula = {
            "tokens": selected["tokens"], "expression": selected["expression"],
            "vocab_version": VOCAB_VERSION, "feature_version": FEATURE_VERSION,
            "engine_version": SEARCH_VERSION, "research_engine_version": RESEARCH_ENGINE_VERSION,
            "training_dataset_hash": self.identity["training_dataset_hash"],
            "source_dataset_hash": self.chart["dataset_hash"], "seed": self.config.seed,
            "config": self.config.model_dump(), "candidate_index": selected["candidate_index"],
        }
        formula["id"] = formula["hash"] = _digest(formula)
        self._finished = {
            "formula": formula, "research_result": research_result,
            "selection": {"method": "train_only_bounded_random_search", "segment": "train",
                          "objective": "total_return - 0.5 * max_drawdown",
                          "fitness": selected["fitness"], "attempted": self.next_index,
                          "rejected": self.rejected, "trials_requested": self.config.trials,
                          "completed": self.done, "leaderboard": copy.deepcopy(self.ranked),
                          "ignored_research_fields": ["factor"]},
            "warnings": warnings,
        }
        return copy.deepcopy(self._finished)
