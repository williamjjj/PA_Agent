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
