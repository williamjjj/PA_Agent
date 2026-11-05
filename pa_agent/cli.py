"""CLI shared by installed, portable and harness-invoked PA Agent."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import signal
import sys


def _read_json(path: str) -> dict:
    source = Path(path)
    if source.stat().st_size > 16 * 1024 * 1024:
        raise ValueError("输入文件超过 16 MB。")
    return json.loads(source.read_text(encoding="utf-8-sig"))


def _load_chart(args, *, research: bool = False) -> dict:
    from pa_agent.quant.local import demo_dataset
    if args.demo:
        return demo_dataset()
    if args.input:
        path = Path(args.input)
        if path.suffix.lower() == ".csv":
            if path.stat().st_size > 2_000_000:
                raise ValueError("CSV 最大 2 MB。")
            csv = path.read_text(encoding="utf-8-sig")
            if research:
                from pa_agent.quant.research_data import DatasetImport, parse_dataset_csv
                return parse_dataset_csv(DatasetImport(csv=csv, symbol=args.symbol, timeframe=args.timeframe))
            from pa_agent.quant.data import ImportRequest, parse_csv, chart_data
            from pa_agent.quant.snapshots import dataset_hash
            chart = chart_data(parse_csv(ImportRequest(csv=csv, symbol=args.symbol, timeframe=args.timeframe)))
            chart.update(source="csv", captured_at=__import__('time').time()*1000)
            chart["dataset_hash"] = dataset_hash(chart)
            return chart
        value = _read_json(args.input)
        return value.get("dataset", value)
    if args.source:
        from pa_agent.data.factory import create_data_source
        from pa_agent.data.snapshot import take_snapshot_from_bars
        from pa_agent.quant.data import chart_data
        from pa_agent.quant.snapshots import dataset_hash
        source = create_data_source(args.source)
        try:
            source.connect()
            if args.source == "tradingview":
                source.set_exchange(args.exchange)
            source.subscribe(args.symbol, args.timeframe)
            frame = take_snapshot_from_bars(source.latest_snapshot(args.bars+55), args.bars,
                                            args.symbol, args.timeframe)
            chart = chart_data(frame)
            chart.update(source=args.source, captured_at=frame.snapshot_ts_local_ms)
            chart["dataset_hash"] = dataset_hash(chart)
            return chart
        finally:
            source.disconnect()
    raise ValueError("请选择 --input、--demo 或 --source。")


def analyze(args, cancel):
    if os.environ.get("PA_AGENT_HARNESS_CHILD"):
        raise ValueError("禁止在 PA Agent 的模型子进程内递归启动分析。")
    from pa_agent.config.settings import load_settings
    from pa_agent.config.paths import PROMPT_DIR, EXPERIENCE_DIR, RECORDS_PENDING_DIR
    from pa_agent.ai.client_factory import create_ai_client
    from pa_agent.ai.prompt_assembler import PromptAssembler
    from pa_agent.ai.json_validator import JsonValidator
    from pa_agent.ai.router import route_strategy_files
    from pa_agent.records.pending_writer import PendingWriter
    from pa_agent.records.experience_reader import ExperienceReader
    from pa_agent.orchestrator.two_stage import TwoStageOrchestrator
    from pa_agent.quant.sources import restore_frame
    from pa_agent.data.snapshot import take_snapshot_from_bars
    from pa_agent.quant.snapshots import factor_evidence
    settings = load_settings()
    if args.backend and args.backend != settings.provider.backend:
        settings.provider.backend = args.backend
        profile = settings.provider.harness_profiles.get(args.backend,{})
        settings.provider.harness_executable = profile.get("executable","")
        settings.provider.harness_model = profile.get("model","")
        settings.provider.harness_config_dir = profile.get("config_dir","")
    if args.model:
        if settings.provider.backend == "api": settings.provider.model = args.model
        else: settings.provider.harness_model = args.model
    if args.executable:
        settings.provider.harness_executable = args.executable
    chart = _load_chart(args)
    full_frame = restore_frame(chart)
    frame = take_snapshot_from_bars(list(full_frame.bars), min(args.bars, len(full_frame.bars)),
                                    full_frame.symbol, full_frame.timeframe)
    from pa_agent.quant.data import chart_data
    from pa_agent.quant.snapshots import dataset_hash
    chart = {**chart_data(frame), "source":chart["source"], "captured_at":frame.snapshot_ts_local_ms}
    chart["dataset_hash"] = dataset_hash(chart)
    exp = ExperienceReader(experience_dir=EXPERIENCE_DIR)
    writer = PendingWriter(pending_dir=RECORDS_PENDING_DIR, api_key=settings.provider.api_key)
    information = []
    for path in getattr(args, "information", []) or []:
        information.append(_read_json(path))
    engine = TwoStageOrchestrator(create_ai_client(settings.provider),
                                 PromptAssembler(PROMPT_DIR, experience_reader=exp, prompt_settings=settings.prompt),
                                 route_strategy_files, JsonValidator(settings), writer, exp, settings,
                                 information_context=information)
    record = engine.submit(frame, cancel, lambda event: print(event.name, file=sys.stderr, flush=True))
    result = writer._sanitize(record.model_dump(), settings.provider.api_key)
    return {"ok":record.exception is None, "record":result,
            "quant_evidence":factor_evidence(chart), "dataset":chart}


def main(argv=None) -> int:
    if sys.stdout is not None and hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="PA Agent：桌面、harness 与量化研究共用入口")
    sub = parser.add_subparsers(dest="command", required=True)
    doctor = sub.add_parser("doctor", help="检查本地 harness 安装；不读取密钥")
    doctor.add_argument("--check", choices=["all","codex","workbuddy","hermes"],
                        help="可选：向所选 harness 发起一次最小应答测试，使用其现有账户")
    integrate = sub.add_parser("integrate", help="为本地 harness 安装 PA Agent 调用技能")
    integrate.add_argument("--target", choices=["all","codex","workbuddy","hermes"], default="all")
    gui = sub.add_parser("gui", help="打开桌面版")
    export = sub.add_parser("export", help="导出已保存的分析结果和各环节")
    export.add_argument("--record", required=True, help="分析记录 JSON 或 analyze 命令结果")
    export.add_argument("--output", required=True, help="目标 .zip / .html / .md / .json")
    export.add_argument("--sections", help="逗号分隔环节 ID，省略时导出全部")
    export.add_argument("--overwrite", action="store_true", help="允许覆盖已有导出文件")
    sources = sub.add_parser("sources", help="列出、测试数据源，或读取 API / MCP 资讯")
    sources.add_argument("--id", help="指定数据源 ID")
    sources.add_argument("--tools", action="store_true", help="读取 MCP 工具列表")
    sources.add_argument("--query", default="", help="查询资讯；数据源 ID 必填")
    sources.add_argument("--test", action="store_true", help="读取 100 根行情或测试资讯")
    sources.add_argument("--output", help="将结果保存为 JSON")
    for command in ("analyze", "factors", "research", "mine"):
        item = sub.add_parser(command)
        source = item.add_mutually_exclusive_group(required=True)
        source.add_argument("--input", help="CSV 或冻结数据集 JSON")
        source.add_argument("--demo", action="store_true", help="模拟数据，非真实行情")
        source.add_argument("--source", help="内置数据源名称或 sources 命令列出的自定义 ID")
        item.add_argument("--symbol", default="BTCUSD")
        item.add_argument("--timeframe", default="1h")
        item.add_argument("--exchange", default="OANDA")
        item.add_argument("--bars", type=int, default=100)
        item.add_argument("--output", help="保存完整 JSON 结果")
        item.add_argument("--config", help="研究或挖掘配置 JSON")
        if command == "analyze":
            item.add_argument("--backend", choices=["api","codex","workbuddy","hermes"])
            item.add_argument("--model", default="")
            item.add_argument("--executable", default="")
            item.add_argument("--information", action="append", default=[], help="附加 sources --query 保存的资讯 JSON，可重复使用")
    args = parser.parse_args(argv)
    if args.command == "gui":
        from pa_agent.main import main as launch
        return launch([sys.argv[0]])
    try:
        if args.command == "export":
            from pa_agent.records.exporter import read_record, build_export, write_export, configured_secrets
            from pa_agent.config.settings import load_settings
            source, output = Path(args.record), Path(args.output)
            if source.resolve() == output.resolve():
                raise ValueError("请使用新的文件名，保留原始分析记录。")
            record, followups, warnings = read_record(source)
            payload = build_export(record, sections=args.sections.split(",") if args.sections else None,
                followups=followups, warnings=warnings, secrets=configured_secrets(load_settings()))
            result = {"ok": True, **write_export(output, payload, overwrite=args.overwrite)}
        elif args.command == "sources":
            from pa_agent.config.settings import load_settings
            from pa_agent.data.factory import create_data_source, data_source_choices, default_symbol_for_kind, source_profile
            settings = load_settings()
            profile = source_profile(args.id, settings)
            if args.tools:
                if profile is None or not profile.transport.startswith("mcp_"):
                    raise ValueError("请指定 MCP 数据源 ID。")
                from pa_agent.data.connections import mcp_request
                result = {"tools": mcp_request(profile, discover=True)}
            elif args.query or (args.test and profile and profile.category == "information"):
                if profile is None:
                    raise ValueError("请指定资讯数据源 ID。")
                from pa_agent.data.information import query_information
                result = query_information(profile, args.query or "近期市场新闻", profile.symbol, profile.timeframe)
            elif args.test:
                if not args.id:
                    raise ValueError("请指定数据源 ID。")
                from dataclasses import asdict
                source = create_data_source(args.id, settings)
                try:
                    source.connect()
                    source.subscribe(default_symbol_for_kind(args.id, settings), profile.timeframe if profile else "1d")
                    bars = source.latest_snapshot(100)
                    result = {"ok": bool(bars), "bars": [asdict(bar) for bar in bars]}
                finally:
                    source.disconnect()
            else:
                result = {"market": [{"id": key, "name": name} for key, name in data_source_choices(settings=settings)],
                          "information": [{"id": p.id, "name": p.name} for p in settings.data_sources.profiles if p.enabled and p.category == "information"]}
            if args.output:
                from pa_agent.quant.local import atomic_json
                atomic_json(Path(args.output), result)
                result = {"ok": result.get("ok", True), "output": str(Path(args.output).resolve())}
        elif args.command == "integrate":
            from pa_agent.integrations import install_skills
            result = {"integrations":install_skills(args.target)}
        elif args.command == "doctor":
            from pa_agent.ai.local_harness import harness_status
            from pa_agent.config.paths import DATA_ROOT, PROMPT_DIR
            result = {"harnesses":harness_status(), "data_dir":str(DATA_ROOT),
                      "prompt_files":len(list(PROMPT_DIR.glob("*.txt")))}
            if args.check:
                from pa_agent.ai.local_harness import LocalHarnessClient
                from pa_agent.config.settings import load_settings
                provider = load_settings().provider
                checks = []
                for backend in (["codex","workbuddy","hermes"] if args.check == "all" else [args.check]):
                    selected = provider.model_copy(deep=True)
                    if selected.backend != backend:
                        profile = selected.harness_profiles.get(backend,{})
                        selected.backend = backend
                        selected.harness_executable = profile.get("executable","")
                        selected.harness_model = profile.get("model","")
                        selected.harness_config_dir = profile.get("config_dir","")
                    try:
                        reply = LocalHarnessClient(selected).chat([
                            {"role":"user","content":'Reply with exactly {"pa_agent_ok":true}.'}], timeout_s=120)
                        passed = json.loads(reply.content).get("pa_agent_ok") is True
                        checks.append({"backend":backend,"ok":passed,"latency_ms":reply.latency_ms})
                    except Exception as exc:
                        checks.append({"backend":backend,"ok":False,"error":str(exc)})
                result.update(checks=checks,ok=all(check["ok"] for check in checks))
        else:
            if not 2 <= args.bars <= 5000:
                raise ValueError("K 线数量须为 2–5000。")
            from pa_agent.util.threading import CancelToken
            cancel = CancelToken()
            signal.signal(signal.SIGINT, lambda *_: cancel.set())
            if args.command == "analyze":
                result = analyze(args, cancel)
            else:
                from pa_agent.config.paths import DATA_ROOT
                from pa_agent.quant.local import run_job
                chart = _load_chart(args, research=args.command != "factors")
                result = run_job(args.command, chart, _read_json(args.config) if args.config else {},
                                 DATA_ROOT / "quant", cancel=cancel,
                                 progress=lambda n,total: print(f"{n}/{total}", file=sys.stderr, flush=True))
            if args.output:
                from pa_agent.quant.local import atomic_json
                atomic_json(Path(args.output), result)
                print(json.dumps({"ok": result.get("ok", True), "output":str(Path(args.output).resolve())},ensure_ascii=False))
                return 0 if result.get("ok", True) else 1
        print(json.dumps(result, ensure_ascii=False, allow_nan=False))
        return 0 if result.get("ok", True) else 1
    except Exception as exc:
        print(json.dumps({"ok":False, "error":str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
