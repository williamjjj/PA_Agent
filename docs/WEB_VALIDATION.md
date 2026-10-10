# Web migration validation

Base: `90924d09f59e4eae7b76b6eb56c21cf446e32096` (`v0/python-4aa3dbb3`).
Latest local validation: 2026-10-10, Linux, CPython 3.12.14, locked dependencies.

## Results

| Check | Result |
| --- | --- |
| `uv sync --locked --extra dev --python 3.12` | Passed |
| `pytest tests -m "not live"` | **670 passed, 7 deselected** |
| `node --test tests/web/test_stream.mjs` | **4 passed** |
| Runtime-error Ruff checks and `node --check public/app.js` | Passed |
| Root `app:app` import without PyQt6 | Passed |
| Browser workflows at 1440×1000 and 390×844 | Passed; no horizontal overflow |

The seven deselected tests require external services. No additional exclusions, xfails, or ignored failures were introduced to obtain this result. CI now runs the entire offline suite as a required job step and preserves its JUnit report; the former nonblocking legacy report has been removed. These are local results, not a claim about an unobserved GitHub Actions run.

## Functional and isolation checks

- Account tests cover encrypted secrets, secret-preserving updates and explicit deletion, tenant ownership, session revocation, password changes, CSRF, invitations, atomic leases and rate limits.
- Snapshot tests cover CSV validation, immutable chart/analysis input, bridge account isolation, source errors, source symbols, adjustment isolation and incremental overlap. A public Yahoo adapter request previously returned 50 closed BTC-USD 1h bars successfully.
- Model transport tests reject private IPs and verify public-IP pinning with the original Host and TLS SNI. Per-account model discovery uses the requesting account's credentials, can test unsaved changes without persisting them, and returns safe errors without exposing upstream bodies or secrets.
- Provider tests exercise DeepSeek thinking parameters, gateway thinking/output budgets, native OpenAI completion budgets, and actual OpenAI SDK stream parsing over a mocked HTTP transport. Truncated streams or a missing successful finish marker cannot be reported as completed analysis.
- Prompt overrides remain separate from the desktop process-wide prompt cache. Continuity only receives an explicitly owned analysis record; it cannot load a shared desktop trade CSV.
- SSE tests cover fragmented UTF-8/CRLF, exactly-once delivery, error propagation, incomplete EOF and multiline data.

## Browser verification without Firecrawl

Playwright with a local Chromium browser exercised the real FastAPI application and a temporary SQLite database. Only the outbound model HTTP transport was replaced with deterministic fixtures; the real OpenAI SDK, two-stage orchestrator, validation, persistence and follow-up code executed. No paid generation endpoint was used.

The verified flow includes registration, model-list discovery, saving personal settings, uploading and selecting a closed-bar MT5 bridge snapshot, two-stage streaming analysis, a persisted result, a saved follow-up, and the decision-path display. API readback confirmed one complete analysis and one follow-up for the first account.

On the mobile viewport, logout cleared both saved and unsaved private views. A second account had no model key, no first-account records and no first-account analysis content. Forced session expiry cleared the settings fields, chart and private result state before opening the login dialog. Expected 422 (missing personal API key) and 401 (expired session) responses were verified; there were no unexpected JavaScript errors. Visual inspection covered the desktop result view and the mobile settings dialog. Browser pattern expressions were corrected for current HTML Unicode-set validation.

Earlier browser checks also covered history, experience, prompt editing, source selection and the example chart. Continuous tracking remains page-bound and does not become an always-on server worker.

## Resolving the inherited regression failures

An earlier checkpoint reported 34 inherited failures, reproduced against the original branch. This revision resolves them instead of ignoring them:

- Real defects: root logging was accidentally suppressed; inside/outside bar contradictions were not rejected; a direct normalization helper omitted pending-order enum aliases; saved timestamps lacked an explicit offset. These behaviors were fixed with regression coverage.
- Strict/lenient tests now explicitly select their intended mode. No-order tests assert the existing normalization contract (no direction or prices) and preserve checks that input data is not mutated. Truncation tests distinguish permitted repair from explicitly disabled repair.
- Snapshot fixtures now contain realistic timestamps and a forming K0 plus closed K1…Kn bars. Continuity fixtures use explicit timezones, unexpired windows and order prices consistent with the scenario. Required second target prices were restored to older fixtures.
- Provider tests select the provider/model they intend to exercise. DeepSeek Chat Completions parameters follow its [thinking-mode documentation](https://api-docs.deepseek.com/guides/thinking_mode/). Gateway thinking budgets remain below the output limit.
- Follow-up tests account for the existing validated recall prefix while retaining reasoning/context assertions. TradingView tests use the actual forex venue set. MT5 clock tests mock the optional Windows SDK. A missing malformed-JSON fixture is now versioned under `tests/fixtures/`.

These fixture updates do not relax order-price invariants or replace the strategy/router implementation with test-only behavior.

## Deployment boundary

GitHub reported a successful Vercel Preview deployment for the preceding `5de87a8` commit. Current commit deployment status is tracked in PR #1. The Preview redirects unauthenticated requests to Vercel login; deployment protection was not bypassed.

The connected Vercel account returned no available teams and a 403 when reading project `pa-agent-rebuild` in team `tianwei`. GitHub access works, but that does not grant Vercel team access. No production deployment, environment-variable change, or database provisioning was performed.

The deployment owner still needs:

- Persistent PostgreSQL `DATABASE_URL` and a stable `PA_WEB_ENCRYPTION_KEY` in the appropriate Preview/Production environments.
- `PA_WEB_INVITE_CODE` or `PA_WEB_REGISTRATION=1` to enable registration.
- Each account's model credentials and data-provider permissions.
- Validation of real model billing/latency, production PostgreSQL concurrency, and a Windows MT5 terminal if that source is used.

A successful build/deployment does not by itself verify those external services.
