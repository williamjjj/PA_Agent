from datetime import datetime, timezone
from types import SimpleNamespace

import pandas as pd
import pytest

from pa_agent.data.tradingview import _row_ts_ms


@pytest.mark.parametrize("epoch", [1704067200, 1719871200, 1793987400])
def test_tvdatafeed_host_local_datetime_roundtrip(epoch):
    # The upstream parser uses datetime.fromtimestamp, without UTC tzinfo.
    for value in (datetime.fromtimestamp(epoch), pd.Timestamp(datetime.fromtimestamp(epoch)),
                  datetime.fromtimestamp(epoch, timezone.utc)):
        assert _row_ts_ms(SimpleNamespace(datetime=value)) == epoch * 1000
