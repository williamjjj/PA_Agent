"""Portable, self-contained exports of saved analysis evidence."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import re
import tempfile
import zipfile
from datetime import datetime
from html import escape
from pathlib import Path

SECTIONS = {
    "summary": "最终结论与风险说明",
    "input": "分析输入：K线与上级周期资料",
    "stage1": "阶段一：市场诊断与判定节点",
    "routing": "策略选择与参考经验",
    "stage2": "阶段二：决策、路径与走势预期",
    "information": "外部资讯与来源",
    "followups": "分析后的追问与回答",
    "technical": "提示词、模型返回、重试与校验记录",
}
FORMAT_VERSION = "pa-analysis-export-v1"
_SECRET_KEYS = {
    "apikey",
    "apikeyencrypted",
    "authorization",
    "password",
    "secret",
    "token",
    "accesstoken",
    "refreshtoken",
    "cookie",
    "setcookie",
    "emapikey",
    "xapikey",
}
_LABELS = {
    "decision": "交易决策",
    "order_type": "结论",
    "direction": "方向",
    "reasoning": "判断依据",
    "diagnosis_summary": "市场诊断摘要",
    "risk_assessment": "风险评估",
    "entry": "入场价",
    "stop_loss": "止损",
    "take_profit": "止盈",
    "trade_confidence": "决策置信度",
    "cycle_position": "市场周期",
    "detected_patterns": "已识别形态",
    "gate_trace": "诊断判定节点",
    "decision_trace": "决策路径",
    "terminal": "最终节点",
    "bar_by_bar": "逐根K线判定",
    "next_bar_prediction": "下根K线预期",
    "next_cycle_prediction": "下一市场周期预期",
    "watch_points": "后续观察事项",
    "risk": "风险",
    "reason": "理由",
    "question": "判定问题",
    "answer": "判定结果",
    "node_id": "节点",
    "source": "来源",
    "captured_at": "查询时间",
    "content": "内容",
    "query": "查询问题",
    "symbol": "品种",
    "timeframe": "周期",
    "filename": "文件名",
    "exception": "异常信息",
    "usage_total": "用量记录",
    "bullish": "偏多",
    "bearish": "偏空",
    "neutral": "中性",
    "trading_range": "震荡区间",
    "user": "问题",
    "ai_content": "回答",
    "ai_reasoning": "模型返回的过程文本",
}


def _key(value):
    return re.sub(r"[^a-z0-9]", "", str(value).lower())


def configured_secrets(settings) -> list[str]:
    values = []

    def visit(node):
        if isinstance(node, dict):
            for key, value in node.items():
                if (
                    (_key(key) in _SECRET_KEYS or _key(key).endswith("apikey"))
                    and isinstance(value, str)
                    and len(value) >= 8
                ):
                    values.append(value)
                elif key == "headers" and isinstance(value, dict):
                    values.extend(v for v in value.values() if isinstance(v, str) and len(v) >= 8)
                else:
                    visit(value)
        elif isinstance(node, list):
            for item in node:
                visit(item)

    if settings is not None:
        visit(settings.model_dump())
    return values


def redact(value, secrets=()):
    if isinstance(value, dict):
        return {
            key: "[已隐藏]" if _key(key) in _SECRET_KEYS else redact(item, secrets)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact(item, secrets) for item in value]
    if isinstance(value, str):
        for secret in secrets:
            if secret:
                value = value.replace(secret, "[已隐藏]")
    return value


def read_record(path: Path) -> tuple[dict, list[dict], list[str]]:
    if path.stat().st_size > 128 * 1024 * 1024:
        raise ValueError("分析记录超过 128 MB，请选择较小的记录。")
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    # Also accept a CLI analyze result wrapping the saved record.
    if isinstance(data, dict) and isinstance(data.get("record"), dict):
        data = data["record"]
    from pa_agent.records.schema import AnalysisRecord

    partial_reason = data.get("_partial_reason") if isinstance(data, dict) else None
    validated = AnalysisRecord.model_validate(
        {k: v for k, v in data.items() if k != "_partial_reason"}
    )
    data = validated.model_dump()
    if partial_reason:
        data["_partial_reason"] = partial_reason
    sidecar = path.with_suffix(".followups.jsonl")
    warnings = []
    followups = []
    if sidecar.exists():
        if sidecar.stat().st_size > 64 * 1024 * 1024:
            raise ValueError("追问记录超过 64 MB，请先拆分后再导出。")
        with sidecar.open(encoding="utf-8-sig") as handle:
            for number, line in enumerate(handle, 1):
                if not line.strip():
                    continue
                try:
                    item = json.loads(line)
                    if not isinstance(item, dict):
                        raise ValueError
                    followups.append(item)
                except ValueError:
                    warnings.append(f"追问记录第 {number} 行无法解析，已跳过。")
    return data, followups, warnings


def default_filename(record: dict) -> str:
    meta = record.get("meta", {})
    text = f"PA分析_{meta.get('symbol', '未命名')}_{meta.get('timeframe', '')}_{meta.get('timestamp_local_iso', '')}"
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", text).strip(". ")[:160] or "PA分析"


def build_export(record, *, sections=None, followups=(), warnings=(), secrets=()) -> dict:
    record = record.model_dump() if hasattr(record, "model_dump") else record
    if not isinstance(record, dict) or not isinstance(record.get("meta"), dict):
        raise ValueError("请选择包含分析元数据的记录。")
    selected = list(SECTIONS) if sections is None else list(dict.fromkeys(sections))
    if not selected or any(key not in SECTIONS for key in selected):
        raise ValueError("请至少选择一个有效的导出环节。")
    record = redact(record, secrets)
    stage1, stage2 = record.get("stage1_diagnosis"), record.get("stage2_decision")
    exception = record.get("exception")
    partial = record.get("_partial_reason")
    status = "已完成" if stage1 and stage2 and not exception and not partial else "未完整完成"
    if exception or partial:
        reason = str((exception or {}).get("type", partial or ""))
        status = "已取消" if "cancel" in reason else "失败 / 部分结果"
    notes = list(warnings)
    if "technical" in selected and not record.get("stage_attempts"):
        notes.append("此记录未保存逐次请求明细；已导出其中实际保存的提示词、模型返回和错误信息。")
    payload = {
        "format": FORMAT_VERSION,
        "exported_at": datetime.now().astimezone().isoformat(),
        "meta": record["meta"],
        "status": status,
        "selected_sections": selected,
        "exception": exception,
        "partial_reason": partial,
        "usage_total": record.get("usage_total", {}),
        "warnings": notes,
        "sections": {},
    }
    content = payload["sections"]
    if "summary" in selected:
        content["summary"] = {
            "市场诊断": stage1.get("diagnosis_summary", {}) if stage1 else None,
            "最终决策": stage2.get("decision", stage2) if stage2 else None,
            "风险评估": stage2.get("risk_assessment") if stage2 else None,
        }
    if "input" in selected:
        content["input"] = {
            "kline_data": record.get("kline_data", []),
            "htf_text": record.get("htf_text", ""),
        }
    if "stage1" in selected:
        content["stage1"] = stage1
    if "routing" in selected:
        content["routing"] = {
            "策略文件": record.get("strategy_files_used", []),
            "参考经验": record.get("experience_loaded", []),
        }
    if "stage2" in selected:
        content["stage2"] = stage2
    if "information" in selected:
        content["information"] = record.get("information_context", [])
    if "followups" in selected:
        content["followups"] = redact(list(followups), secrets)
    if "technical" in selected:
        content["technical"] = {
            key: record.get(key)
            for key in (
                "stage1_messages",
                "stage1_response",
                "stage2_messages",
                "stage2_response",
                "stage_attempts",
            )
        }
    return payload


def _json(value):
    return json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)


def _display(value):
    if value is None:
        return "未记录 / 未执行"
    if isinstance(value, bool):
        return "是" if value else "否"
    return str(value)


def _html_value(value, depth=0):
    if value is None or value == [] or value == {}:
        return '<p class="muted">未记录 / 未执行</p>'
    if isinstance(value, dict):
        if depth > 5:
            return "<pre>" + escape(_json(value)) + "</pre>"
        return (
            '<table class="fields">'
            + "".join(
                f"<tr><th>{escape(_LABELS.get(key, str(key)))}</th><td>{_html_value(item, depth + 1)}</td></tr>"
                for key, item in value.items()
            )
            + "</table>"
        )
    if isinstance(value, list):
        return (
            "<ol>"
            + "".join("<li>" + _html_value(item, depth + 1) + "</li>" for item in value)
            + "</ol>"
        )
    return "<p>" + escape(_LABELS.get(str(value), _display(value))).replace("\n", "<br>") + "</p>"


def report_html(payload: dict) -> str:
    meta = payload["meta"]
    title = f"{meta.get('symbol', '')} · {meta.get('timeframe', '')} 分析报告"
    body = [
        f"<h1>{escape(title)}</h1>",
        f"<p class='subtitle'>{escape(payload['status'])} · 分析时间 {escape(str(meta.get('timestamp_local_iso', '')))}</p>",
        _html_value(
            {
                "品种": meta.get("symbol"),
                "周期": meta.get("timeframe"),
                "K线根数": meta.get("bar_count"),
                "分析模型": meta.get("ai_provider"),
                "导出时间": payload["exported_at"],
            }
        ),
    ]
    body.append(
        "<nav>"
        + " · ".join(
            f'<a href="#{key}">{SECTIONS[key]}</a>' for key in payload["selected_sections"]
        )
        + "</nav>"
    )
    for key, value in payload["sections"].items():
        body.append(f'<section id="{key}"><h2>{SECTIONS[key]}</h2>')
        if key == "technical":
            for field, detail in value.items():
                body.append(
                    f"<details><summary>{escape(field)}</summary><pre>{escape(_json(detail))}</pre></details>"
                )
        elif key == "input":
            bars = value["kline_data"]
            fields = (
                "seq",
                "ts_open",
                "open",
                "high",
                "low",
                "close",
                "volume",
                "ema20",
                "atr14",
                "closed",
            )
            body.append(
                "<div class='scroll'><table><thead><tr>"
                + "".join(f"<th>{field}</th>" for field in fields)
                + "</tr></thead><tbody>"
            )
            body.extend(
                "<tr>"
                + "".join(f"<td>{escape(_display(bar.get(field, '')))}</td>" for field in fields)
                + "</tr>"
                for bar in bars
            )
            body.append("</tbody></table></div>" + _html_value(value["htf_text"]))
        else:
            body.append(_html_value(value))
        body.append("</section>")
    body.append(
        "<section><h2>完成状态与记录说明</h2>"
        + _html_value(
            {
                "异常": payload["exception"],
                "部分记录原因": payload["partial_reason"],
                "用量": payload["usage_total"],
                "导出说明": payload["warnings"],
            }
        )
        + "</section>"
    )
    return (
        """<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>"""
        + escape(title)
        + """</title><style>
