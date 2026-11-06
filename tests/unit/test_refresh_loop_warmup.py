"""RefreshLoop must fetch enough bars for indicator warmup."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from pa_agent.data.refresh_loop import RefreshLoop
from pa_agent.data.snapshot import INDICATOR_WARMUP_BARS
from pa_agent.util.threading import CancelToken


@pytest.mark.parametrize("cancellable", [False, True])
def test_refresh_loop_snapshot_count_includes_indicator_warmup(qapp, cancellable):
    token = CancelToken()

    def fetch(*args):
        token.set()
        return []

    source = SimpleNamespace(latest_snapshot=Mock(side_effect=fetch))
    if cancellable:
        source.latest_snapshot_cancellable = Mock(side_effect=fetch)
    loop = RefreshLoop(data_source=source, n_bars=3000, interval_ms=1500, cancel_token=token)
    loop.run()
    count = 3000 + INDICATOR_WARMUP_BARS + 5
    if cancellable:
        source.latest_snapshot_cancellable.assert_called_once_with(count, token)
        source.latest_snapshot.assert_not_called()
    else:
        source.latest_snapshot.assert_called_once_with(count)
