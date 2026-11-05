import sys
import time

import httpx
import pytest

from pa_agent.config.settings import Settings, load_settings, save_settings
from pa_agent.data.configured_source import ConfiguredSource, parse_market_rows
from pa_agent.data.connections import ConnectionError, import_mcp_config, mcp_request, request_json
from pa_agent.data.factory import create_data_source, data_source_choices
from pa_agent.data.source_config import SourceProfile, source_templates


@pytest.fixture
def csv_profile(tmp_path):
    from pa_agent.quant.local import demo_dataset

    chart = demo_dataset()
    path = tmp_path / "中文行情.csv"
    rows = ["time,open,high,low,close,volume"]
    rows += [
        ",".join(str(bar[key]) for key in ("time", "open", "high", "low", "close", "volume"))
        for bar in chart["bars"]
    ]
    path.write_text("\n".join(rows), encoding="utf-8-sig")
    return SourceProfile(name="模拟 CSV", transport="csv", csv_path=str(path), symbol="BTCUSDT")


def test_custom_and_yahoo_survive_restart_without_gold_rewrite(tmp_path, csv_profile):
    settings = Settings(provider={"backend": "codex", "api_key": "preserve-this"})
    settings.data_sources.profiles.append(csv_profile)
    settings.general.last_data_source = csv_profile.id
    settings.general.last_symbol = "BTCUSDT"
    path = tmp_path / "settings.json"
    save_settings(settings, path)
    loaded = load_settings(path)
    assert loaded.general.last_symbol == "BTCUSDT"
    assert loaded.provider.api_key == "preserve-this"
    assert (csv_profile.id, "模拟 CSV") in data_source_choices(settings=loaded)
    source = create_data_source(csv_profile.id, loaded)
    source.connect()
    source.subscribe("BTCUSDT", "1h")
    bars = source.latest_snapshot(600)
    assert len(bars) == 600 and all(b.closed for b in bars)
    assert bars[0].ts_open > bars[-1].ts_open
    assert bars[0].seq == 1 and bars[-1].seq == 600
    loaded.general.last_data_source = "yfinance"
    loaded.general.last_symbol = "BTC-USD"
    save_settings(loaded, path)
    assert load_settings(path).general.last_data_source == "yfinance"
    assert load_settings(path).general.last_symbol == "BTC-USD"


def test_unknown_custom_is_an_error():
    with pytest.raises(ValueError, match="已删除"):
        create_data_source("custom:missing", Settings())


def test_array_mapping_forming_flags_and_duplicate_rejection():
    p = source_templates()["OKX · 现货 / 合约行情"]
    now = int(time.time()) // 3600 * 3600
    rows = [
        [str(t * 1000), "10", "12", "9", "11", "100", "0", "0", flag]
        for t, flag in ((now, "0"), (now - 3600, "1"))
    ]
    bars = parse_market_rows(rows, p, "1h")
    assert not bars[0].closed and bars[1].closed
    with pytest.raises(ValueError):
        # Invalid profile validation should never silently normalize data.
        SourceProfile(name="bad", timeframe="banana")
    from pa_agent.data.base import DataSourceError

    with pytest.raises(DataSourceError, match="第 2 行"):
        parse_market_rows([rows[0], rows[0]], p, "1h")
    rows[1][2] = "nan"
    with pytest.raises(DataSourceError):
        parse_market_rows(rows, p, "1h")


def test_http_encodes_parameters_and_hides_auth_errors(monkeypatch):
    real_client = httpx.Client
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={"items": []})

    monkeypatch.setattr(
        httpx, "Client", lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw)
    )
    profile = SourceProfile(
        name="资讯",
        category="information",
        url="https://example.test/news?q={query}",
        headers={"Authorization": "secret-token"},
    )
    assert request_json(profile, {"query": "黄金&limit=999"}) == {"items": []}
    assert requests[0].url.params["q"] == "黄金&limit=999"
    assert "limit" not in requests[0].url.params
    monkeypatch.setattr(
        httpx,
        "Client",
        lambda **kw: real_client(
            transport=httpx.MockTransport(lambda r: httpx.Response(401)), **kw
        ),
    )
    with pytest.raises(ConnectionError) as error:
        request_json(profile, {"query": "test"})
    assert "401" in str(error.value) and "secret-token" not in str(error.value)


def test_import_mcp_preserves_env_references(tmp_path):
    config = tmp_path / "config.toml"
    config.write_text(
        '[mcp_servers.mx]\nurl="https://mxapi.eastmoney.com/mxds/mcp"\nenv_http_headers={em_api_key="TEST_MX_KEY"}\n',
        encoding="utf-8",
    )
    [profile] = import_mcp_config(config)
    assert profile.env_headers == {"em_api_key": "TEST_MX_KEY"}
    assert profile.headers == {} and profile.mcp_tool == "mx_finance_search_news"


def test_real_stdio_mcp_discovery_and_call(tmp_path):
    server = tmp_path / "mcp_server.py"
    server.write_text(
        """from mcp.server.fastmcp import FastMCP
mcp = FastMCP("test")
@mcp.tool()
def search_news(query: str, limit: int = 5) -> dict:
    return {"query": query, "limit": limit, "items": [{"title": "模拟资讯", "url": "https://example.test/news"}]}
if __name__ == "__main__":
    mcp.run(transport="stdio")
""",
        encoding="utf-8",
    )
    p = SourceProfile(
        name="测试 MCP",
        category="information",
        transport="mcp_stdio",
        command=sys.executable,
        args=[str(server)],
        mcp_tool="search_news",
        mcp_arguments={"query": "{query}", "limit": "{limit}"},
    )
    tools = mcp_request(p, discover=True)
    assert tools[0]["name"] == "search_news"
    data = mcp_request(p, variables={"query": "黄金", "limit": "3"})
    assert data["query"] == "黄金" and data["limit"] == 3
    p.mcp_arguments = {"invalid": True}
    with pytest.raises(ConnectionError, match="参数"):
        mcp_request(p)


