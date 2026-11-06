"""Inspectable incremental feedback, scoped by source, symbol and timeframe."""
from PyQt6.QtWidgets import (QDialog, QFileDialog, QHBoxLayout, QLabel, QPlainTextEdit,
    QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout)


class ReviewDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("增量复盘与经验")
        self.resize(950, 650)
        root = QVBoxLayout(self)
        explanation = QLabel("按品种、行情来源、交易所和时间周期分别复盘。每连续 10 次有效对比形成一版经验；"
                            "不足 10 次时只积累记录。比较上一轮分析完成时的报价与下一轮新增已收盘 K 线末价。")
        explanation.setWordWrap(True)
        root.addWidget(explanation)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["品种", "周期", "来源 / 交易所", "连续有效对比", "已形成经验版本"])
        root.addWidget(self.table)
        self.notes = QPlainTextEdit()
        self.notes.setReadOnly(True)
        root.addWidget(self.notes)
        row = QHBoxLayout()
        refresh = QPushButton("刷新记录")
        refresh.clicked.connect(self.refresh)
        export = QPushButton("导出复盘与经验")
        export.clicked.connect(self.export)
        row.addWidget(refresh)
        row.addWidget(export)
        root.addLayout(row)
        self.refresh()

    def refresh(self):
        from pa_agent.config.paths import DATA_ROOT
        from pa_agent.records.rolling_review import ReviewStore

        self.report = ReviewStore(DATA_ROOT / "learning" / "reviews.sqlite3").report()
        states, summaries = self.report["streams"], self.report["summaries"]
        self.table.setRowCount(len(states))
        for row, state in enumerate(states):
            identity = state.get("identity", {})
            versions = sum(s["identity"] == identity for s in summaries)
            values = [identity.get("symbol", ""), identity.get("timeframe", ""),
                      f"{identity.get('source', '')} / {identity.get('exchange', '')}",
                      f"{state['consecutive']} 次（每 10 次总结）", versions]
            for column, value in enumerate(values):
                self.table.setItem(row, column, QTableWidgetItem(str(value)))
        self.table.resizeColumnsToContents()
        self.notes.setPlainText("\n\n────────\n\n".join(s["text"] for s in summaries)
            if summaries else "尚未形成经验。开启“持续跟踪分析”，保留增量分析设置；累计 10 次有效对比后会自动生成，并供同品种同周期的后续分析引用。\n\n无新增已收盘 K 线、重复快照、完成时缺少新鲜报价的轮次不计入样本。")

    def export(self):
        path, _ = QFileDialog.getSaveFileName(self, "导出增量复盘", "incremental-reviews.json", "JSON (*.json)")
        if path:
            from pathlib import Path
            from pa_agent.quant.local import atomic_json
            atomic_json(Path(path), self.report)
