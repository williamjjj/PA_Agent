.PHONY: run test lint uv-run uv-test uv-lint
run uv-run:
	uv run uvicorn app:app --host 127.0.0.1 --port 8000
test uv-test:
	uv run pytest tests -m "not live"
	node --test tests/web/test_stream.mjs
lint uv-lint:
	uv run ruff check pa_agent/web tests/web pa_agent/bridge.py --select E9,F63,F7,F82