def test_cache_prevents_each_chart_tick_from_requesting_api(monkeypatch):
    p = SourceProfile(name="行情", url="https://example.test/bars")
    calls = []
    rows = [{"time": 1700000000, "open": 10, "high": 12, "low": 9, "close": 11, "volume": 100}]
    monkeypatch.setattr(
        "pa_agent.data.configured_source.request_json", lambda *args: calls.append(True) or rows
    )
    source = ConfiguredSource(p)
    source.connect()
    source.subscribe(p.symbol, p.timeframe)
    source.latest_snapshot(100)
    source.latest_snapshot(100)
    assert len(calls) == 1
    source.subscribe("ETHUSDT", "1h")
    source.latest_snapshot(100)
    assert len(calls) == 2


def test_information_context_is_bounded_and_bound_to_symbol():
    from pa_agent.data.information import add_information_context, selected_context

    items = [{"symbol": "BTCUSDT", "source": "Test", "content": "x" * 16000} for _ in range(3)]
    items.append({"symbol": "OTHER", "content": "not relevant"})
    selected = selected_context(items, "BTCUSDT")
    assert sum(len(item["content"]) for item in selected) == 24000
    original = [{"role": "user", "content": "analyze"}]
    messages = add_information_context(original, selected)
    assert "不具备指令权限" in messages[0]["content"]
    assert "not relevant" not in messages[0]["content"]
    assert original[0]["content"] == "analyze"


def test_information_reader_formats_miaoxiang_rows_and_escapes_markup():
    from pa_agent.gui.information_dialog import render_information
    snapshot = {"source":"妙想", "query":"黄金", "captured_at":"2026-11-05T00:00:00Z",
                "content": __import__("json").dumps({"data":[{"columns":["标题","摘要","跳转链接"],
                    "items":[["<script>example</script>","新闻正文","https://example.test/news"]]}]})}
    html = render_information(snapshot)
    assert "新闻正文" in html and 'href="https://example.test/news"' in html
    assert "<script>" not in html and "&lt;script&gt;" in html


def test_editor_save_and_manager_cancel_do_not_mutate_settings(qtbot, csv_profile):
    from pa_agent.gui.data_sources_dialog import DataSourcesDialog, SourceEditor

    settings = Settings()
    dialog = DataSourcesDialog(settings)
    qtbot.addWidget(dialog)
    dialog._draft.data_sources.profiles.append(csv_profile)
    dialog.reject()
    assert settings.data_sources.profiles == []
    editor = SourceEditor(csv_profile)
    qtbot.addWidget(editor)
    editor._name.setText("我的行情")
    editor._save()
    assert editor.profile.name == "我的行情" and editor.profile.id == csv_profile.id
    assert csv_profile.name == "模拟 CSV"


def test_manager_saves_sources_without_changing_provider(qtbot, tmp_path, monkeypatch, csv_profile):
    from PyQt6.QtCore import Qt
    from pa_agent.gui import data_sources_dialog as ui

    path = tmp_path / "saved.json"
    monkeypatch.setattr(ui, "save_settings", lambda settings: save_settings(settings, path))
    settings = Settings(provider={"backend": "codex", "harness_model": "preserve"})
    dialog = ui.DataSourcesDialog(settings)
    qtbot.addWidget(dialog)
    dialog._draft.data_sources.profiles.append(csv_profile)
    dialog._populate()
    dialog._list.item(2).setCheckState(Qt.CheckState.Checked)
    dialog._save()
    saved = load_settings(path)
    assert "ashare" in saved.data_sources.enabled_builtins
    assert saved.data_sources.profiles[0].id == csv_profile.id
    assert saved.provider.harness_model == "preserve"


def test_main_window_selects_custom_feed_and_rolls_back_failed_connection(
    qtbot, monkeypatch, csv_profile
):
    from pa_agent.app_context import AppContext
    from pa_agent.gui.main_window import MainWindow
    from pa_agent.data import factory

    settings = Settings()
    settings.data_sources.profiles.append(csv_profile)
    settings.general.last_data_source = csv_profile.id
    settings.general.last_symbol = csv_profile.symbol
    settings.general.last_timeframe = csv_profile.timeframe
    source = ConfiguredSource(csv_profile)
    source.connect()
    source.subscribe(csv_profile.symbol, "1h")
    window = MainWindow(AppContext(settings=settings, data_source=source))
    qtbot.addWidget(window)
    assert window._data_source_combo.currentData() == csv_profile.id
    assert window._symbol_combo.currentText() == "BTCUSDT"
    from pa_agent.data.base import DataSourceTransientError

    def fail(*_):
        raise DataSourceTransientError("missing token")

    monkeypatch.setattr(factory, "create_data_source", fail)
    with pytest.raises(DataSourceTransientError):
        window._switch_data_source("tushare")
    assert window._ctx.data_source is source and source._connected
    assert window._current_data_source_kind() == csv_profile.id
    assert window._symbol_combo.currentText() == "BTCUSDT"