body{margin:0 auto;padding:36px;max-width:1120px;font:15px/1.65 'Microsoft YaHei',sans-serif;color:#172433;background:#fff}
h1{font-size:28px;margin-bottom:6px}h2{font-size:20px;border-bottom:2px solid #dae3ec;padding-bottom:8px;margin-top:32px}
.subtitle,.muted{color:#64748b}nav{padding:14px 0}a{color:#155e99}table{border-collapse:collapse;width:100%;font-size:13px}
td,th{border:1px solid #dbe3ec;padding:8px;text-align:left;vertical-align:top}th{background:#f1f5f9;font-weight:600}
.fields>tbody>tr>th{width:170px}td p{margin:0}ol{padding-left:22px}li{margin:8px 0}pre{white-space:pre-wrap;overflow-wrap:anywhere;font:12px/1.6 Consolas,monospace;background:#f6f8fa;padding:16px}
.scroll{overflow-x:auto}details{margin:8px 0}summary{cursor:pointer;font-weight:600}p,td{overflow-wrap:anywhere}
@media print{body{padding:0;font-size:11px}nav{display:none}h2{break-after:avoid}tr{break-inside:avoid}pre{font-size:9px}.scroll{overflow:visible}@page{size:A4;margin:14mm}
</style><body>"""
        + "".join(body)
        + """<script>
let closed=[];addEventListener('beforeprint',()=>{closed=[...document.querySelectorAll('details:not([open])')];closed.forEach(e=>e.open=true)});addEventListener('afterprint',()=>closed.forEach(e=>e.open=false));
</script></body></html>"""
    )


def report_markdown(payload: dict) -> str:
    meta = payload["meta"]
    lines = [
        f"# {meta.get('symbol', '')} · {meta.get('timeframe', '')} 分析报告",
        "",
        f"状态：{payload['status']}　分析时间：{meta.get('timestamp_local_iso', '')}",
        "",
    ]
    for key, value in payload["sections"].items():
        lines.extend(["## " + SECTIONS[key], ""])
        # A fence longer than any fence in supplied content keeps raw text literal.
        text = _json(value)
        fence = "`" * max(3, 1 + max((len(m.group()) for m in re.finditer(r"`+", text)), default=0))
        lines.extend([fence + "json", text, fence, ""])
    lines.extend(
        [
            "## 完成状态与记录说明",
            "",
            _json(
                {
                    "exception": payload["exception"],
                    "partial_reason": payload["partial_reason"],
                    "usage_total": payload["usage_total"],
                    "warnings": payload["warnings"],
                }
            ),
        ]
    )
    return "\n".join(lines)


def kline_csv(bars):
    fields = [
        "seq",
        "ts_open",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "amount",
        "pct_chg",
        "ema20",
        "atr14",
        "closed",
    ]
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    for bar in bars:
        writer.writerow(
            {
                key: "'" + value
                if isinstance(value, str) and value.startswith(("=", "+", "-", "@"))
                else value
                for key, value in bar.items()
            }
        )
    return output.getvalue().encode("utf-8-sig")


def ohlcv_csv(bars):
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(["time", "open", "high", "low", "close", "volume"])
    for bar in sorted(bars, key=lambda bar: float(bar.get("ts_open", 0))):
        values = [
            bar.get("ts_open", ""),
            *[bar.get(key, "") for key in ("open", "high", "low", "close", "volume")],
        ]
        writer.writerow(
            [
                "'" + value
                if isinstance(value, str) and value.startswith(("=", "+", "-", "@"))
                else value
                for value in values
            ]
        )
    return output.getvalue().encode("utf-8-sig")


def write_export(path: Path, payload: dict, *, overwrite=False) -> dict:
    suffix = path.suffix.lower()
    if suffix not in (".zip", ".html", ".md", ".json"):
        raise ValueError("请选择 ZIP、HTML、Markdown 或 JSON 格式。")
    if path.exists() and not overwrite:
        raise FileExistsError("目标文件已存在，请另选名称或确认覆盖。")
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(prefix=".pa-export-", suffix=suffix, dir=path.parent)
    os.close(descriptor)
    temp_path = Path(temp_name)
    try:
        if suffix == ".zip":
            entries = {
                "分析报告.html": report_html(payload).encode("utf-8"),
                "分析报告.md": report_markdown(payload).encode("utf-8"),
                "导出内容.json": _json(payload).encode("utf-8"),
            }
            for index, (key, value) in enumerate(payload["sections"].items(), 1):
                name = f"环节/{index:02d}_{key}.json"
                entries[name] = _json(value).encode("utf-8")
                if key == "input":
                    entries["输入_K线.csv"] = ohlcv_csv(value["kline_data"])
                    entries["输入_K线与指标.csv"] = kline_csv(value["kline_data"])
                if key == "technical":
                    from pa_agent.ai.response_extract import (
                        content_from_response,
                        reasoning_from_response,
                    )

                    for stage in ("stage1", "stage2"):
                        raw = value.get(stage + "_response")
                        entries[f"模型返回/{stage}_回答.txt"] = content_from_response(raw).encode(
                            "utf-8"
                        )
                        entries[f"模型返回/{stage}_过程文本.txt"] = reasoning_from_response(
                            raw
                        ).encode("utf-8")
            entries["说明.txt"] = (
                "先打开分析报告.html 阅读。环节目录保留各项结构化结果，导出内容.json 是完整的本次导出。\n"
                "空值表示原记录没有保存该内容或该环节未执行。导出不会重新运行模型。\n"
                "HTML 报告可使用浏览器的打印功能另存为 PDF。\n" + "\n".join(payload["warnings"])
            ).encode("utf-8")
            manifest = {
                "format": FORMAT_VERSION,
                "files": [
                    {"name": name, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
                    for name, data in entries.items()
                ],
            }
            with zipfile.ZipFile(temp_path, "w", zipfile.ZIP_DEFLATED) as archive:
                for name, data in entries.items():
                    archive.writestr(name, data)
                archive.writestr("manifest.json", _json(manifest))
        else:
            text = (
                report_html(payload)
                if suffix == ".html"
                else report_markdown(payload)
                if suffix == ".md"
                else _json(payload)
            )
            temp_path.write_text(text, encoding="utf-8")
        if overwrite:
            os.replace(temp_path, path)
        else:
            # Preserve another file if it appeared after the initial check.
            with path.open("xb") as target, temp_path.open("rb") as source:
                import shutil

                shutil.copyfileobj(source, target)
        return {
            "path": str(path.resolve()),
            "bytes": path.stat().st_size,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
    finally:
        temp_path.unlink(missing_ok=True)
