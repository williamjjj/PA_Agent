"""Price rounding that retains useful digits for assets quoted below one unit."""
from __future__ import annotations

import math
from decimal import Decimal


def round_price(value: float, places: int = 6) -> float:
    """Keep ordinary rounding, but retain 12 significant digits below one unit.

    A fixed number of decimal places can turn a valid token price or indicator
    into zero. Twelve significant digits also removes normal floating-point
    arithmetic noise without discarding the scale of a small quote.
    """
    if not math.isfinite(value) or value == 0:
        return value
    if abs(value) < 1:
        places = max(places, 11 - Decimal(str(abs(value))).adjusted())
    return round(value, places)


def price_text(value: float, places: int = 6) -> str:
    """Format a rounded price as a decimal string, never an exponent."""
    rounded = round_price(value, places)
    if rounded == 0:
        return "0"
    text = format(Decimal(str(rounded)), "f")
    return text.rstrip("0").rstrip(".") if "." in text else text
