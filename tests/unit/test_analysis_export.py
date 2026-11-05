import copy
import hashlib
import json
import zipfile

import pytest

from pa_agent.records.exporter import (
    SECTIONS,
    build_export,
    default_filename,
    read_record,
    report_html,
    write_export,
)


@pytest.fixture
def export_record():
    from pa_agent.orchestrator.two_stage import _build_empty_record
    from pa_agent.data.snapshot import take_snapshot_from_bars
    from pa_agent.config.settings import Settings
    from tests.fixtures.kline_bars import make_newest_first_bars
    from tests.fixtures.ai_payloads import VALID_STAGE1, VALID_STAGE2_ORDER

    frame = take_snapshot_from_bars(make_newest_first_bars(80, with_forming=True), 20, "DEMO", "1h")
    record = _build_empty_record(frame, Settings(provider={"backend": "codex"})).model_dump()
    record.update(
        stage1_diagnosis=copy.deepcopy(VALID_STAGE1),
        stage2_decision=copy.deepcopy(VALID_STAGE2_ORDER),
        stage1_messages=[{"role": "user", "content": "stage1 input private-key-1234"}],
        stage2_messages=[{"role": "user", "content": "stage2 input"}],
        stage1_response={
            "content": "诊断原文",
            "reasoning_content": "模型返回过程",
            "authorization": "another-secret",
        },
        stage2_response={"content": "决策原文"},
        information_context=[{"source": "来源", "content": "资讯内容"}],
    )
    return record


def test_full_zip_preserves_all_sections_and_checksums(tmp_path, export_record):
    payload = build_export(
        export_record,
        followups=[{"turn": 1, "user": "后续问题", "ai_content": "后续回答"}],
        secrets=["private-key-1234"],
    )
    path = tmp_path / "中文分析.zip"
    result = write_export(path, payload)
    assert result["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    with zipfile.ZipFile(path) as z:
        assert z.testzip() is None
        assert {
            "分析报告.html",
            "分析报告.md",
            "导出内容.json",
            "输入_K线.csv",
            "输入_K线与指标.csv",
        } <= set(z.namelist())
        saved = json.loads(z.read("导出内容.json"))
        assert set(saved["sections"]) == set(SECTIONS)
        assert saved["sections"]["followups"][0]["ai_content"] == "后续回答"
        assert saved["sections"]["stage2"] == export_record["stage2_decision"]
        assert z.read("模型返回/stage1_过程文本.txt").decode() == "模型返回过程"
        for name in z.namelist():
            assert b"private-key-1234" not in z.read(name)
            assert b"another-secret" not in z.read(name)
        manifest = json.loads(z.read("manifest.json"))
        for file in manifest["files"]:
            assert hashlib.sha256(z.read(file["name"])).hexdigest() == file["sha256"]
        lines = z.read("输入_K线.csv").decode("utf-8-sig").splitlines()
        assert lines[0] == "time,open,high,low,close,volume"
        assert float(lines[1].split(",")[0]) < float(lines[-1].split(",")[0])


@pytest.mark.parametrize("suffix", [".json", ".html", ".md", ".zip"])
def test_selected_sections_do_not_leak_unselected_details(tmp_path, export_record, suffix):
    payload = build_export(export_record, sections=["summary"])
    path = tmp_path / ("summary" + suffix)
    write_export(path, payload)
    content = (
        path.read_text(encoding="utf-8")
        if suffix != ".zip"
        else zipfile.ZipFile(path).read("导出内容.json").decode()
    )
    assert "stage1 input" not in content and "资讯内容" not in content
    assert "private-key" not in content


def test_partial_record_and_followup_sidecar(tmp_path, export_record):
    export_record.update(stage2_decision=None, exception=None, _partial_reason="user_cancelled")
    record_path = tmp_path / "analysis.json"
    record_path.write_text(json.dumps(export_record), encoding="utf-8")
    record_path.with_suffix(".followups.jsonl").write_text(
        '{"user":"问题","ai_content":"回答"}\nbroken\n', encoding="utf-8"
    )
    record, followups, warnings = read_record(record_path)
    payload = build_export(record, followups=followups, warnings=warnings)
    assert payload["status"] == "已取消"
    assert payload["sections"]["stage2"] is None
    assert payload["sections"]["stage1"]
    assert len(followups) == 1 and "第 2 行" in warnings[0]


def test_export_does_not_overwrite_without_permission(tmp_path, export_record):
    path = tmp_path / "analysis.json"
    path.write_text("keep", encoding="utf-8")
    with pytest.raises(FileExistsError):
        write_export(path, build_export(export_record))
    assert path.read_text() == "keep"
    assert not list(tmp_path.glob(".pa-export-*"))


def test_html_escapes_external_markup_and_filename_is_safe(export_record):
    export_record["information_context"] = [{"content": "<script>alert('x')</script>"}]
    html = report_html(build_export(export_record))
    assert "<script>alert" not in html and "&lt;script&gt;" in html
    export_record["meta"]["symbol"] = "../BTC/USDT:*?"
    assert not any(c in default_filename(export_record) for c in ("/", "\\", ":", "*", "?"))


def test_dialog_exports_current_record_and_reads_followups(
    qtbot, monkeypatch, tmp_path, export_record
):
    from pa_agent.gui import export_dialog as ui

    source = tmp_path / "record.json"
    source.write_text(json.dumps(export_record), encoding="utf-8")
    source.with_suffix(".followups.jsonl").write_text(
        '{"turn":1,"user":"追问","ai_content":"回答"}\n', encoding="utf-8"
    )
    target = tmp_path / "result.zip"
    monkeypatch.setattr(ui.QFileDialog, "getSaveFileName", lambda *a: (str(target), ""))
    dialog = ui.AnalysisExportDialog(export_record, current_path=source, records_dir=tmp_path)
    qtbot.addWidget(dialog)
    assert "追问：1 轮" in dialog._summary.text()
    dialog._save()
    qtbot.waitUntil(lambda: dialog._worker is None, timeout=10000)
    assert target.exists() and "导出完成" in dialog._status.text()
    assert source.exists()


def test_dialog_rejects_export_over_original(qtbot, monkeypatch, tmp_path, export_record):
    from pa_agent.gui import export_dialog as ui

    source = tmp_path / "record.json"
    source.write_text(json.dumps(export_record), encoding="utf-8")
    before = source.read_bytes()
    dialog = ui.AnalysisExportDialog(export_record, current_path=source, records_dir=tmp_path)
    qtbot.addWidget(dialog)
    dialog._format.setCurrentIndex(dialog._format.findData(".json"))
    monkeypatch.setattr(ui.QFileDialog, "getSaveFileName", lambda *a: (str(source), ""))
    dialog._save()
    assert dialog._worker is None and source.read_bytes() == before
    assert "保留原始" in dialog._status.text()
