"""Manage persistent market and information connections from the desktop UI."""

from __future__ import annotations

import json
from pathlib import Path

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from pa_agent.config.settings import save_settings
from pa_agent.data.connections import import_mcp_config, mcp_request, validate_profile
from pa_agent.data.source_config import BUILTIN_SOURCES, TIMEFRAMES, SourceProfile, source_templates


class ConnectionWorker(QThread):
    completed = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, action, parent=None):
        super().__init__(parent)
        self.action = action

    def run(self):
        try:
            self.completed.emit(self.action())
        except Exception as exc:
            from pa_agent.data.base import DataSourceError
            from pa_agent.data.connections import ConnectionError

            message = (
                str(exc)
                if isinstance(exc, (DataSourceError, ConnectionError))
                else "请求失败，请检查配置、网络和服务授权。"
            )
            self.failed.emit(message)


def _json_edit(value, *, secret=False):
    edit = QLineEdit(json.dumps(value, ensure_ascii=False))
    if secret:
        edit.setEchoMode(QLineEdit.EchoMode.Password)
    return edit


class SourceEditor(QDialog):
    def __init__(self, profile=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("新增数据源" if profile is None else "编辑数据源")
        self.resize(850, 840)
        self.profile = profile
        self._worker = None
        self._tools = []
        self._templates = source_templates()
        root = QVBoxLayout(self)
        self._template = QComboBox()
        self._template.addItems(self._templates)
        root.addWidget(QLabel("选择接入模板；地址、参数和字段映射均可修改。"))
        root.addWidget(self._template)
        scroll = self._scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        layout = QVBoxLayout(content)
        scroll.setWidget(content)
        root.addWidget(scroll, 1)
        basic = QFormLayout()
        layout.addLayout(basic)
        self._name = QLineEdit()
        self._category = QComboBox()
        self._category.addItem("行情 · 图表、分析与因子", "market")
        self._category.addItem("资讯 · 新闻、公告、宏观与财务", "information")
        self._transport = QComboBox()
        for label, key in (
            ("本地 CSV", "csv"),
            ("HTTP / JSON API", "http"),
            ("MCP · Streamable HTTP", "mcp_http"),
            ("MCP · SSE", "mcp_sse"),
            ("MCP · 本地程序", "mcp_stdio"),
        ):
            self._transport.addItem(label, key)
        basic.addRow("名称", self._name)
        basic.addRow("用途", self._category)
        basic.addRow("连接方式", self._transport)
        self._description = QLabel()
        self._description.setWordWrap(True)
        layout.addWidget(self._description)

        self._csv_group = QGroupBox("CSV 文件")
        csv_form = QFormLayout(self._csv_group)
        row = QHBoxLayout()
        self._csv = QLineEdit()
        browse = QPushButton("选择文件…")
        browse.clicked.connect(self._browse_csv)
        row.addWidget(self._csv)
        row.addWidget(browse)
        csv_form.addRow("文件位置", row)
        csv_form.addRow(
            QLabel("UTF-8 CSV；时间为 Unix 秒/毫秒或 ISO 时间。无时区的时间按 UTC 读取。")
        )
        layout.addWidget(self._csv_group)

        self._http_group = QGroupBox("接口与授权")
        form = QFormLayout(self._http_group)
        self._url = QLineEdit()
        self._url.setPlaceholderText("https://…  可用 {symbol} {timeframe} {limit} {query}")
        self._headers = _json_edit({}, secret=True)
        self._env_headers = _json_edit({})
        self._auth_header = QLineEdit("Authorization")
        self._auth_value = QLineEdit()
        self._auth_value.setEchoMode(QLineEdit.EchoMode.Password)
        self._auth_value.setPlaceholderText("金十：Bearer 空格后接 Token；妙想：填写 Key 原文")
        self._method = QComboBox()
        self._method.addItems(["GET", "POST"])
        self._body = _json_edit({})
        for label, widget in (
            ("接口地址", self._url),
            ("授权头名称", self._auth_header),
            ("授权头内容", self._auth_value),
            ("其他请求头（JSON）", self._headers),
            ("从环境变量读取请求头（JSON）", self._env_headers),
            ("API 请求方法", self._method),
            ("POST 请求体（JSON）", self._body),
        ):
            form.addRow(label, widget)
        layout.addWidget(self._http_group)

        self._stdio_group = QGroupBox("本地 MCP 程序")
        form = QFormLayout(self._stdio_group)
        self._command = QLineEdit()
        self._args = _json_edit([])
        self._env = _json_edit({}, secret=True)
        form.addRow("程序路径", self._command)
        form.addRow("参数数组（JSON）", self._args)
        form.addRow("环境变量（JSON）", self._env)
        layout.addWidget(self._stdio_group)

        self._mcp_group = QGroupBox("MCP 工具")
        form = QFormLayout(self._mcp_group)
        row = QHBoxLayout()
        self._tool = QComboBox()
        self._tool.setEditable(True)
        self._discover = QPushButton("读取工具列表")
        self._discover.clicked.connect(self._discover_tools)
        row.addWidget(self._tool, 1)
        row.addWidget(self._discover)
        form.addRow("查询工具", row)
        self._arguments = _json_edit({"query": "{query}"})
        form.addRow("工具参数（JSON）", self._arguments)
        self._schema = QPlainTextEdit()
        self._schema.setReadOnly(True)
        self._schema.setMaximumHeight(120)
        form.addRow("参数说明", self._schema)
        self._tool.activated.connect(self._tool_selected)
        layout.addWidget(self._mcp_group)

        self._market_group = QGroupBox("行情字段")
        form = QFormLayout(self._market_group)
        self._symbol = QLineEdit()
        self._tf = QComboBox()
        self._tf.addItems(TIMEFRAMES)
        self._tfmap = _json_edit({})
        self._columns = _json_edit({})
        self._limit = QSpinBox()
        self._limit.setRange(1, 10000)
        self._poll = QSpinBox()
        self._poll.setRange(1, 3600)
        self._poll.setSuffix(" 秒")
        for label, widget in (
            ("默认品种", self._symbol),
            ("默认周期 / CSV 周期", self._tf),
            ("周期映射（JSON）", self._tfmap),
            ("字段映射（JSON）", self._columns),
            ("每次请求最多 K 线", self._limit),
            ("最短请求间隔", self._poll),
        ):
            form.addRow(label, widget)
        form.addRow(QLabel("字段路径支持 data.price；数组列用 0、1、2…；可选 closed 字段为 0/1。"))
        layout.addWidget(self._market_group)
        extra = QFormLayout()
        self._rows_path = QLineEdit()
        self._rows_path.setPlaceholderText("如 data.klines / data.items；根节点留空")
        self._timeout = QSpinBox()
        self._timeout.setRange(3, 180)
        self._timeout.setSuffix(" 秒")
        self._query = QLineEdit("近期市场新闻与风险事件")
        extra.addRow("返回数据路径", self._rows_path)
        extra.addRow("请求超时", self._timeout)
        extra.addRow("测试查询", self._query)
        layout.addLayout(extra)
        self._preview = QPlainTextEdit()
        self._preview.setReadOnly(True)
        self._preview.setMaximumHeight(110)
        root.addWidget(self._preview)
        row = QHBoxLayout()
        self._test = QPushButton("测试读取")
        self._test.clicked.connect(self._test_connection)
        row.addWidget(self._test)
        self._buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        self._buttons.button(QDialogButtonBox.StandardButton.Save).setText("保存数据源")
        self._buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        self._buttons.accepted.connect(self._save)
        self._buttons.rejected.connect(self.reject)
        row.addWidget(self._buttons)
        root.addLayout(row)
        self._template.currentTextChanged.connect(lambda name: self._load(self._templates[name]))
        self._transport.currentIndexChanged.connect(self._visibility)
        self._category.currentIndexChanged.connect(self._visibility)
        self._load(profile or next(iter(self._templates.values())))

    def _load(self, p):
        self._name.setText(p.name)
        self._category.setCurrentIndex(self._category.findData(p.category))
        self._transport.setCurrentIndex(self._transport.findData(p.transport))
        self._description.setText(p.description)
        self._url.setText(p.url)
        self._csv.setText(p.csv_path)
        self._auth_header.setText(
            "em_api_key" if "mxapi.eastmoney.com" in p.url else "Authorization"
        )
        headers = dict(p.headers)
        self._auth_value.setText(headers.pop(self._auth_header.text(), ""))
        for widget, value in (
            (self._headers, headers),
            (self._env_headers, p.env_headers),
            (self._body, p.body),
            (self._args, p.args),
            (self._env, p.env),
            (self._tfmap, p.timeframe_map),
            (self._columns, p.columns),
            (self._arguments, p.mcp_arguments),
        ):
            widget.setText(json.dumps(value, ensure_ascii=False))
        self._command.setText(p.command)
        self._method.setCurrentText(p.method)
        self._tool.clear()
        self._tool.setEditText(p.mcp_tool)
        self._schema.clear()
        self._tools = []
        self._symbol.setText(p.symbol)
        self._tf.setCurrentText(p.timeframe)
        self._rows_path.setText(p.rows_path)
        self._limit.setValue(p.max_bars)
        self._poll.setValue(p.poll_seconds)
        self._timeout.setValue(p.timeout_seconds)
        self._visibility()

    def _visibility(self):
        transport = self._transport.currentData()
        self._csv_group.setVisible(transport == "csv")
        self._http_group.setVisible(transport not in ("csv", "mcp_stdio"))
        self._stdio_group.setVisible(transport == "mcp_stdio")
        self._mcp_group.setVisible(transport.startswith("mcp_"))
        self._market_group.setVisible(self._category.currentData() == "market")

    def _browse_csv(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择行情 CSV", "", "CSV (*.csv)")
        if path:
            self._csv.setText(path)

    def _read_profile(self):
        try:
            headers = json.loads(self._headers.text())
            env_headers = json.loads(self._env_headers.text())
            if self._auth_value.text():
                key = self._auth_header.text().strip()
                if not key:
                    raise ValueError
                headers[key] = self._auth_value.text().strip()
                env_headers.pop(key, None)
            kwargs = dict(
                name=self._name.text().strip(),
                category=self._category.currentData(),
                transport=self._transport.currentData(),
                url=self._url.text().strip(),
                headers=headers,
                env_headers=env_headers,
                method=self._method.currentText(),
                body=json.loads(self._body.text()),
                command=self._command.text().strip(),
                args=json.loads(self._args.text()),
                env=json.loads(self._env.text()),
                csv_path=self._csv.text().strip(),
                symbol=self._symbol.text().strip(),
                timeframe=self._tf.currentText(),
                timeframe_map=json.loads(self._tfmap.text()),
                columns=json.loads(self._columns.text()),
                rows_path=self._rows_path.text().strip(),
                max_bars=self._limit.value(),
                poll_seconds=self._poll.value(),
                timeout_seconds=self._timeout.value(),
                mcp_tool=self._tool.currentText().strip(),
                mcp_arguments=json.loads(self._arguments.text()),
                description=self._description.text(),
            )
            if self.profile:
                kwargs.update(id=self.profile.id, enabled=self.profile.enabled)
            profile = SourceProfile(**kwargs)
        except (ValueError, TypeError):
            raise ValueError(
                "请检查名称及 JSON 字段的格式；请求头/参数使用对象，程序参数使用数组。"
            ) from None
        validate_profile(profile)
        return profile

    def _save(self):
        try:
            self.profile = self._read_profile()
        except ValueError as exc:
            self._preview.setPlainText(str(exc))
            return
        self.accept()

    def _start(self, action, on_result):
        if self._worker:
            return
        worker = self._worker = ConnectionWorker(action, self)
        worker.completed.connect(on_result)
        worker.failed.connect(self._preview.setPlainText)
        worker.finished.connect(self._done)
        worker.finished.connect(worker.deleteLater)
        self._test.setEnabled(False)
        self._discover.setEnabled(False)
        self._buttons.setEnabled(False)
        self._template.setEnabled(False)
        self._scroll.setEnabled(False)
        self._preview.setPlainText("正在请求，请稍候…")
        worker.start()

    def _done(self):
        self._worker = None
        self._test.setEnabled(True)
        self._discover.setEnabled(True)
        self._buttons.setEnabled(True)
        self._template.setEnabled(True)
        self._scroll.setEnabled(True)

    def _discover_tools(self):
        try:
            profile = self._read_profile()
        except ValueError as exc:
            self._preview.setPlainText(str(exc))
            return
        self._start(lambda: mcp_request(profile, discover=True), self._show_tools)

    def _show_tools(self, tools):
        current = self._tool.currentText()
        self._tools = tools
        self._tool.clear()
        self._tool.addItems([tool["name"] for tool in tools])
        if self._tool.findText(current) >= 0:
            self._tool.setCurrentText(current)
        self._show_schema()
        self._preview.setPlainText(
            f"连接成功：发现 {len(tools)} 个工具。选择工具后查看参数说明，点击测试读取。"
        )

    def _show_schema(self):
        tool = next(
            (tool for tool in self._tools if tool["name"] == self._tool.currentText()), None
        )
        if tool:
            self._schema.setPlainText(
                tool.get("description", "")
                + "\n"
                + json.dumps(tool.get("inputSchema", {}), ensure_ascii=False, indent=2)
            )
        return tool

    def _tool_selected(self):
        tool = self._show_schema()
        if not tool:
            return
        schema = tool.get("inputSchema", {})
        arguments = {}
        for key in schema.get("required", []):
            spec = schema.get("properties", {}).get(key, {})
            if key in ("query", "keyword", "keywords", "question"):
                arguments[key] = "{query}"
            elif key in ("symbol", "code", "instId"):
                arguments[key] = "{symbol}"
            elif key in ("timeframe", "interval"):
                arguments[key] = "{timeframe}"
            else:
                arguments[key] = spec.get("default", 20 if spec.get("type") == "integer" else "")
        self._arguments.setText(json.dumps(arguments, ensure_ascii=False))

    def _test_connection(self):
        try:
            profile = self._read_profile()
        except ValueError as exc:
            self._preview.setPlainText(str(exc))
            return
        query = self._query.text()

        def run():
            if profile.category == "information":
                from pa_agent.data.information import query_information

                return query_information(profile, query, profile.symbol, profile.timeframe)
            from pa_agent.data.configured_source import ConfiguredSource

            source = ConfiguredSource(profile)
            try:
                source.connect()
                source.subscribe(profile.symbol, profile.timeframe)
                bars = source.latest_snapshot(100)
                return {
                    "结果": "行情读取成功",
                    "品种": profile.symbol,
                    "周期": profile.timeframe,
                    "根数": len(bars),
                    "最新收盘价": bars[0].close,
                    "最新K线已收盘": bars[0].closed,
                }
            finally:
                source.disconnect()

        self._start(
            run,
            lambda result: self._preview.setPlainText(
                json.dumps(result, ensure_ascii=False, indent=2)[:9000]
            ),
        )

    def reject(self):
        if not self._worker:
            super().reject()

    def closeEvent(self, event):
        if self._worker:
            event.ignore()
        else:
            super().closeEvent(event)


class DataSourcesDialog(QDialog):
    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.setWindowTitle("管理数据源 · 行情与资讯")
        self.resize(780, 620)
        self._settings = settings
        self._draft = settings.model_copy(deep=True)
        last = self._draft.general.last_data_source
        if last in dict(BUILTIN_SOURCES) and last not in self._draft.data_sources.enabled_builtins:
            self._draft.data_sources.enabled_builtins.append(last)
        root = QVBoxLayout(self)
        note = QLabel("勾选启用的数据源。行情出现在主界面下拉框，资讯在「资讯与 MCP」中查询。")
        note.setWordWrap(True)
        root.addWidget(note)
        self._list = QListWidget()
        root.addWidget(self._list, 1)
        self._list.itemDoubleClicked.connect(self._edit)
        row = QHBoxLayout()
        for text, action in (
            ("新增数据源…", self._add),
            ("编辑…", self._edit),
            ("移除", self._remove),
            ("从 Codex 导入 MCP…", self._import_codex),
            ("导入 MCP 配置文件…", self._import_file),
        ):
            button = QPushButton(text)
            button.clicked.connect(action)
            row.addWidget(button)
        root.addLayout(row)
        credentials = QGroupBox("内置服务授权（已有配置会保留）")
        form = QFormLayout(credentials)
        self._tv_user = QLineEdit(settings.tradingview.username)
        self._tv_password = QLineEdit(settings.tradingview.password)
        self._tv_password.setEchoMode(QLineEdit.EchoMode.Password)
        self._tushare = QLineEdit(settings.tushare.token)
        self._tushare.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow("TradingView 用户名", self._tv_user)
        form.addRow("TradingView 密码", self._tv_password)
        form.addRow("Tushare Token", self._tushare)
        root.addWidget(credentials)
        self._status = QLabel("")
        self._status.setWordWrap(True)
        root.addWidget(self._status)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("保存并应用")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)
        self._populate()

    def _populate(self, selected=None):
        self._list.clear()
        for key, name in BUILTIN_SOURCES:
            item = QListWidgetItem("行情 · " + name)
            item.setData(Qt.ItemDataRole.UserRole, key)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            enabled = key in self._draft.data_sources.enabled_builtins
            item.setCheckState(Qt.CheckState.Checked if enabled else Qt.CheckState.Unchecked)
            self._list.addItem(item)
        for profile in self._draft.data_sources.profiles:
            item = QListWidgetItem(
                f"{'行情' if profile.category == 'market' else '资讯'} · {profile.name} · {profile.transport}"
            )
            item.setData(Qt.ItemDataRole.UserRole, profile.id)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(
                Qt.CheckState.Checked if profile.enabled else Qt.CheckState.Unchecked
            )
            self._list.addItem(item)
            if profile.id == selected:
                self._list.setCurrentItem(item)

    def _collect_checks(self):
        enabled = set()
        for index in range(self._list.count()):
            item = self._list.item(index)
            if item.checkState() == Qt.CheckState.Checked:
                enabled.add(item.data(Qt.ItemDataRole.UserRole))
        self._draft.data_sources.enabled_builtins = [
            key for key, _ in BUILTIN_SOURCES if key in enabled
        ]
        for profile in self._draft.data_sources.profiles:
            profile.enabled = profile.id in enabled

    def _add(self):
        dialog = SourceEditor(parent=self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._collect_checks()
            self._draft.data_sources.profiles.append(dialog.profile)
            self._populate(dialog.profile.id)

    def _edit(self, *_):
        item = self._list.currentItem()
        if not item:
            return
        key = item.data(Qt.ItemDataRole.UserRole)
        profile = next((p for p in self._draft.data_sources.profiles if p.id == key), None)
        if not profile:
            self._status.setText(
                "内置行情：勾选即可启用；TradingView 和 Tushare 的授权信息在下方填写。"
            )
            return
        self._collect_checks()
        dialog = SourceEditor(profile, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._draft.data_sources.profiles = [
                dialog.profile if p.id == key else p for p in self._draft.data_sources.profiles
            ]
            self._populate(key)

    def _remove(self):
        item = self._list.currentItem()
        if not item:
            return
        key = item.data(Qt.ItemDataRole.UserRole)
        if not key.startswith("custom:"):
            item.setCheckState(Qt.CheckState.Unchecked)
            return
        self._collect_checks()
        self._draft.data_sources.profiles = [
            p for p in self._draft.data_sources.profiles if p.id != key
        ]
        self._populate()

    def _import_codex(self):
        self._import(Path.home() / ".codex" / "config.toml")

    def _import_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "导入 MCP 配置", "", "MCP 配置 (*.json *.toml)")
        if path:
            self._import(Path(path))

    def _import(self, path):
        try:
            profiles = import_mcp_config(path)
        except (ValueError, OSError):
            self._status.setText(
                "无法读取配置，请选择包含 mcpServers 或 mcp_servers 的 JSON / TOML 文件。"
            )
            return
        if not profiles:
            self._status.setText("该文件没有可导入的 MCP 服务。")
            return
        name, ok = QInputDialog.getItem(
            self, "选择 MCP 服务", "选择要导入的服务", [p.name for p in profiles], 0, False
        )
        if ok:
            profile = next(p for p in profiles if p.name == name)
            dialog = SourceEditor(profile, self)
            if dialog.exec() == QDialog.DialogCode.Accepted:
                self._collect_checks()
                self._draft.data_sources.profiles.append(dialog.profile)
                self._populate(dialog.profile.id)

    def _save(self):
        self._collect_checks()
        market_ids = list(self._draft.data_sources.enabled_builtins)
        market_ids += [
            p.id for p in self._draft.data_sources.profiles if p.enabled and p.category == "market"
        ]
        if not market_ids:
            self._status.setText("请至少保留一个行情数据源。")
            return
        self._draft.tradingview.username = self._tv_user.text().strip()
        self._draft.tradingview.password = self._tv_password.text()
        self._draft.tushare.token = self._tushare.text().strip()
        if self._draft.general.last_data_source not in market_ids:
            self._draft.general.last_data_source = market_ids[0]
            from pa_agent.data.factory import default_symbol_for_kind, source_profile

            self._draft.general.last_symbol = default_symbol_for_kind(market_ids[0], self._draft)
            profile = source_profile(market_ids[0], self._draft)
            self._draft.general.last_timeframe = profile.timeframe if profile else "15m"
        try:
            save_settings(self._draft)
        except OSError:
            self._status.setText("设置保存失败，请检查数据目录是否可写。")
            return
        self._settings.data_sources = self._draft.data_sources
        self._settings.tradingview = self._draft.tradingview
        self._settings.tushare = self._draft.tushare
        self._settings.general = self._draft.general
        self.accept()
