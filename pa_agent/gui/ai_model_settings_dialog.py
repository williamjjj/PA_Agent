"""AI 模型设置对话框 — 只包含 AI 提供商相关字段."""
from __future__ import annotations

from typing import Any

from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from pa_agent.config.settings import Settings, save_settings
from pa_agent.config.paths import SETTINGS_JSON_PATH
from pa_agent.ai.qclaw_connector import (
    detect_qclaw,
    is_openclaw_model,
    should_use_qclaw_provider,
)
from pa_agent.ai.workbuddy_connector import (
    detect_workbuddy,
    is_openclaw_wb_model,
    should_use_workbuddy_provider,
)
from pa_agent.ai.trae_connector import (
    detect_trae_cn,
    is_openclaw_twc_model,
    should_use_trae_cn_provider,
)
from pa_agent.ai.qoder_connector import (
    detect_qoder_cn,
    is_openclaw_qc_model,
    should_use_qoder_cn_provider,
)

_API_KEY_HELP_URL = "https://my.feishu.cn/wiki/CUV1wUKWxiQGhekQdRvcZQQ2ncf"
_AGENT_TUTORIAL_URL = (
    "https://my.feishu.cn/wiki/BEdFwGJhaiATbukuD2HccSXCnrb?from=from_copylink"
)


