"""Export a selected current or historical analysis without running a model."""

from __future__ import annotations

import copy
from pathlib import Path

from PyQt6.QtCore import Qt, QThread, QUrl, pyqtSignal
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
)

from pa_agent.config.paths import DATA_ROOT, RECORDS_PENDING_DIR
from pa_agent.records.exporter import (
    SECTIONS,
    build_export,
    configured_secrets,
    default_filename,
    read_record,
    write_export,
)


class ExportWorker(QThread):
    completed = pyqtSignal(dict)
    failed = pyqtSignal(str)

    def __init__(self, path, record, sections, followups, warnings, secrets, parent=None):
        super().__init__(parent)
        self.path, self.record, self.sections = path, record, sections
        self.followups, self.warnings, self.secrets = followups, warnings, secrets

    def run(self):
        try:
            payload = build_export(
                self.record,
                sections=self.sections,
                followups=self.followups,
                warnings=self.warnings,
                secrets=self.secrets,
            )
            self.completed.emit(write_export(self.path, payload, overwrite=True))
        except (OSError, ValueError) as exc:
            self.failed.emit(f"导出失败：{exc}")
        except Exception:
            self.failed.emit("导出失败：记录内容无法转换，请尝试 JSON 格式。")


class AnalysisExportDialog(QDialog):
    def __init__(
        self,
        current=None,
        *,
        current_path=None,
        settings=None,
        running=False,
        records_dir=RECORDS_PENDING_DIR,
        parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle("导出分析 · 结果与各环节")
        self.resize(740, 570)
        self._settings = settings
        self._current = current.model_dump() if hasattr(current, "model_dump") else current
        self._current_path = Path(current_path) if current_path else None
        self._records_dir = Path(records_dir)
        self._record = None
        self._source_path = None
        self._followups, self._warnings = [], []
        self._worker = None
        self._last_output = None
        root = QVBoxLayout(self)
        note = QLabel("选择一条分析记录与需要的环节。导出使用已保存的内容，不重新调用模型。")
        note.setWordWrap(True)
        root.addWidget(note)
        if running:
            notice = QLabel(
                "当前轮仍在运行：这里可导出上一条已返回的结果或历史记录。当前轮结束后重新打开即可导出。"
            )
            notice.setWordWrap(True)
            root.addWidget(notice)
        row = QHBoxLayout()
        self._records = QComboBox()
        row.addWidget(self._records, 1)
        self._browse = QPushButton("选择记录文件…")
        self._browse.clicked.connect(self._browse_record)
        row.addWidget(self._browse)
        root.addLayout(row)
        self._summary = QLabel()
        self._summary.setTextFormat(Qt.TextFormat.PlainText)
        self._summary.setWordWrap(True)
        root.addWidget(self._summary)
        grid = QGridLayout()
        self._sections = {}
        for index, (key, label) in enumerate(SECTIONS.items()):
            box = QCheckBox(label)
            box.setChecked(True)
            self._sections[key] = box
            grid.addWidget(box, index // 2, index % 2)
        root.addLayout(grid)
        row = QHBoxLayout()
        for label, keys in (
            ("全部环节", list(SECTIONS)),
            ("仅最终结论", ["summary"]),
            ("诊断与决策", ["summary", "stage1", "stage2"]),
        ):
            button = QPushButton(label)
            button.clicked.connect(lambda _, selected=keys: self._select_sections(selected))
            row.addWidget(button)
        root.addLayout(row)
        self._format = QComboBox()
        for label, ext in (
            ("完整资料包 ZIP（报告 + 各环节 JSON + K线 CSV + 模型文本）", ".zip"),
            ("可阅读 / 打印的 HTML 报告", ".html"),
            ("Markdown 文档", ".md"),
            ("结构化 JSON", ".json"),
        ):
            self._format.addItem(label, ext)
        root.addWidget(self._format)
        hint = QLabel(
            "ZIP 适合完整留档；HTML 可直接阅读，也可用浏览器打印为 PDF。\n失败或取消的记录会标明状态；旧记录缺少的环节会明确标注。"
        )
        hint.setWordWrap(True)
        root.addWidget(hint)
        root.addStretch()
        self._status = QLabel()
        self._status.setTextFormat(Qt.TextFormat.PlainText)
        self._status.setWordWrap(True)
        root.addWidget(self._status)
        row = QHBoxLayout()
        self._export = QPushButton("选择位置并导出…")
        self._export.clicked.connect(self._save)
        row.addWidget(self._export)
        self._open = QPushButton("打开导出文件夹")
        self._open.setEnabled(False)
        self._open.clicked.connect(self._open_folder)
        row.addWidget(self._open)
        close = QPushButton("关闭")
        close.clicked.connect(self.reject)
        row.addWidget(close)
        root.addLayout(row)
        if self._current is not None:
            self._records.addItem("当前显示的分析结果", None)
        from pa_agent.records.analysis_history import list_record_paths

        for path in list_record_paths(self._records_dir)[:2000]:
            self._records.addItem(path.stem, str(path))
        self._records.currentIndexChanged.connect(self._load_selected)
        self._load_selected()

    def _select_sections(self, selected):
        if self._worker:
            return
        for key, box in self._sections.items():
            box.setChecked(key in selected)

    def _load_selected(self):
        self._record, self._source_path = None, None
        self._followups, self._warnings = [], []
        value = self._records.currentData()
        try:
            if self._records.count() == 0:
                self._summary.setText("还没有已保存的分析。完成一次分析，或选择已有记录文件。")
                return
            if value is not None:
                self._source_path = Path(value)
                self._record, self._followups, self._warnings = read_record(self._source_path)
            else:
                self._record = copy.deepcopy(self._current)
                path = self._current_path
                if path is None and self._record:
                    from pa_agent.records.pending_writer import _build_basename
                    from pa_agent.records.schema import AnalysisRecord

                    path = self._records_dir / (
                        _build_basename(AnalysisRecord.model_validate(self._record)) + ".json"
                    )
                self._source_path = path
                if path and path.is_file():
                    # Read followups from disk while retaining the exact currently displayed record.
                    saved, self._followups, self._warnings = read_record(path)
                    if saved.get("_partial_reason"):
                        self._record["_partial_reason"] = saved["_partial_reason"]
            payload = build_export(self._record, sections=["summary"])
            meta = payload["meta"]
            self._summary.setText(
                f"{meta.get('symbol')} · {meta.get('timeframe')} · {meta.get('timestamp_local_iso')}\n"
                f"状态：{payload['status']}　K线：{meta.get('bar_count')} 根　追问：{len(self._followups)} 轮"
            )
            self._status.setText("\n".join(self._warnings))
        except (OSError, ValueError, TypeError):
            self._record = None
            self._summary.setText("无法读取此记录。请选择 PA Agent 的分析 JSON 文件。")
        finally:
            self._export.setEnabled(self._record is not None and self._worker is None)

    def _browse_record(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择 PA Agent 分析记录", str(self._records_dir), "分析记录 (*.json)"
        )
        if path:
            self._records.addItem(Path(path).stem, path)
            self._records.setCurrentIndex(self._records.count() - 1)

    def _save(self):
        if self._worker or self._record is None:
            return
        sections = [key for key, box in self._sections.items() if box.isChecked()]
        if not sections:
            self._status.setText("请至少勾选一个环节。")
            return
        ext = self._format.currentData()
        directory = DATA_ROOT / "exports"
        try:
            directory.mkdir(parents=True, exist_ok=True)
        except OSError:
            directory = DATA_ROOT
        target, _ = QFileDialog.getSaveFileName(
            self,
            "导出分析",
            str(directory / (default_filename(self._record) + ext)),
            f"导出文件 (*{ext})",
        )
        if not target:
            return
        path = Path(target)
        if not path.suffix:
            path = path.with_suffix(ext)
        if path.suffix.lower() != ext:
            self._status.setText(f"请使用 {ext} 文件扩展名。")
            return
        if self._source_path and path.resolve() == self._source_path.resolve():
            self._status.setText("请使用新的文件名，保留原始分析记录。")
            return
        worker = self._worker = ExportWorker(
            path,
            copy.deepcopy(self._record),
            sections,
            copy.deepcopy(self._followups),
            list(self._warnings),
            configured_secrets(self._settings),
            self,
        )
        worker.completed.connect(self._complete)
        worker.failed.connect(self._status.setText)
        worker.finished.connect(self._done)
        worker.finished.connect(worker.deleteLater)
        self._records.setEnabled(False)
        self._browse.setEnabled(False)
        self._format.setEnabled(False)
        self._export.setEnabled(False)
        self._status.setText("正在整理并导出…")
        worker.start()

    def _complete(self, result):
        self._last_output = Path(result["path"])
        self._open.setEnabled(True)
        self._status.setText(
            f"导出完成：{result['path']}\n文件大小：{result['bytes'] / 1024:.1f} KB"
        )

    def _done(self):
        self._worker = None
        self._records.setEnabled(True)
        self._browse.setEnabled(True)
        self._format.setEnabled(True)
        self._export.setEnabled(self._record is not None)

    def _open_folder(self):
        if self._last_output:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._last_output.parent)))

    def reject(self):
        if not self._worker:
            super().reject()

    def closeEvent(self, event):
        if self._worker:
            event.ignore()
        else:
            super().closeEvent(event)
