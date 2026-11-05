"""Bounded, account-scoped Birdeye OHLCV access for immutable PA snapshots.

The v1 endpoint returns USD OHLC prices. We preserve its volume as reported:
no liquidity, FDV, missing candles, or historical values are synthesized.
"""
from __future__ import annotations

import math
import time
from email.utils import parsedate_to_datetime

import httpx

from pa_agent.data.base import KlineBar
from pa_agent.data.snapshot import take_snapshot_from_bars

ENDPOINT = "https://public-api.birdeye.so/defi/ohlcv"
WRAPPED_SOL = "So11111111111111111111111111111111111111112"
TIMEFRAMES = {
    "1m": ("1m", 60), "5m": ("5m", 300), "15m": ("15m", 900),
    "30m": ("30m", 1800), "1h": ("1H", 3600), "4h": ("4H", 14400),
    "1d": ("1D", 86400),
}
_BASE58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
_WINDOW_BARS = 200  # Well below the endpoint's 1,000-item response ceiling.
_MAX_ATTEMPTS = 3
_MAX_SECONDS = 60
_INVALID = "Birdeye 返回了无效或不完整的 OHLCV 数据。"


class BirdeyeError(ValueError):
    """A fixed, user-actionable error that never contains an upstream body."""


def valid_solana_address(value: str) -> bool:
    """A mint identity must encode exactly 32 bytes; labels are not identities."""
    if not 32 <= len(value) <= 44 or any(c not in _BASE58 for c in value):
        return False
    number = 0
    for char in value:
        number = number * 58 + _BASE58.index(char)
    leading_zeroes = len(value) - len(value.lstrip("1"))
    return leading_zeroes + (number.bit_length() + 7) // 8 == 32


def _retry_delay(value: str | None, attempt: int) -> float:
    delay = 0.5 * 2 ** attempt
    if value:
        try:
            delay = float(value)
        except ValueError:
            try:
                delay = parsedate_to_datetime(value).timestamp() - time.time()
            except (ValueError, TypeError, OverflowError):
                pass
    return min(2.0, max(0.0, delay)) if math.isfinite(delay) else 2.0


def _items(client, params, deadline):
    for attempt in range(_MAX_ATTEMPTS):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise BirdeyeError("Birdeye 请求超时，请稍后重试。")
        try:
            response = client.get(ENDPOINT, params=params,
                                  timeout=httpx.Timeout(min(15, remaining),
                                                       connect=min(5, remaining)))
        except httpx.HTTPError:
            raise BirdeyeError("无法连接 Birdeye，请稍后重试。") from None
        if response.status_code == 429:
            if attempt + 1 == _MAX_ATTEMPTS:
                raise BirdeyeError("Birdeye 请求次数受限，请稍后重试。")
            delay = _retry_delay(response.headers.get("retry-after"), attempt)
            if time.monotonic() + delay >= deadline:
                raise BirdeyeError("Birdeye 请求超时，请稍后重试。")
            time.sleep(delay)
            continue
        if response.status_code in (401, 403):
            raise BirdeyeError("Birdeye 认证或套餐权限不足，请检查个人 API Key 和 OHLCV 权限。")
        if response.status_code != 200:
            raise BirdeyeError("Birdeye 暂时无法提供行情，请稍后重试。")
        try:
            payload = response.json()
            data = payload["data"]
            items = data["items"]
            if (payload.get("success") is not True or not isinstance(items, list)
                    or data.get("hasMore") or data.get("has_more")
                    or data.get("address", params["address"]) != params["address"]):
                raise BirdeyeError(_INVALID)
        except (ValueError, KeyError, TypeError, AttributeError):
            raise BirdeyeError(_INVALID) from None
        return items
    raise BirdeyeError("Birdeye 请求次数受限，请稍后重试。")


def _number(value):
    if isinstance(value, bool):
        raise BirdeyeError(_INVALID)
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        raise BirdeyeError(_INVALID) from None
    if not math.isfinite(number):
        raise BirdeyeError(_INVALID)
    return number


def fetch_birdeye_frame(data, settings):
    """Fetch at most 560 intervals in at most three non-overlapping windows."""
    key = settings.birdeye_api_key
    if not key:
        raise BirdeyeError("请在个人设置中填写 Birdeye API Key。")
    if data.timeframe not in TIMEFRAMES:
        raise BirdeyeError("Birdeye 暂不支持所选周期。")
    api_type, step = TIMEFRAMES[data.timeframe]
    now = int(time.time())
    closed_end = now // step * step
    end = closed_end
    remaining_bars = data.count + 60
    deadline = time.monotonic() + _MAX_SECONDS
    bars = {}
    # trust_env deliberately preserves the managed environment's proxy and CA.
    with httpx.Client(headers={"X-API-KEY": key, "x-chain": data.chain,
                               "accept": "application/json"},
                      trust_env=True, follow_redirects=False) as client:
        while remaining_bars:
            window = min(_WINDOW_BARS, remaining_bars)
            start = end - window * step
            items = _items(client, {"address": data.instrument_id, "type": api_type,
                                    "time_from": start, "time_to": end - 1}, deadline)
            if len(items) > window + 1:
                raise BirdeyeError(_INVALID)
            for item in items:
                try:
                    ts = _number(item["unixTime"])
                    if ts != int(ts) or int(ts) % step:
                        raise BirdeyeError(_INVALID)
                    if ts == closed_end:  # Never promote the currently forming candle.
                        continue
                    if not start <= ts < end or ts in bars:
                        raise BirdeyeError(_INVALID)
                    values = [_number(item[k]) for k in ("o", "h", "l", "c", "v")]
                    opening, high, low, close, volume = values
                    if not 0 < low <= min(opening, close) <= max(opening, close) <= high or volume < 0:
                        raise BirdeyeError(_INVALID)
                    bars[ts] = KlineBar(0, ts * 1000, *values, closed=True)
                except (KeyError, TypeError, ValueError):
                    raise BirdeyeError(_INVALID) from None
            end = start
            remaining_bars -= window
    if len(bars) < data.count:
        raise BirdeyeError(f"Birdeye 仅返回 {len(bars)} 根已收盘 K 线，需要至少 {data.count} 根；缺失区间不会填充。")
    newest = [bars[ts] for ts in sorted(bars, reverse=True)]
    return take_snapshot_from_bars(newest, data.count, data.symbol, data.timeframe,
                                   now_ms=now * 1000)
