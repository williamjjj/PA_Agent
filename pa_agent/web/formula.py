"""Named-token RPN formulas with bounded structure and causal time operators."""
from __future__ import annotations

import hashlib
import math
import random

VOCAB_VERSION = "pa-causal-rpn-v1"
FEATURES = ("RET", "PRESSURE", "FOMO", "DEV", "VOL")
BINARY = ("ADD", "SUB", "MUL", "DIV")
UNARY = ("NEG", "ABS", "SIGN", "DELAY1", "MEAN3", "DECAY", "MAX3")
MAX_TOKENS, MAX_DEPTH = 15, 3


def validate_tokens(tokens: list[str]) -> str:
    """Validate the complete program and return a display-only expression."""
    if not isinstance(tokens, list) or not 1 <= len(tokens) <= MAX_TOKENS:
        raise ValueError("公式须包含 1 至 15 个命名 token。")
    stack: list[tuple[int, str]] = []
    for token in tokens:
        if not isinstance(token, str) or token not in FEATURES + BINARY + UNARY:
            raise ValueError("公式只接受当前词表中的命名 token，不接受未知或旧整数公式。")
        if token in FEATURES:
            stack.append((0, token))
            continue
        arity = 2 if token in BINARY else 1
        if len(stack) < arity:
            raise ValueError("公式操作数不足。")
        arguments = stack[-arity:]
        del stack[-arity:]
        depth = 1 + max(item[0] for item in arguments)
        if depth > MAX_DEPTH:
            raise ValueError("公式运算深度最多为 3。")
        expression = f"{token}({', '.join(item[1] for item in arguments)})"
        stack.append((depth, expression))
    if len(stack) != 1:
        raise ValueError("公式必须恰好产生一个结果。")
    return stack[0][1]


def candidate_tokens(seed: int, index: int) -> list[str]:
    """Candidate i is independent of prior trials, outcomes and global RNG state."""
    if not isinstance(index, int) or index < 0:
        raise ValueError("候选公式索引无效。")
    if index < len(FEATURES):
        return [FEATURES[index]]
    digest = hashlib.sha256(f"{VOCAB_VERSION}:{seed}:{index}".encode()).digest()
    rng = random.Random(int.from_bytes(digest, "big"))

    def expression(depth):
        if depth == 0 or rng.random() < 0.25:
            return [rng.choice(FEATURES)]
        operator = rng.choice(BINARY + UNARY)
        arguments = expression(depth - 1)
        if operator in BINARY:
            arguments += expression(depth - 1)
        return arguments + [operator]

    return expression(MAX_DEPTH)


def _finite(value):
    return value if math.isfinite(value) else None


def evaluate_formula(tokens: list[str], features: dict[str, list[float | None]]) -> list[float | None]:
    """Invalid arithmetic and incomplete trailing windows produce null, never zero."""
    validate_tokens(tokens)
    needed = {token for token in tokens if token in FEATURES}
    if not needed <= features.keys() or len({len(features[name]) for name in needed}) != 1:
        raise ValueError("公式特征不存在或时间长度不一致。")
    if any(value is not None and (not isinstance(value, (int, float)) or
                                  isinstance(value, bool) or not math.isfinite(value))
           for name in needed for value in features[name]):
        raise ValueError("公式输入只接受有限特征或空值。")
    stack: list[list[float | None]] = []
    for token in tokens:
        if token in FEATURES:
            stack.append(list(features[token]))
            continue
        right = stack.pop()
        if token in BINARY:
            left = stack.pop()
            output = []
            for a, b in zip(left, right, strict=True):
                if a is None or b is None or token == "DIV" and abs(b) <= 1e-12:
                    output.append(None)
                elif token == "ADD":
                    output.append(_finite(a + b))
                elif token == "SUB":
                    output.append(_finite(a - b))
                elif token == "MUL":
                    output.append(_finite(a * b))
                else:
                    output.append(_finite(a / b))
        elif token == "DELAY1":
            output = [None] + right[:-1] if right else []
        elif token in ("MEAN3", "DECAY", "MAX3"):
            output = []
            for index in range(len(right)):
                window = right[max(0, index - 2):index + 1]
                if len(window) < 3 or any(value is None for value in window):
                    output.append(None)
                elif token == "MEAN3":
                    output.append(_finite(sum(value / 3 for value in window)))
                elif token == "DECAY":
                    output.append(_finite(sum(value * ((i + 1) / 6)
                                              for i, value in enumerate(window))))
                else:
                    output.append(max(window))
        else:
            output = [None if value is None else -value if token == "NEG"
                      else abs(value) if token == "ABS" else float((value > 0) - (value < 0))
                      for value in right]
        stack.append(output)
    return stack[0]
