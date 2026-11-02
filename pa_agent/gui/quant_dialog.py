"""Native AlphaGPT research workbench; calculations stay off the GUI thread."""
from __future__ import annotations
import json
from pathlib import Path
import threading

from PyQt6.QtCore import QThread, pyqtSignal
from PyQt6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDoubleSpinBox, QFileDialog,
    QFormLayout, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPlainTextEdit,
    QProgressBar, QPushButton, QSpinBox, QTableWidget, QTableWidgetItem, QVBoxLayout)
import pyqtgraph as pg


class QuantWorker(QThread):
    result_ready = pyqtSignal(dict)
    failed = pyqtSignal(str)
    progress = pyqtSignal(int, int)

    def __init__(self, kind, chart, config, parent=None):
        super().__init__(parent)
        self.kind, self.chart, self.config = kind, chart, config
        self.cancel = threading.Event()

    def run(self):
        try:
            from pa_agent.config.paths import DATA_ROOT
            from pa_agent.quant.local import run_job
            result = run_job(self.kind, self.chart, self.config, DATA_ROOT / "quant",
                             cancel=self.cancel, progress=self.progress.emit)
            self.result_ready.emit(result)
        except Exception as exc:
            self.failed.emit(str(exc))


class QuantDialog(QDialog):
    def __init__(self, frame=None, source="unknown", parent=None):
        super().__init__(parent)
        self.setWindowTitle("AlphaGPT · 因子与历史研究")
        self.resize(1080, 820)
        self._worker = None
        self._chart = None
        self._result = None
        root = QVBoxLayout(self)
        self._status = QLabel("载入已收盘行情，或选择明确标注的模拟数据。")
        self._status.setWordWrap(True)
        root.addWidget(self._status)
        row = QHBoxLayout()
        self._symbol = QLineEdit("BTCUSD")
        self._tf = QComboBox()
        self._tf.addItems(["1m","5m","15m","30m","1h","4h","1d"])
        self._tf.setCurrentText("1h")
        row.addWidget(QLabel("CSV 标的")); row.addWidget(self._symbol)
        row.addWidget(QLabel("周期")); row.addWidget(self._tf)
        for text, action in (("导入 CSV",self._import_csv),("载入模拟研究示例",self._demo),
                             ("打开历史结果",self._open_result),("导出完整结果",self._export)):
            button = QPushButton(text); button.clicked.connect(action); row.addWidget(button)
        root.addLayout(row)
        self._assumption = QCheckBox("回测市场假设：连续现货、仅持有多头或现金（不适用于股票/期货）")
        root.addWidget(self._assumption)
        form = QHBoxLayout()
        self._factor = QComboBox(); self._factor.addItems(["DEV","RET","PRESSURE","FOMO","VOL"])
        self._fee = QDoubleSpinBox(); self._fee.setRange(0,500); self._fee.setValue(10)
        self._slippage = QDoubleSpinBox(); self._slippage.setRange(0,500); self._slippage.setValue(10)
        self._seed = QSpinBox(); self._seed.setRange(0,2**31-1); self._seed.setValue(42)
        self._trials = QSpinBox(); self._trials.setRange(8,128); self._trials.setValue(32)
        for label, widget in (("因子",self._factor),("手续费（bp）",self._fee),
                              ("滑点（bp）",self._slippage),("种子",self._seed),("候选数",self._trials)):
            form.addWidget(QLabel(label)); form.addWidget(widget)
        root.addLayout(form)
        actions = QHBoxLayout()
        self._buttons = []
        for text, kind in (("计算因子","factors"),("历史回测","research"),("挖掘 / 恢复公式","mine")):
            button = QPushButton(text); button.clicked.connect(lambda _,k=kind:self._run(k))
            actions.addWidget(button); self._buttons.append(button)
        self._cancel = QPushButton("取消"); self._cancel.setEnabled(False)
        self._cancel.clicked.connect(lambda: self._worker.cancel.set() if self._worker else None)
        actions.addWidget(self._cancel)
        self._progress = QProgressBar(); actions.addWidget(self._progress)
        root.addLayout(actions)
        self._table = QTableWidget(); root.addWidget(self._table,1)
        self._plot = pg.PlotWidget(axisItems={"bottom":pg.DateAxisItem(orientation="bottom")})
        self._plot.setBackground("#0d1117")
        self._plot.addLegend(); self._plot.showGrid(x=True,y=True,alpha=.2)
        root.addWidget(self._plot,2)
        self._notes = QPlainTextEdit(); self._notes.setReadOnly(True); self._notes.setMaximumHeight(120)
        root.addWidget(self._notes)
        if frame is not None:
            from pa_agent.data.snapshot import take_snapshot_from_bars
            closed_count = sum(bar.closed for bar in frame.bars)
            if closed_count:
                frame = take_snapshot_from_bars(list(frame.bars), closed_count, frame.symbol, frame.timeframe)
            from pa_agent.quant.data import chart_data
            from pa_agent.quant.snapshots import dataset_hash
            chart = chart_data(frame)
            chart.update(source=source, captured_at=frame.snapshot_ts_local_ms)
            chart["dataset_hash"] = dataset_hash(chart)
            self._set_chart(chart)

    def _set_chart(self, chart):
        if self._worker:
            return
        self._chart = chart
        self._status.setText(f"{chart['symbol']} · {chart['timeframe']} · {len(chart['bars'])} 根 · "
                             f"{'模拟数据' if chart['source']=='demo' else chart['source']} · "
                             f"数据指纹 {chart['dataset_hash'][:16]}")

    def _demo(self):
        from pa_agent.quant.local import demo_dataset
        self._set_chart(demo_dataset())
        self._assumption.setChecked(True)

    def _import_csv(self):
        if self._worker:
            return
        path, _ = QFileDialog.getOpenFileName(self, "导入 300–5000 根连续现货 OHLCV", "", "CSV (*.csv)")
        if not path:
            return
        try:
            from pa_agent.quant.research_data import DatasetImport, parse_dataset_csv
            source = Path(path)
            if source.stat().st_size > 2_000_000:
                raise ValueError("CSV 最大 2 MB。")
            self._set_chart(parse_dataset_csv(DatasetImport(csv=source.read_text(encoding="utf-8-sig"),
                              symbol=self._symbol.text().strip(), timeframe=self._tf.currentText())))
        except Exception as exc:
            QMessageBox.warning(self, "无法导入", str(exc))

    def _run(self, kind):
        if self._worker or self._chart is None:
            return
        if kind != "factors" and not self._assumption.isChecked():
            QMessageBox.information(self, "确认研究假设", "请确认数据适用于连续现货模型。股票和期货可计算因子，但不适用本回测。")
            return
        config = {"factor":self._factor.currentText(), "fee_bps":self._fee.value(), "slippage_bps":self._slippage.value()}
        if kind == "mine":
            config = {"research":config,"seed":self._seed.value(),"trials":self._trials.value()}
        if kind == "factors": config = {}
        worker = self._worker = QuantWorker(kind,self._chart,config,self)
        worker.result_ready.connect(self._show_result)
        worker.failed.connect(lambda message:self._notes.setPlainText(message))
        worker.progress.connect(lambda n,total:(self._progress.setRange(0,total),self._progress.setValue(n)))
        worker.finished.connect(self._done); worker.finished.connect(worker.deleteLater)
        for button in self._buttons: button.setEnabled(False)
        self._cancel.setEnabled(True); self._progress.setRange(0,0)
        worker.start()

    def _done(self):
        self._worker = None
        for button in self._buttons: button.setEnabled(True)
        self._cancel.setEnabled(False); self._progress.setRange(0,100); self._progress.setValue(100)

    def _show_result(self, envelope):
        self._result = envelope
        result = envelope["result"]
        self._plot.clear()
        if envelope["kind"] == "factors":
            headers = ["因子","原始值","标准化值","历史分位","状态"]
            rows = [[f["label"],f["value"],f["normalized"],f["percentile"],f["status"]] for f in result["factors"]]
            for i,factor in enumerate(result["factors"]):
                points = [(p["time"],p["normalized"]) for p in factor["series"] if p["normalized"] is not None]
                if points:
                    self._plot.plot([p[0] for p in points],[p[1] for p in points],pen=pg.intColor(i),name=factor["id"])
            notes = [f["message"] for f in result["quality_flags"]]
        else:
            research = result.get("research_result",result)
            headers = ["区间","收益","最大回撤","买入持有收益","交易数"]
            rows = []
            for i,(name,segment) in enumerate(research["segments"].items()):
                metrics=segment["metrics"]
                rows.append([segment["label"],f"{metrics['total_return']:.2%}",f"{metrics['max_drawdown']:.2%}",
                             f"{metrics['benchmark_return']:.2%}",metrics["closed_trades"]])
                points=segment["equity_curve"]
                self._plot.plot([p["time"] for p in points],[p["equity"] for p in points],pen=pg.intColor(i),name=name)
            notes = result.get("warnings",research["warnings"])
            if "formula" in result: notes = ["获选公式："+result["formula"]["expression"]]+notes
        self._table.setColumnCount(len(headers)); self._table.setHorizontalHeaderLabels(headers)
        self._table.setRowCount(len(rows))
        for r,row in enumerate(rows):
            for c,value in enumerate(row):
                text = "不可用" if value is None else f"{value:.6g}" if isinstance(value,float) else str(value)
                self._table.setItem(r,c,QTableWidgetItem(text))
        self._table.resizeColumnsToContents()
        self._notes.setPlainText("\n".join(notes))

    def _export(self):
        if self._result is None: return
        path,_=QFileDialog.getSaveFileName(self,"导出数据、配置、结果和血缘","quant-result.json","JSON (*.json)")
        if path:
            from pa_agent.quant.local import atomic_json
            atomic_json(Path(path),self._result)

    def _open_result(self):
        if self._worker: return
        from pa_agent.config.paths import DATA_ROOT
        path,_=QFileDialog.getOpenFileName(self,"打开本地结果",str(DATA_ROOT/"quant/results"),"JSON (*.json)")
        if path:
            try:
                result=json.loads(Path(path).read_text(encoding="utf-8"))
                self._set_chart(result["dataset"]); self._show_result(result)
            except Exception as exc: QMessageBox.warning(self,"无法读取结果",str(exc))

    def closeEvent(self,event):
        if self._worker and self._worker.isRunning():
            self._worker.cancel.set(); self._notes.setPlainText("正在停止计算，请稍后关闭。")
            event.ignore(); return
        super().closeEvent(event)
