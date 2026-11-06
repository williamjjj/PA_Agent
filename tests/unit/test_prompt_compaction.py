from pa_agent.ai.prompt_assembler import PromptAssembler


def test_both_3000_bar_tables_preserve_every_cell(monkeypatch):
    from tests.unit.test_prompt_assembler import _make_frame
    frame = _make_frame(3000)
    for render in (PromptAssembler._render_kline_table, PromptAssembler._render_kline_feature_table):
        compact = render(frame)
        with monkeypatch.context() as ctx:
            ctx.setattr(PromptAssembler, "_compact_table", staticmethod(lambda lines: "\n".join(lines)))
            original = render(frame)
        old_rows = [[v.strip() for v in line.split("|")] for line in original.splitlines() if "|" in line]
        new_rows = [line.split("\t") for line in compact.splitlines() if "\t" in line]
        assert old_rows == new_rows and len(new_rows) == 3001
        assert len(compact) < len(original) * .8
