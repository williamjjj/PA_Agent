"""Coalesce tiny network deltas before they enter Qt's queued signal backlog."""
from __future__ import annotations

import time
from collections.abc import Callable


class StreamBatcher:
    """Used on ONE producer thread; flush on boundaries and before result signals."""
    def __init__(self, emit: Callable[[str, str, str], None], interval: float = 0.05,
                 max_chars: int = 4096) -> None:
        self.emit = emit
        self.interval = interval
        self.max_chars = max_chars
        self._key: tuple[str, str] | None = None
        self._parts: list[str] = []
        self._size = 0
        self._last_flush = time.monotonic()

    def add(self, stage: str, kind: str, text: str) -> None:
        if not text:
            return
        key = (stage, kind)
        if self._key != key:
            self.flush()
        self._key = key
        self._parts.append(text)
        self._size += len(text)
        if self._size >= self.max_chars or time.monotonic() - self._last_flush >= self.interval:
            self.flush()

    def flush(self) -> None:
        if self._parts and self._key is not None:
            self.emit(*self._key, "".join(self._parts))
        self._parts.clear()
        self._size = 0
        self._last_flush = time.monotonic()
