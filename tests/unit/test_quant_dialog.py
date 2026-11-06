def test_demo_factors_research_and_saved_result_render(qtbot):
    from pa_agent.gui.quant_dialog import QuantDialog
    from pa_agent.quant.local import demo_dataset
    from pa_agent.quant.snapshots import factor_evidence
    from pa_agent.quant.research import run_research,ResearchConfig
    dialog=QuantDialog(); qtbot.addWidget(dialog)
    chart=demo_dataset(); dialog._set_chart(chart)
    dialog._show_result({'kind':'factors','dataset':chart,'result':factor_evidence(chart)})
    assert dialog._table.rowCount()==6
    result=run_research(chart,ResearchConfig())
    dialog._show_result({'kind':'research','dataset':chart,'result':result})
    assert dialog._table.rowCount()==3
    assert '样本外' in dialog._notes.toPlainText()


def test_main_chart_is_reusable_for_stock_research_without_csv(qtbot, tmp_path):
    from pa_agent.gui.quant_dialog import QuantDialog
    from pa_agent.quant.local import run_job
    from pa_agent.quant.sources import restore_frame
    from tests.quant.test_multi_market import dataset
    frame = restore_frame(dataset("stock", "1h"))
    dialog = QuantDialog(frame, "tradingview", exchange="NASDAQ",
                         snapshot_provider=lambda: (frame,"tradingview","NASDAQ"))
    qtbot.addWidget(dialog)
    assert dialog._market.currentData() == "stock"
    assert len(dialog._chart["bars"]) == 300
    assert dialog._position_mode.currentData() == "long_only"
    assert dialog._quantity_step.value() == 1
    result = run_job("research", dialog._chart, {}, tmp_path)
    dialog._show_result(result)
    assert result["result"]["market_kind"] == "stock"
    assert dialog._table.rowCount() == 3
