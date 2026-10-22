# AlphaGPT factor attribution

`pa_agent/web/quant.py` adapts the six basic mathematical factor definitions in
[`williamjjj/AlphaGPT`](https://github.com/williamjjj/AlphaGPT),
`model_core/factors.py`, revision `d851f2221dcaf4d53a707344f68ae6801e3e5af5`.
The original project is distributed under the Apache License, Version 2.0;
its complete license is preserved in [LICENSE](LICENSE). The inspected upstream
revision contains no separate NOTICE file and no specific copyright notice in
the adapted source file.

This adaptation replaces the PyTorch implementation with local Python math;
removes circular time shifts, zero-padded averages and future-dependent
whole-series normalization; validates closed OHLCV snapshots; marks missing
historical liquidity rather than filling it; and emits versioned, causal
factor evidence. It does not import or package the AlphaGPT training engine,
trading runner, existing trained formulas, or credentials.

See [the factor specification](../../docs/ALPHAGPT_FACTORS.md) for the changed
numerical contract. The containing PA Agent project retains its own license.

The stage-three named-token RPN design also draws on upstream
`model_core/vocab.py`, `model_core/vm.py` and `model_core/ops.py` at the same
revision. `pa_agent/web/formula.py` is a bounded Python implementation with
explicit null warm-up, protected division, trailing-only windows and a new
versioned vocabulary. Its DECAY weighting is separately specified; whole-series
JUMP, zero padding and old integer-token formulas are not reused. The bounded
deterministic CPU search in `mining.py` does not use upstream Transformer weights
or execute the trading runner. See [the mining contract](../../docs/ALPHAGPT_MINING.md).
