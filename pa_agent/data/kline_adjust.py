"""Request-local A-share adjustment preference."""
from contextvars import ContextVar
from typing import Literal

KlineAdjust = Literal["qfq", "hfq", "none"]
_current: ContextVar[KlineAdjust] = ContextVar("kline_adjust", default="qfq")

def set_kline_adjust(adjust: str | None) -> None:
    key = str(adjust or "qfq").strip().lower()
    _current.set(key if key in ("qfq", "hfq", "none") else "qfq")

def get_kline_adjust() -> KlineAdjust:
    return _current.get()

def apply_kline_adjust_from_settings(settings: object | None) -> None:
    set_kline_adjust(getattr(getattr(settings, "general", settings), "kline_adjust", "qfq"))
