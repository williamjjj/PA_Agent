"""Query information services and explicitly attach evidence to analysis."""

from __future__ import annotations

import json

from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QTabWidget,
    QTextBrowser,
    QVBoxLayout,
)

from pa_agent.data.information import query_information
from pa_agent.gui.data_sources_dialog import ConnectionWorker, DataSourcesDialog


class InformationDialog(QDialog):
    def __init__(self, ctx, symbol, timeframe, parent=None):
        super().__init__(parent)
        self.setWindowTitle("资讯与 MCP · 查询与分析资料")
        self.resize(920, 680)
        self._ctx, self._symbol, self._timeframe = ctx, symbol, timeframe
        self._worker = None
        self._result = None
        root = QVBoxLayout(self)
        self._label = QLabel(
            f"当前品种：{symbol} · 资料按来源与查询时间保存。可选择附加到该品种的下一次分析。"
        )
        self._label.setWordWrap(True)
        root.addWidget(self._label)
        row = QHBoxLayout()
        self._source = QComboBox()
        row.addWidget(self._source, 1)
        self._manage = QPushButton("管理数据源…")
        self._manage.clicked.connect(self._open_manager)
        row.addWidget(self._manage)
        root.addLayout(row)
        row = QHBoxLayout()
        self._query = QLineEdit(
            f"{symbol} 最近一周的重要新闻、公告及风险事件，请保留日期和来源链接"
        )
        row.addWidget(self._query, 1)
        self._fetch = QPushButton("查询")
        self._fetch.clicked.connect(self._run)
        row.addWidget(self._fetch)
        root.addLayout(row)
        tabs = QTabWidget()
        self._article_view = QTextBrowser()
        self._article_view.setOpenExternalLinks(True)
        tabs.addTab(self._article_view, "阅读资讯")
        self._result_view = QPlainTextEdit()
        self._result_view.setReadOnly(True)
        tabs.addTab(self._result_view, "原始数据与来源")
        root.addWidget(tabs, 1)
        self._status = QLabel("")
        self._status.setWordWrap(True)
        root.addWidget(self._status)
        row = QHBoxLayout()
        self._attach = QPushButton("加入本品种分析资料")
        self._attach.setEnabled(False)
        self._attach.clicked.connect(self._use_result)
        row.addWidget(self._attach)
        clear = QPushButton("清除本品种已附加资料")
        clear.clicked.connect(self._clear)
        row.addWidget(clear)
        close = QPushButton("返回主界面")
        close.clicked.connect(self.accept)
        row.addWidget(close)
        root.addLayout(row)
        self._reload_sources()
        self._update_count()

    def _reload_sources(self):
        current = self._source.currentData()
        self._source.clear()
        for profile in self._ctx.settings.data_sources.profiles:
            if profile.enabled and profile.category == "information":
                self._source.addItem(profile.name, profile.id)
        if self._source.findData(current) >= 0:
            self._source.setCurrentIndex(self._source.findData(current))
        self._fetch.setEnabled(self._source.count() > 0)
        if not self._source.count():
            self._article_view.setPlainText(
                "请点击「管理数据源」新增金十、东方财富妙想或其他资讯 API / MCP。"
            )
            self._result_view.setPlainText(
                "请点击「管理数据源」新增金十、东方财富妙想或其他资讯 API / MCP。"
            )

    def _open_manager(self):
        if hasattr(self.parent(), "_open_data_sources_dialog"):
            self.parent()._open_data_sources_dialog()
            self._reload_sources()
            return
        dialog = DataSourcesDialog(self._ctx.settings, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._reload_sources()

    def _run(self):
        if self._worker or not self._query.text().strip():
            return
        profile = next(
            (
                p
                for p in self._ctx.settings.data_sources.profiles
                if p.id == self._source.currentData()
            ),
            None,
        )
        if profile is None:
            return
        query = self._query.text().strip()
        profile = profile.model_copy(deep=True)
        worker = self._worker = ConnectionWorker(
            lambda: query_information(profile, query, self._symbol, self._timeframe), self
        )
        worker.completed.connect(self._show_result)
        worker.failed.connect(self._status.setText)
        worker.finished.connect(self._done)
        worker.finished.connect(worker.deleteLater)
        self._fetch.setEnabled(False)
        self._manage.setEnabled(False)
        self._attach.setEnabled(False)
        self._result = None
        self._status.setText("正在查询…")
        worker.start()

    def _show_result(self, result):
        self._result = result
        self._article_view.setHtml(render_information(result))
        self._result_view.setPlainText(json.dumps(result, ensure_ascii=False, indent=2))
        self._attach.setEnabled(True)
        self._status.setText(
            "查询完成，资料已保存。点击下方按钮可附加到分析。"
            + ("内容较长，已截取前 16000 字符。" if result["truncated"] else "")
        )

    def _done(self):
        self._worker = None
        self._fetch.setEnabled(True)
        self._manage.setEnabled(True)

    def _use_result(self):
        if self._result:
            snapshots = [
                item for item in self._ctx.information_context if item["id"] != self._result["id"]
            ]
            self._ctx.information_context = (snapshots + [self._result])[-4:]
            self._update_count()

    def _clear(self):
        self._ctx.information_context = [
            item for item in self._ctx.information_context if item["symbol"] != self._symbol
        ]
        self._update_count()

    def _update_count(self):
        count = sum(item["symbol"] == self._symbol for item in self._ctx.information_context)
        self._status.setText(f"本品种已附加 {count} 份资料；分析会记录所用来源、查询时间和内容。")

    def done(self, result):
        if not self._worker:
            super().done(result)

    def closeEvent(self, event):
        if self._worker:
            event.ignore()
        else:
            super().closeEvent(event)


def render_information(snapshot):
    """Readable cards for Jin10 objects and Miaoxiang's column/row results."""
    from html import escape
    from urllib.parse import urlsplit

    def text(value):
        value = str(value)
        try:
            is_link = urlsplit(value).scheme in ("http", "https") and "\n" not in value
        except ValueError:
            is_link = False
        if is_link:
            return f'<a href="{escape(value, quote=True)}">打开原文</a>'
        return escape(value).replace("\n", "<br>")

    html = [
        f"<h3>{escape(snapshot['source'])}</h3>",
        f"<p>查询：{escape(snapshot['query'])}<br>查询时间：{escape(snapshot['captured_at'])}</p>",
    ]
    try:
        payload = json.loads(snapshot["content"])
    except ValueError:
        return "".join(html) + "<p>" + text(snapshot["content"]) + "</p>"
    data = payload.get("data", payload) if isinstance(payload, dict) else payload
    if isinstance(data, dict) and isinstance(data.get("items"), list):
        data = data["items"]
    if isinstance(data, list):
        items = []
        for item in data:
            if isinstance(item, dict) and "columns" in item and "items" in item:
                items.extend(
                    dict(zip(item["columns"], row))
                    for row in item["items"]
                    if isinstance(row, list)
                )
            else:
                items.append(item)
        for item in items[:40]:
            if not isinstance(item, dict):
                html.append("<p>" + text(item) + "</p>")
                continue
            title = item.get("title") or item.get("标题") or item.get("name") or "资料"
            html.append("<hr><h3>" + text(title) + "</h3>")
            for key, value in item.items():
                if key in ("title", "标题") or value in (None, ""):
                    continue
                label = {
                    "content": "正文",
                    "time": "发布时间",
                    "url": "来源链接",
                    "introduction": "摘要",
                    "id": "编号",
                }.get(key, key)
                html.append(f"<p><b>{escape(str(label))}</b>：{text(value)}</p>")
    else:
        if isinstance(data, dict) and "text" in data:
            data = data["text"]
        html.append(
            "<p>"
            + text(
                json.dumps(data, ensure_ascii=False, indent=2)
                if not isinstance(data, str)
                else data
            )
            + "</p>"
        )
    return "".join(html)
