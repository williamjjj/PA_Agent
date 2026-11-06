"""Render the complete candle history with bounded, reusable Qt objects."""
from __future__ import annotations

from typing import TYPE_CHECKING

import pyqtgraph as pg
from PyQt6.QtCore import QRectF
from PyQt6.QtGui import QPainter, QPicture
from PyQt6.QtWidgets import QGraphicsItem

from pa_agent.gui.widgets.candle_item import CandleItem

if TYPE_CHECKING:
    from pa_agent.data.base import KlineBar


class CandleSeriesItem(pg.GraphicsObject):
    """Keep all bars; rebuild only changed picture blocks and paint visible blocks."""

    BLOCK_SIZE = 128

    def __init__(self):
        super().__init__()
        self.bars: tuple[KlineBar, ...] = ()
        self._blocks: list[tuple[tuple, QPicture, int, int]] = []
        self._bounds = QRectF()
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemUsesExtendedStyleOption, True)

    def set_bars(self, bars) -> None:
        self.bars = tuple(bars)
        ascending = self.bars[::-1]
        blocks = []
        for start in range(0, len(ascending), self.BLOCK_SIZE):
            group = ascending[start:start + self.BLOCK_SIZE]
            signature = tuple((b.open, b.high, b.low, b.close, b.closed) for b in group)
            index = start // self.BLOCK_SIZE
            old = self._blocks[index] if index < len(self._blocks) else None
            if old is not None and old[0] == signature:
                blocks.append(old)
                continue
            picture = QPicture()
            painter = QPainter(picture)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
            for offset, bar in enumerate(group):
                draw = CandleItem._paint_closed if bar.closed else CandleItem._paint_forming
                draw(painter, bar, float(start + offset))
            painter.end()
            blocks.append((signature, picture, start, start + len(group)))
        self._blocks = blocks
        if ascending:
            # Include doji minimum body height, also for very small-price assets.
            low = min(min(b.low, CandleItem._body_bounds(b)[1]) for b in ascending)
            high = max(max(b.high, CandleItem._body_bounds(b)[0]) for b in ascending)
            margin = (high - low) * .05 + 1e-8
            bounds = QRectF(-.5, low - margin, len(ascending), high - low + 2 * margin)
        else:
            bounds = QRectF()
        if bounds != self._bounds:
            self.prepareGeometryChange()
            self._bounds = bounds
        self.update()

    def boundingRect(self) -> QRectF:
        return self._bounds

    def paint(self, painter, option, widget=None) -> None:
        exposed = option.exposedRect
        for _, picture, start, end in self._blocks:
            if end < exposed.left() or start > exposed.right():
                continue
            painter.drawPicture(0, 0, picture)
