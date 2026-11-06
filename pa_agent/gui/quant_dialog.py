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
    def __init__(self, frame=None, source="unknown", parent=None, *, snapshot_provider=None,
                 settings=None, exchange=""):
        super().__init__(parent)
        self.setWindowTitle("AlphaGPT · 因子与历史研究")
        self.resize(1080, 820)
        self._worker = None
        self._chart = None
        self._result = None
        self._snapshot_provider = snapshot_provider
        self._source_kind, self._source_settings, self._exchange = source, settings, exchange
        root = QVBoxLayout(self)
        self._status = QLabel("载入已收盘行情，或选择明确标注的模拟数据。")
        self._status.setWordWrap(True)
        root.addWidget(self._status)
        row = QHBoxLayout()
        self._symbol = QLineEdit("BTCUSD")
        self._tf = QComboBox()
        from pa_agent.quant.markets import MARKET_LABELS, TIMEFRAME_SECONDS, infer_market
        self._tf.addItems(list(TIMEFRAME_SECONDS))
        self._tf.setCurrentText("1h")
        row.addWidget(QLabel("获取 / 导入品种")); row.addWidget(self._symbol)
        row.addWidget(QLabel("周期")); row.addWidget(self._tf)
        for text, action in (("导入 CSV",self._import_csv),("载入模拟研究示例",self._demo),
                             ("打开历史结果",self._open_result),("导出完整结果",self._export)):
            button = QPushButton(text); button.clicked.connect(action); row.addWidget(button)
        root.addLayout(row)
        source_row = QHBoxLayout()
        self._market = QComboBox()
        for key, label in MARKET_LABELS.items():
            self._market.addItem(label, key)
        self._market.setCurrentIndex(self._market.findData(infer_market(source, frame.symbol if frame else "", exchange)))
        self._bar_count = QSpinBox(); self._bar_count.setRange(300, 5000); self._bar_count.setValue(3000)
        current = QPushButton("使用 PA Agent 当前行情"); current.clicked.connect(self._load_current)
        fetch = QPushButton("从当前数据源获取"); fetch.clicked.connect(self._fetch_source)
        source_row.addWidget(QLabel("市场类型")); source_row.addWidget(self._market)
        source_row.addWidget(QLabel("获取根数")); source_row.addWidget(self._bar_count)
        source_row.addWidget(current); source_row.addWidget(fetch)
        root.addLayout(source_row)
        self._assumption = QLabel("信号按已收盘 K 线计算，下一根有效交易 K 线开盘执行；历史研究采用 1 倍名义敞口。")
        self._assumption.setWordWrap(True)
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
        position_row = QHBoxLayout()
        self._position_mode = QComboBox()
        for label, value in (("做多 / 现金", "long_only"), ("做空 / 现金", "short_only"), ("双向", "long_short")):
            self._position_mode.addItem(label, value)
        self._capital = QDoubleSpinBox(); self._capital.setRange(100, 1e9); self._capital.setValue(100_000)
        self._quantity_step = QDoubleSpinBox(); self._quantity_step.setRange(0, 1e6); self._quantity_step.setDecimals(4)
        self._multiplier = QDoubleSpinBox(); self._multiplier.setRange(.0001, 1e6); self._multiplier.setDecimals(4); self._multiplier.setValue(1)
        self._multiplier.setToolTip("期货按实际合约规格填写；默认 1 表示价格单位对应的标准化数量，不自动推断合约规格。")
        for label, control in (("仓位方向", self._position_mode), ("初始资金", self._capital),
                               ("数量步长（0=可分割）", self._quantity_step), ("合约乘数", self._multiplier)):
            position_row.addWidget(QLabel(label)); position_row.addWidget(control)
        root.addLayout(position_row)
        self._market.currentIndexChanged.connect(self._market_changed)
        self._market_changed()
        actions = QHBoxLayout()
        self._buttons = []
        for text, kind in (("计算因子","factors"),("历史回测","research"),("挖掘 / 恢复公式","mine")):
            button = QPushButton(text); button.clicked.connect(lambda _,k=kind:self._run(k))
            actions.addWidget(button); self._buttons.append(button)
        self._cancel = QPushButton("取消"); self._cancel.setEnabled(False)
        self._cancel.clicked.connect(self._cancel_work)
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
            self._use_frame(frame, source, exchange)

    def _set_chart(self, chart, *, loaded=False):
        if self._worker and not loaded:
            return
        self._chart = chart
        self._symbol.setText(chart["symbol"])
        self._tf.setCurrentText(chart["timeframe"])
        if chart.get("market_kind"):
            self._market.setCurrentIndex(self._market.findData(chart["market_kind"]))
        self._status.setText(f"{chart['symbol']} · {chart['timeframe']} · {len(chart['bars'])} 根 · "
                             f"{'模拟数据' if chart['source']=='demo' else chart['source']} · "
                             f"数据指纹 {chart['dataset_hash'][:16]}")

    def _demo(self):
        from pa_agent.quant.local import demo_dataset
        self._set_chart(demo_dataset())
        self._market.setCurrentIndex(self._market.findData("continuous_spot"))

    def _market_changed(self):
        kind = self._market.currentData()
        self._position_mode.setCurrentIndex(0 if kind in {"stock", "continuous_spot"} else 2)
        self._position_mode.setEnabled(kind != "stock")
        self._quantity_step.setValue(1 if kind in {"stock", "futures"} else 0)

    def _use_frame(self, frame, source, exchange):
        from pa_agent.gui.quant_source_worker import chart_from_frame
        self._set_chart(chart_from_frame(frame, source, exchange, self._market.currentData()))

    def _load_current(self):
        if self._worker:
            return
        if not callable(self._snapshot_provider):
            self._notes.setPlainText("请从 PA Agent 主窗口打开研究窗口，或使用数据源获取 / CSV 导入。")
            return
        frame, source, exchange = self._snapshot_provider()
        if frame is None:
            self._notes.setPlainText("主图尚无行情。可点击“从当前数据源获取”，直接取得所选品种和周期的研究数据。")
            return
        self._use_frame(frame, source, exchange)

    def _fetch_source(self):
        if self._worker:
            return
        if self._source_settings is None or self._source_kind == "unknown":
            self._notes.setPlainText("请先在 PA Agent 主窗口选择数据源。")
            return
        from pa_agent.gui.quant_source_worker import QuantSourceWorker
        worker = self._worker = QuantSourceWorker(self._source_kind, self._source_settings,
            self._symbol.text().strip(), self._tf.currentText(), self._bar_count.value(),
            self._exchange, self._market.currentData(), self)
        worker.result_ready.connect(lambda chart: self._set_chart(chart, loaded=True))
        worker.failed.connect(self._notes.setPlainText)
        worker.finished.connect(self._done); worker.finished.connect(worker.deleteLater)
        self._notes.setPlainText("正在后台获取研究行情…")
        self._cancel.setEnabled(True); self._progress.setRange(0, 0)
        for button in self._buttons: button.setEnabled(False)
        worker.start()

    def _cancel_work(self):
        if self._worker:
            if hasattr(self._worker, "stop"):
                self._worker.stop()
            else:
                self._worker.cancel.set()
            self._notes.setPlainText("已请求取消，等待当前计算步骤结束。")

    def _import_csv(self):
        if self._worker:
            return
        path, _ = QFileDialog.getOpenFileName(self, "导入 300–5000 根 OHLCV", "", "CSV (*.csv)")
        if not path:
            return
        try:
            from pa_agent.quant.research_data import DatasetImport, parse_dataset_csv
            source = Path(path)
            if source.stat().st_size > 2_000_000:
                raise ValueError("CSV 最大 2 MB。")
            self._set_chart(parse_dataset_csv(DatasetImport(csv=source.read_text(encoding="utf-8-sig"),
                              symbol=self._symbol.text().strip(), timeframe=self._tf.currentText(),
                              market_kind=self._market.currentData())))
        except Exception as exc:
            QMessageBox.warning(self, "无法导入", str(exc))

    def _run(self, kind):
        if self._worker or self._chart is None:
            return
        config = {"factor":self._factor.currentText(), "fee_bps":self._fee.value(), "slippage_bps":self._slippage.value(),
                  "position_mode": self._position_mode.currentData(), "initial_capital": self._capital.value(),
                  "quantity_step": self._quantity_step.value(), "contract_multiplier": self._multiplier.value()}
        if kind == "mine":
            config = {"research":config,"seed":self._seed.value(),"trials":self._trials.value()}
        if kind == "factors": config = {}
        chart = dict(self._chart)
        if chart.get("market_kind") != self._market.currentData():
            chart["market_kind"] = self._market.currentData()
            chart.pop("dataset_version", None)
            from pa_agent.quant.snapshots import dataset_hash
            chart["dataset_hash"] = dataset_hash(chart)
        worker = self._worker = QuantWorker(kind,chart,config,self)
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
                self._set_chart(result["dataset"])
                research = result["result"].get("research_result", result["result"])
                config = research.get("config", {})
                for key, control in (("fee_bps", self._fee), ("slippage_bps", self._slippage),
                        ("initial_capital", self._capital), ("quantity_step", self._quantity_step),
                        ("contract_multiplier", self._multiplier)):
                    if key in config: control.setValue(config[key])
                self._position_mode.setCurrentIndex(self._position_mode.findData(config.get("position_mode", "long_only")))
                self._show_result(result)
            except Exception as exc: QMessageBox.warning(self,"无法读取结果",str(exc))

    def closeEvent(self,event):
        if self._worker and self._worker.isRunning():
            self._cancel_work(); self._notes.setPlainText("正在停止计算，请稍后关闭。")
            event.ignore(); return
        super().closeEvent(event)
