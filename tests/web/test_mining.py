import copy
import json
import math
import random

import pytest
from pydantic import ValidationError

from pa_agent.web import mining
from pa_agent.web.formula import (
    FEATURES,
    candidate_tokens,
    evaluate_formula,
    validate_tokens,
)
from pa_agent.web.mining import MiningConfig, MiningSession
from pa_agent.web.research import ResearchConfig, run_research
from pa_agent.web.research_data import DatasetImport, parse_dataset_csv, research_dataset_hash


def chart():
    rows = ["time,open,high,low,close,volume"]
    previous = 100
    for index in range(400):
        close = 100 + 12 * math.sin(index / 9) + 3 * math.sin(index / 2)
        rows.append(f"{1727683200 + 60 * index},{previous},{max(previous, close) + 1},"
                    f"{min(previous, close) - 1},{close},{100 + index % 19}")
        previous = close
    return parse_dataset_csv(DatasetImport(csv="\n".join(rows), timeframe="1m"))


def complete(session):
    while not session.done:
        session.step()
    return session


@pytest.mark.parametrize("tokens,expected", [
    (["RET", "VOL", "ADD"], [3, None, 3, 4.0000000000001]),
    (["RET", "VOL", "SUB"], [-1, None, 3, 3.9999999999999]),
    (["RET", "VOL", "MUL"], [2, None, 0, 4e-13]),
    (["RET", "VOL", "DIV"], [0.5, None, None, None]),
    (["RET", "NEG"], [-1, None, -3, -4]),
    (["RET", "ABS"], [1, None, 3, 4]),
    (["RET", "SIGN"], [1, None, 1, 1]),
    (["RET", "DELAY1"], [None, 1, None, 3]),
    (["VOL", "MEAN3"], [None, None, 4 / 3, (2 + 1e-13) / 3]),
    (["VOL", "DECAY"], [None, None, 1, (2 + 3e-13) / 6]),
    (["VOL", "MAX3"], [None, None, 2, 2]),
])
def test_named_operators_known_vectors_and_null_propagation(tokens, expected):
    result = evaluate_formula(tokens, {"RET": [1, None, 3, 4], "VOL": [2, 2, 0, 1e-13]})
    for actual, wanted in zip(result, expected, strict=True):
        assert actual is None if wanted is None else actual == pytest.approx(wanted)


def test_overflow_and_incomplete_windows_are_null_not_zero():
    assert evaluate_formula(["RET", "VOL", "MUL"], {"RET": [1e308], "VOL": [1e308]}) == [None]
    assert evaluate_formula(["RET", "MEAN3"], {"RET": [1, None, 3, 4, 5]}) == [None, None, None, None, 4]
    assert evaluate_formula(["RET", "SIGN"], {"RET": [-2, 0, 2]}) == [-1, 0, 1]


@pytest.mark.parametrize("tokens", [
    [], [1, 2, 3], ["RET", "UNKNOWN"], ["RET", "LEAD1"],
    ["RET", "ADD"], ["RET", "VOL"], ["RET", "NEG", "NEG", "NEG", "NEG"],
    ["RET"] * 16, ["__import__('os').system('anything')"],
])
def test_invalid_old_or_noncausal_programs_reject(tokens):
    with pytest.raises(ValueError):
        validate_tokens(tokens)


def test_candidate_generation_is_bounded_seeded_and_independent_of_random_global_state():
    expected = [candidate_tokens(42, index) for index in range(128)]
    random.seed(999)
    assert expected == [candidate_tokens(42, index) for index in range(128)]
    assert expected != [candidate_tokens(43, index) for index in range(128)]
    assert expected[:5] == [[name] for name in FEATURES]
    for seed in (0, 42, 2**31 - 1):
        for index in range(128):
            tokens = candidate_tokens(seed, index)
            assert len(tokens) <= 15
            validate_tokens(tokens)


def test_formula_prefix_unchanged_by_future_mutation_or_append():
    tokens = ["RET", "MEAN3", "VOL", "DELAY1", "SUB"]
    features = {"RET": list(range(20)), "VOL": list(range(20, 40))}
    before = evaluate_formula(tokens, {name: series[:10] for name, series in features.items()})
    assert evaluate_formula(tokens, features)[:10] == before
    mutated = {name: series[:10] + [9999] * 10 for name, series in features.items()}
    assert evaluate_formula(tokens, mutated)[:10] == before