class AIModelSettingsDialog(QDialog):
    """AI 模型 / 提供商配置对话框."""

    def __init__(self, settings: Settings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("AI 模型设置")
        self.setMinimumWidth(520)
        self._settings = settings
        self._harness_profiles = {k:dict(v) for k,v in settings.provider.harness_profiles.items()}
        self._previous_backend = None
        self._setup_ui()
        self._load_values()

    # ── UI ────────────────────────────────────────────────────────────────────

    def _setup_ui(self) -> None:
        root = QVBoxLayout(self)

        provider_group = QGroupBox("AI 提供商")
        form = QFormLayout(provider_group)

        self._backend_combo = QComboBox()
        for title, value in (("API 接口", "api"), ("本地 Codex（使用现有登录）", "codex"),
                             ("本地 WorkBuddy / CodeBuddy CLI", "workbuddy"),
                             ("本地 Hermes（使用现有配置）", "hermes")):
            self._backend_combo.addItem(title, value)
        form.addRow("分析方式：", self._backend_combo)
        self._harness_path = QLineEdit()
        self._harness_path.setPlaceholderText("自动查找；也可指定已安装的 CLI 入口")
        local_row = QHBoxLayout()
        local_row.addWidget(self._harness_path)
        browse = QPushButton("选择文件")
        browse.clicked.connect(self._browse_harness)
        local_row.addWidget(browse)
        self._harness_group = QGroupBox("本地 agent")
        harness_form = QFormLayout(self._harness_group)
        harness_form.addRow("程序：", local_row)
        self._harness_model = QLineEdit()
        self._harness_model.setPlaceholderText("留空使用 harness 自己的默认模型")
        harness_form.addRow("模型（可选）：", self._harness_model)
        self._codex_speed = QComboBox()
        for label, value in (("跟随 Codex 设置", "follow"), ("标准", "standard"),
                             ("Fast（更快，消耗更多额度）", "fast")):
            self._codex_speed.addItem(label, value)
        self._codex_speed.setToolTip("仅调整 Codex 的服务速度，保留实际模型和推理强度。Fast 的可用性及额度消耗以 Codex 账户为准。")
        harness_form.addRow("Codex 速度：", self._codex_speed)
        self._harness_form = harness_form
        self._harness_config_dir = QLineEdit()
        self._harness_config_dir.setPlaceholderText("WorkBuddy / CodeBuddy 专用配置目录；通常留空")
        harness_form.addRow("配置目录（可选）：", self._harness_config_dir)
        self._harness_status = QLabel()
        self._harness_status.setWordWrap(True)
        harness_form.addRow(self._harness_status)
        form.addRow(self._harness_group)
        self._provider_form = form

        self._model_edit = QLineEdit()
        form.addRow("模型 (model):", self._model_edit)

        self._base_url_edit = QLineEdit()
        form.addRow("Base URL:", self._base_url_edit)

        api_key_row = QHBoxLayout()
        self._api_key_row = api_key_row
        self._api_key_edit = QLineEdit()
        self._api_key_edit.setEchoMode(QLineEdit.EchoMode.Normal)
        self._api_key_edit.setPlaceholderText("输入 API Key")
        api_key_row.addWidget(self._api_key_edit)
        self._show_key_btn = QPushButton("隐藏")
        self._show_key_btn.setCheckable(True)
        self._show_key_btn.setFixedWidth(52)
        self._show_key_btn.toggled.connect(self._toggle_api_key_visibility)
        api_key_row.addWidget(self._show_key_btn)
        form.addRow("API Key:", api_key_row)

        # GLM 备用通道 Key：仅当主通道是 SenseNova 网关时显示。
        self._glm_key_label = QLabel("GLM 备用 Key:")
        self._glm_key_edit = QLineEdit()
        self._glm_key_edit.setPlaceholderText("输入 GLM Key")
        self._glm_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow(self._glm_key_label, self._glm_key_edit)
        self._base_url_edit.textChanged.connect(self._refresh_glm_key_row)

        self._thinking_check = QCheckBox("启用 Thinking")
        form.addRow("Thinking:", self._thinking_check)

        self._reasoning_effort_combo = QComboBox()
        self._reasoning_effort_combo.addItems(["low", "medium", "high", "max"])
        form.addRow("Reasoning Effort:", self._reasoning_effort_combo)

        self._api_key_help_btn = QPushButton("小白点这里！获取程序无限Token，无限分析")
        self._api_key_help_btn.setStyleSheet(
            "QPushButton { font-size: 13pt; font-weight: bold; "
            "padding: 8px 16px; }"
        )
        self._api_key_help_btn.clicked.connect(self._show_unlimited_token_info)
        form.addRow("", self._api_key_help_btn)

        self._agent_tutorial_btn = QPushButton("智能体使用教程及问题解决方法")
        self._agent_tutorial_btn.setToolTip(_AGENT_TUTORIAL_URL)
        self._agent_tutorial_btn.clicked.connect(self._open_agent_tutorial_url)
        form.addRow("", self._agent_tutorial_btn)

        self._backend_combo.currentIndexChanged.connect(self._backend_selected)
        self._harness_path.textChanged.connect(self._refresh_backend)

        root.addWidget(provider_group)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        save_btn = buttons.button(QDialogButtonBox.StandardButton.Save)
        if save_btn:
            save_btn.setText("保存")
        cancel_btn = buttons.button(QDialogButtonBox.StandardButton.Cancel)
        if cancel_btn:
            cancel_btn.setText("取消")
        buttons.accepted.connect(self._on_save)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    # ── 加载 / 保存 ────────────────────────────────────────────────────────────

    def _load_values(self) -> None:
        p = self._settings.provider
        self._backend_combo.blockSignals(True)
        self._backend_combo.setCurrentIndex(self._backend_combo.findData(p.backend))
        self._harness_path.setText(p.harness_executable)
        self._harness_model.setText(p.harness_model)
        self._codex_speed.setCurrentIndex(self._codex_speed.findData(p.codex_speed))
        self._harness_config_dir.setText(p.harness_config_dir)
        self._previous_backend = p.backend
        self._backend_combo.blockSignals(False)
        self._model_edit.setText(p.model)
        self._base_url_edit.setText(p.base_url)
        self._api_key_edit.setText(p.api_key)
        self._glm_key_edit.setText(str(getattr(p, "backup_glm_api_key", "") or ""))
        self._refresh_glm_key_row()
        self._thinking_check.setChecked(p.thinking)
        idx = self._reasoning_effort_combo.findText(p.reasoning_effort)
        if idx >= 0:
            self._reasoning_effort_combo.setCurrentIndex(idx)
        self._refresh_backend()

    def _browse_harness(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "选择本地 agent 的 CLI 入口")
        if path:
            self._harness_path.setText(path)

    def _remember_harness(self) -> None:
        if self._previous_backend and self._previous_backend != "api":
            self._harness_profiles[self._previous_backend] = {
                "executable":self._harness_path.text().strip(),
                "model":self._harness_model.text().strip(),
                "config_dir":self._harness_config_dir.text().strip(),
            }

    def _backend_selected(self) -> None:
        self._remember_harness()
        backend = self._backend_combo.currentData()
        profile = self._harness_profiles.get(backend,{})
        self._previous_backend = backend
        self._harness_path.setText(profile.get("executable",""))
        self._harness_model.setText(profile.get("model",""))
        self._harness_config_dir.setText(profile.get("config_dir",""))
        self._refresh_backend()

    def _refresh_backend(self) -> None:
        from pa_agent.ai.local_harness import discover_harness
        backend = self._backend_combo.currentData()
        local = backend != "api"
        self._harness_group.setVisible(local)
        self._harness_form.setRowVisible(self._codex_speed, backend == "codex")
        for field in (self._model_edit, self._base_url_edit, self._api_key_edit,
                      self._show_key_btn, self._thinking_check, self._reasoning_effort_combo,
                      self._api_key_help_btn, self._agent_tutorial_btn):
            field.setEnabled(not local)
        for row in (self._model_edit, self._base_url_edit, self._api_key_row,
                    self._thinking_check, self._reasoning_effort_combo,
                    self._api_key_help_btn, self._agent_tutorial_btn):
            self._provider_form.setRowVisible(row, not local)
        if local:
            path = discover_harness(backend, self._harness_path.text().strip())
            self._harness_status.setToolTip(str(path) if path else "")
            self._harness_status.setText(
                ("已找到 CLI 程序；登录状态需实际调用确认。\n" if path else "尚未找到 CLI，请选择程序入口。\n")
                + "无需在 PA Agent 填写 API。请先在对应 agent 中登录并完成模型配置。"
                + "\n本地调用仍遵循该账户的网络要求和额度；这里只检测安装路径。"
            )
        self._refresh_glm_key_row()

    def _on_save(self) -> None:
        p = self._settings.provider
        backend = self._backend_combo.currentData()
        self._remember_harness()
        p.harness_profiles = self._harness_profiles
        p.codex_speed = self._codex_speed.currentData()
        if backend != "api":
            from pa_agent.ai.local_harness import discover_harness
            path = self._harness_path.text().strip()
            if discover_harness(backend, path) is None:
                QMessageBox.warning(self, "未找到本地 agent", "请先安装对应 CLI，或选择它的程序入口。")
                return
            p.backend = backend
            p.harness_executable = path
            p.harness_model = self._harness_model.text().strip()
            p.harness_config_dir = self._harness_config_dir.text().strip()
            self._remember_harness()
            p.harness_profiles = self._harness_profiles
            save_settings(self._settings, SETTINGS_JSON_PATH)
            self.accept()
            return
        p.backend = "api"
        model = self._model_edit.text().strip()
        base_url = self._base_url_edit.text().strip()
        api_key = self._api_key_edit.text().strip()

        # Explicit model aliases win over stale base_url (openclaw_wb before openclaw).
        if is_openclaw_wb_model(model) or should_use_workbuddy_provider(model, base_url):
            p.api_key = api_key
            err = self._apply_workbuddy_provider(preferred_model=model)
            if err:
                QMessageBox.warning(self, "WorkBuddy 配置异常", err)
                return
        elif is_openclaw_twc_model(model) or should_use_trae_cn_provider(model, base_url):
            p.api_key = api_key
            err = self._apply_trae_cn_provider(preferred_model=model)
            if err:
                QMessageBox.warning(self, "TRAE Work CN 配置异常", err)
                return
        elif is_openclaw_qc_model(model) or should_use_qoder_cn_provider(model, base_url):
            p.api_key = api_key
            err = self._apply_qoder_cn_provider(preferred_model=model)
            if err:
                QMessageBox.warning(self, "Qoder CN 配置异常", err)
                return
        elif is_openclaw_model(model) or should_use_qclaw_provider(model, base_url):
            p.api_key = api_key
            err = self._apply_qclaw_provider(preferred_model=model)
            if err:
                QMessageBox.warning(self, "QClaw 配置异常", err)
                return
        else:
            field_err = self._validate_provider_fields(model, base_url)
            if field_err:
                QMessageBox.warning(self, "AI 提供商配置有误", field_err)
                return
            p.model = model
            p.base_url = base_url
            p.api_key = api_key

        p.thinking = self._thinking_check.isChecked()
        p.reasoning_effort = self._reasoning_effort_combo.currentText()  # type: ignore[assignment]

        self._apply_glm_backup_key(p)

        save_settings(self._settings, SETTINGS_JSON_PATH)
        self.accept()

    def _refresh_glm_key_row(self) -> None:
        """GLM 备用 Key 只在主通道为 SenseNova 网关时有意义。"""
        from pa_agent.ai.glm_backup import is_sensenova_base

        visible = (self._backend_combo.currentData() == "api"
                   and is_sensenova_base(self._base_url_edit.text().strip()))
        self._glm_key_label.setVisible(visible)
        self._glm_key_edit.setVisible(visible)

    def _apply_glm_backup_key(self, p: Any) -> None:
        """保存 GLM Key，并同步维护 fallbacks 里的 glm-4-flash 条目。"""
        from pa_agent.ai.glm_backup import GLM_BASE_URL, GLM_MODEL, ensure_glm_fallback

        p.backup_glm_api_key = self._glm_key_edit.text().strip()
        if p.backup_glm_api_key:
            ensure_glm_fallback(p)
            return
        p.fallbacks = [
            fb for fb in (getattr(p, "fallbacks", None) or [])
            if not (str(getattr(fb, "model", "")) == GLM_MODEL
                    and str(getattr(fb, "base_url", "")) == GLM_BASE_URL)
        ]

    # ── 辅助 ──────────────────────────────────────────────────────────────────

    def focus_api_key_field(self) -> None:
        self._api_key_edit.setFocus(Qt.FocusReason.OtherFocusReason)
        self._api_key_edit.selectAll()

    def _toggle_api_key_visibility(self, checked: bool) -> None:
        if checked:
            self._api_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
            self._show_key_btn.setText("显示")
        else:
            self._api_key_edit.setEchoMode(QLineEdit.EchoMode.Normal)
            self._show_key_btn.setText("隐藏")

    def _apply_qclaw_provider(self, *, preferred_model: str = "") -> str | None:
        from pa_agent.ai.qclaw_connector import apply_qclaw_provider_to_settings
        return apply_qclaw_provider_to_settings(self._settings, preferred_model=preferred_model or None)

    def _apply_workbuddy_provider(self, *, preferred_model: str = "") -> str | None:
        from pa_agent.ai.workbuddy_connector import apply_workbuddy_provider_to_settings
        return apply_workbuddy_provider_to_settings(self._settings, preferred_model=preferred_model or None)

    def _apply_trae_cn_provider(self, *, preferred_model: str = "") -> str | None:
        from pa_agent.ai.trae_connector import apply_trae_cn_provider_to_settings
        return apply_trae_cn_provider_to_settings(self._settings, preferred_model=preferred_model or None)

    def _apply_qoder_cn_provider(self, *, preferred_model: str = "") -> str | None:
        from pa_agent.ai.qoder_connector import apply_qoder_cn_provider_to_settings
        return apply_qoder_cn_provider_to_settings(self._settings, preferred_model=preferred_model or None)

    @staticmethod
    def _validate_provider_fields(model: str, base_url: str) -> str | None:
        if is_openclaw_model(model) or should_use_qclaw_provider(model, base_url):
            return None
        if is_openclaw_wb_model(model) or should_use_workbuddy_provider(model, base_url):
            return None
        if is_openclaw_twc_model(model) or should_use_trae_cn_provider(model, base_url):
            return None
        if is_openclaw_qc_model(model) or should_use_qoder_cn_provider(model, base_url):
            return None
        if model.startswith(("http://", "https://")) and not base_url.startswith(("http://", "https://")):
            return (
                "「模型」与「Base URL」似乎填反了：\n"
                "• 模型应填模型名，如 deepseek-v4-pro 或 claude-sonnet-4-6\n"
                "• 使用 QClaw 时模型填 openclaw（或 openclaw/main）\n"
                "• 使用 WorkBuddy 时模型填 openclaw_wb\n"
                "• 使用 TRAE Work CN 时模型填 openclaw_twc\n"
                "• 使用 Qoder CN 时模型填 openclaw_qc\n"
                "• Base URL 应填接口地址，如 https://api.deepseek.com"
            )
        if base_url.startswith(("http://", "https://")):
            return None
        if not base_url:
            if detect_qclaw():
                return (
                    "请填写 Base URL，或使用 QClaw/WorkBuddy/TRAE Work CN/Qoder CN：\n"
                    "• 模型填 openclaw → QClaw\n"
                    "• 模型填 openclaw_wb → WorkBuddy\n"
                    "• 模型填 openclaw_twc → TRAE Work CN\n"
                    "• 模型填 openclaw_qc → Qoder CN"
                )
            if detect_workbuddy():
                return (
                    "请填写 Base URL，或使用 WorkBuddy/TRAE Work CN/Qoder CN：\n"
                    "• 模型填 openclaw_wb → WorkBuddy\n"
                    "• 模型填 openclaw_twc → TRAE Work CN\n"
                    "• 模型填 openclaw_qc → Qoder CN"
                )
            if detect_trae_cn():
                return (
                    "请填写 Base URL，或使用 TRAE Work CN/Qoder CN：\n"
                    "• 模型填 openclaw_twc → TRAE Work CN（保存时自动配置）\n"
                    "• 模型填 openclaw_qc → Qoder CN（保存时自动配置）"
                )
            if detect_qoder_cn():
                return (
                    "请填写 Base URL，或使用 Qoder CN：\n"
                    "• 模型填 openclaw_qc → Qoder CN（保存时自动配置）"
                )
            return "请填写 Base URL（API 接口地址）。"
        return (
            f"Base URL 不是有效网址（当前：{base_url}）。\n"
            "DeepSeek 示例：https://api.deepseek.com\n"
            "PackyAPI 示例：https://www.packyapi.com/v1\n"
            "QClaw：模型填 openclaw 后点保存（自动配置本地网关）\n"
            "WorkBuddy：模型填 openclaw_wb 后点保存（自动配置 WorkBuddy）\n"
            "TRAE Work CN：模型填 openclaw_twc 后点保存（自动配置 TRAE Work CN）\n"
            "Qoder CN：模型填 openclaw_qc 后点保存（自动配置 Qoder CN）"
        )

    def _show_unlimited_token_info(self) -> None:
        from PyQt6.QtWidgets import QDialog as _QDialog
        dlg = _QDialog(self)
        dlg.setWindowTitle("获取无限Token")
        from PyQt6.QtWidgets import QVBoxLayout as _VBox, QDialogButtonBox as _DBB
        layout = _VBox(dlg)
        label = QLabel(
            "获取无限Token方法需付费49.9元，付费后你将获得<br>"
            "Deepseek V4 Pro/GLM5.1/Kimi2.6等\"满血\"模型的无限分析方法<br>"
            "注意无限Token只支持使用这个分析软件<br>"
            "如果你愿意付费，请联系QQ：564020069（付费后提供远程协助部署安装服务）<br><br>"
            "如果你不愿意付费，你可以用自己的模型api，如果你不知道模型api是什么<br>"
            "可以直接跟龙虾说：<br>"
            "PA_Agent这个程序的模型api有什么作用，该怎么填？<br>"
            "请教我填上Deepseek官方的模型API接口"
        )
        label.setStyleSheet("font-size: 22pt;")
        layout.addWidget(label)
        bb = _DBB(_DBB.StandardButton.Ok)
        bb.accepted.connect(dlg.accept)
        layout.addWidget(bb)
        dlg.exec()

    def _open_agent_tutorial_url(self) -> None:
        QDesktopServices.openUrl(QUrl(_AGENT_TUTORIAL_URL))
