"""Validated per-account preferences; secrets are never returned to the browser."""
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from pa_agent.config.settings import PromptSettings, Settings, ValidationSettings

PROMPTS = Path(__file__).resolve().parents[2] / "prompt_engineering"
SECRET_FIELDS = ("api_key", "tradingview_password", "tushare_token", "feishu_webhook", "feishu_secret", "pushplus_token")


class WebSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    base_url: str = Field(default="https://api.deepseek.com/v1", max_length=300)
    model: str = Field(default="deepseek-chat", min_length=1, max_length=120)
    api_key: str = Field(default="", max_length=4096)
    thinking: bool = False
    reasoning_effort: Literal["low", "medium", "high", "max"] = "high"
    context_window: int = Field(default=128000, ge=8192, le=2000000)
    max_tokens: int = Field(default=16000, ge=1024, le=64000)
    decision_stance: Literal["conservative", "balanced", "aggressive", "extreme_aggressive"] = "conservative"
    enable_next_bar_prediction: bool = True
    incremental_max_new_bars: int = Field(default=10, ge=0, le=50)
    analysis_bar_count: int = Field(default=100, ge=50, le=500)
    decision_confidence_threshold: int = Field(default=40, ge=0, le=100)
    structure_flip_cooldown_bars: int = Field(default=3, ge=1, le=50)
    cancel_keep_analysis_on_retry: bool = True
    prompt: PromptSettings = Field(default_factory=PromptSettings)
    validation: ValidationSettings = Field(default_factory=lambda: ValidationSettings(retry_max=1))
    tradingview_username: str = Field(default="", max_length=100)
    tradingview_password: str = Field(default="", max_length=256)
    tushare_token: str = Field(default="", max_length=256)
    kline_adjust: Literal["qfq", "hfq", "none"] = "qfq"
    prompt_overrides: dict[str, str] = Field(default_factory=dict)
    notify_enabled: bool = False
    notify_on_order_only: bool = True
    feishu_webhook: str = Field(default="", max_length=300)
    feishu_secret: str = Field(default="", max_length=256)
    pushplus_token: str = Field(default="", max_length=256)

    @field_validator("base_url")
    @classmethod
    def https_url(cls, value):
        value = value.strip().rstrip("/")
        parsed = urlsplit(value)
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
                or parsed.query or parsed.fragment or parsed.port not in (None, 443)):
            raise ValueError("模型地址须为 HTTPS，不可包含账号、查询参数或自定义端口。")
        return value

    @field_validator("api_key")
    @classmethod
    def valid_header_key(cls, value):
        if value and any(ord(c) < 33 or ord(c) > 126 for c in value):
            raise ValueError("API Key 不可包含空白、换行或非 ASCII 字符。")
        return value

    @field_validator("model")
    @classmethod
    def api_model(cls, value):
        if any(c.isspace() for c in value) or value.lower().startswith(("openclaw:", "cursor:", "trae:", "qoder:")):
            raise ValueError("请填写 HTTP 模型 API 的模型标识。")
        return value

    @field_validator("feishu_webhook")
    @classmethod
    def feishu_url(cls, value):
        if value:
            p = urlsplit(value)
            if p.scheme != "https" or p.hostname != "open.feishu.cn" or not p.path.startswith("/open-apis/bot/v2/hook/") or p.query or p.fragment or p.username or p.port:
                raise ValueError("请输入飞书自定义机器人 HTTPS Webhook。")
        return value

    @model_validator(mode="after")
    def validate_prompts(self):
        allowed = {p.name for p in PROMPTS.glob("*.txt")}
        if set(self.prompt_overrides) - allowed:
            raise ValueError("提示词文件不存在。")
        if sum(len(x) for x in self.prompt_overrides.values()) > 200000:
            raise ValueError("自定义提示词总长不可超过 200000 字符。")
        if any(not v.strip() for v in self.prompt_overrides.values()):
            raise ValueError("自定义提示词不可为空；删除覆盖项可恢复默认。")
        return self

    def public(self):
        data = self.model_dump()
        for name in SECRET_FIELDS:
            data[name + "_configured"] = bool(data.pop(name))
        return data

    def core(self):
        return Settings(
            provider={"base_url": self.base_url, "model": self.model, "api_key": self.api_key,
                      "thinking": self.thinking, "reasoning_effort": self.reasoning_effort,
                      "context_window": self.context_window},
            general={name: getattr(self, name) for name in (
                "decision_stance", "enable_next_bar_prediction", "incremental_max_new_bars",
                "analysis_bar_count", "decision_confidence_threshold", "structure_flip_cooldown_bars",
                "cancel_keep_analysis_on_retry", "kline_adjust")},
            prompt=self.prompt, validation=self.validation,
            tushare={"token": self.tushare_token}, feishu={"enabled": False},
        )


def merge_settings(current, changes, clear):
    if set(clear) - set(SECRET_FIELDS):
        raise ValueError("无效的凭据名称。")
    current = dict(current)
    for key, value in changes.items():
        if key in SECRET_FIELDS and not value:
            continue
        current[key] = value
    for key in clear:
        current[key] = ""
    return WebSettings.model_validate(current)