def test_search_step_computes_training_metrics_only_then_finish_freezes_formula(monkeypatch):
    calls = []
    original = mining._simulate

    def train_only(bars, values, start, end, *args, **kwargs):
        calls.append((len(bars), len(values), start, end))
        assert (len(bars), len(values), start, end) == (240, 240, 0, 240)
        return original(bars, values, start, end, *args, **kwargs)

    monkeypatch.setattr(mining, "_simulate", train_only)
    session = complete(MiningSession(chart(), MiningConfig(trials=8)))
    checkpoint = session.checkpoint()
    assert calls
    assert "validation" not in json.dumps(checkpoint["ranked"])
    assert "test" not in json.dumps(checkpoint["ranked"])
    assert all("train_metrics" in entry for entry in checkpoint["ranked"])
    result = session.finish()
    assert result["research_result"]["config"]["factor"] == "FORMULA"
    assert result["selection"]["segment"] == "train"
    assert result["formula"]["tokens"] == checkpoint["best"]["tokens"]
    assert result["research_result"]["thresholds"]["entry"] == checkpoint["best"]["thresholds"]["entry"]
    assert result["research_result"]["segments"]["train"]["metrics"]["total_return"] == checkpoint["best"]["train_metrics"]["total_return"]
    json.dumps(result, allow_nan=False)
    assert session.finish() == result


def test_checkpoint_resume_replays_same_candidate_sequence_and_final_result():
    source = chart()
    config = MiningConfig(trials=16)
    uninterrupted = complete(MiningSession(source, config))
    interrupted = MiningSession(source, config)
    for _ in range(7):
        interrupted.step()
    checkpoint = json.loads(json.dumps(interrupted.checkpoint(), allow_nan=False))
    resumed = complete(MiningSession(source, config, checkpoint))
    assert resumed.checkpoint() == uninterrupted.checkpoint()
    assert resumed.finish() == uninterrupted.finish()
    assert resumed.done
    assert len(resumed.checkpoint()["ranked"]) <= 10


def test_future_mutations_cannot_change_eligibility_ranking_or_training_thresholds():
    source = chart()
    other = copy.deepcopy(source)
    for index, bar in enumerate(other["bars"][240:]):
        for key in ("open", "high", "low", "close"):
            bar[key] *= 5 + index / 3
        bar["volume"] = 100  # Causes held-out VOL denominators to become zero after warmup.
    other["dataset_hash"] = research_dataset_hash(other)
    before = complete(MiningSession(source, MiningConfig(trials=32)))
    after = complete(MiningSession(other, MiningConfig(trials=32)))
    assert before.checkpoint()["ranked"] == after.checkpoint()["ranked"]
    assert before.checkpoint()["rejected"] == after.checkpoint()["rejected"]
    assert before.checkpoint()["training_dataset_hash"] == after.checkpoint()["training_dataset_hash"]
    assert before.finish()["formula"]["tokens"] == after.finish()["formula"]["tokens"]


@pytest.mark.parametrize("mutation", ["checksum", "dataset", "seed", "version"])
def test_checkpoint_mismatch_or_corruption_rejects(mutation):
    source = chart()
    config = MiningConfig(trials=8)
    session = MiningSession(source, config)
    checkpoint = session.step()
    if mutation == "checksum":
        checkpoint["next_index"] += 1
    elif mutation == "dataset":
        source["bars"][5]["volume"] += 1
        source["dataset_hash"] = research_dataset_hash(source)
    elif mutation == "seed":
        config = MiningConfig(trials=8, seed=43)
    else:
        checkpoint["search_version"] = "old-version"
        checkpoint["checksum"] = mining._digest({key: value for key, value in checkpoint.items()
                                                 if key != "checksum"})
    with pytest.raises(ValueError, match="检查点"):
        MiningSession(source, config, checkpoint)


def test_partial_search_never_generates_holdout_artifact():
    session = MiningSession(chart(), MiningConfig(trials=8))
    session.step()
    with pytest.raises(ValueError, match="尚未完成"):
        session.finish()


def test_all_invalid_or_ineligible_candidates_finish_without_fabricated_winner(monkeypatch):
    monkeypatch.setattr(mining, "candidate_tokens", lambda seed, index: ["RET", "RET", "SUB"])
    session = complete(MiningSession(chart(), MiningConfig(trials=8)))
    assert session.checkpoint()["best"] is None
    assert session.checkpoint()["rejected"] == 8
    with pytest.raises(ValueError, match="没有满足"):
        session.finish()


def test_shared_formula_research_path_still_validates_closed_ohlcv_inputs():
    source = chart()
    source["bars"][-1]["closed"] = False
    source["dataset_hash"] = research_dataset_hash(source)
    with pytest.raises(ValueError, match="仅接受已收盘"):
        run_research(source, ResearchConfig(), values=list(range(400)), factor_label="FORMULA")


@pytest.mark.parametrize("changes", [
    {"seed": -1}, {"seed": 2**31}, {"seed": True}, {"trials": 7}, {"trials": 129},
    {"max_seconds": 9}, {"max_seconds": 121}, {"min_trades": 0}, {"min_trades": 21},
    {"unexpected": 1},
])
def test_mining_limits_are_validated(changes):
    with pytest.raises(ValidationError):
        MiningConfig(**changes)
