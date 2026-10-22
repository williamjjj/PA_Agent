# Web migration validation

## Independent administrator and invitations — 2026-10-22

Added a separate administrator account, initialized once from private deployment
variables, plus an administrator-only invitation panel. Administrators can view,
copy, replace, randomly regenerate, and disable the shared registration code.
The database stores the code encrypted and serializes registration with changes;
existing environment variables are imported once and cannot revive a revoked code.
Ordinary accounts cannot access these endpoints, and administrators retain normal
tenant isolation for private analysis and model credentials.

Validation on the final feature tree:

- Full offline Python suite: **1007 passed, 7 live deselected**. One existing
  invalid-escape SyntaxWarning; no failures.
- Streaming parser regression, JavaScript syntax, runtime lint, and diff checks pass.
- Local Chromium: **15 checks passed**, no JavaScript or console errors. Covered
  administrator login, manual change/copy, random rotation, rejected old code,
  disabled registration, ordinary-user denial, password change, delayed responses,
  logout and cross-tab account changes.
- Desktop 1440×1000 and mobile 390×844 have no horizontal overflow. Administrator
  controls are accessible from personal settings on mobile. Screenshots inspected.
- Browser interactions used a temporary local SQLite database and test credentials.
  These results do not claim browser interaction against deployed PostgreSQL.

Deployment and bootstrap instructions: [ADMIN.md](ADMIN.md).

## Registration availability feedback — 2026-10-22

The deployed Preview reported `configured: false` and `registration_open: false`;
the owner confirmed that its Preview environment had no variables configured.
The disabled registration option therefore reflects incomplete account storage
configuration. The account dialog previously obscured the page-level explanation.
It now shows the unavailable-service, closed-registration or unknown-status
reason inside the dialog. An explicitly unconfigured service also disables auth
submission; closing registration alone still permits existing-account login.
Registration remains controlled by the existing server policy. Switching out of
registration clears/hides the invitation field and restores password autocomplete.

The existing accounts/auth-race/tab-identity suite passed **30 tests**. Browser
plugin was unavailable, so installed Python Playwright and system Chromium tested
an isolated local service at `http://127.0.0.1:18770`, with status-response fixtures
and real registration/session requests. Desktop 1440×1000 and mobile 390×844 each
passed **10 scenarios**, including configuration states, mode transitions, a
deliberately failed status request, registration, login and logout. Page identity,
nonblank rendering, absence of a framework overlay, readable screenshots and
interaction results were checked. There were no page exceptions or unexpected
console errors; the deliberately aborted request produced the expected network
error. Screenshots were visually inspected; the temporary server was stopped.
JavaScript syntax and whitespace checks passed. Evidence is outside the checkout
under `/workspace/.cloud-setup/pa-registration-qa/` and
`/workspace/.cloud-setup/logs/pa-registration-regression.log`.

This UI change does not create a hosted database or configure Vercel variables.
The owner must configure the Preview environment, redeploy, and check the new
deployment's `/api/status`. See [the trial setup guide](VERCEL_TRIAL.md).

## Deployment preparation — 2026-10-22

The uploaded Vercel notification reports that the Git commit author is not
recognized as a member of team TIANWEI. It does not report an exhausted runtime
quota or a failed application build. The user confirmed that both accounts are
their own and selected a Vercel web/analysis/backtest trial without a persistent
Worker. Account association and persistent PostgreSQL configuration still need
verification in the hosting account; no public trial URL has been verified.

The optional container deployment package builds successfully with Python 3.12,
uv 0.12.23 and the existing lockfile. An actual first startup exposed root-owned
0600 source files inherited from the managed checkout. The Dockerfile now grants
read/traverse access inside the image while retaining root ownership and a
non-root runtime user; the workspace source permissions were not changed.

An isolated Compose project then passed five live checks: Web/HTML/database
readiness, invitation enforcement and two registrations, isolated personal
provider settings, a separate Worker completing an eight-candidate synthetic
research task with private export, and session/settings/results persistence
after all three services restarted. The database was PostgreSQL 17 and the
application filesystem was read-only. No real model or data-provider calls were
made. These local checks do not prove Vercel hosting or external provider access.

The configuration helper creates new private files with mode 0600, refuses
overwrites, and does not print generated values. Compose configuration parsing,
helper checks, Ruff and whitespace checks passed. Evidence is in
`/workspace/.cloud-setup/pa-deploy-smoke/validation.json` and the
`pa-deploy-*.log` files under `/workspace/.cloud-setup/logs/`.

## Prediction repair and background mining, stage 3 — 2026-10-22

The previously failing prediction property is fixed before stage-three work.
The production normalizer and validator intentionally preserve a model-selected
tied maximum; the older property and prompt incorrectly demanded the first
maximum. The property now asserts the exact existing policy (preserve a valid
maximum, otherwise use the fixed-order first maximum), rather than merely
accepting membership or skipping ties. Regression cases check the validator
still rejects non-maxima and zero-total probabilities. A real production defect
was also fixed: `" Bearish "` now becomes `"bearish"` for both unique and tied
maxima through the public normalizer. The intermediate complete suite passed
**903 tests, 7 deselected**, before mining implementation began.

Stage three provides a durable database queue and explicitly started CPU
Worker. Candidate eligibility and fitness use training data only. Completed
artifacts contain the winning named-token formula, provenance, configuration,
seed, versions, training ranking and separate validation/test research results.
This is bounded deterministic RPN search, not legacy Transformer/GPU training.

| Latest check | Result |
| --- | --- |
| Full offline suite, unchanged live selection | **996 passed, 0 failed, 7 deselected** |
| Web suite, included above | **357 passed** |
| Formula interpreter and mining engine | **43 passed**, included above |
| Durable mining store | **32 passed**, included above |
| Independent Worker lifecycle | **14 passed**, included above |
| Mining authenticated API with real CLI processes | **4 passed**, included above |
| PostgreSQL 17.11 mining and schema concurrency | **33 checks passed** in a disposable cluster |
| SSE parser | **4 passed** |
| Runtime-error Ruff, JavaScript syntax, diff whitespace | Passed |
| Chromium desktop/mobile mining workflows | **9 checks passed**, 1440×1000 and 390×844 |
| Lost submission followed by rate limiting | Focused browser regression passed: 3 requests, 1 idempotency key, 1 task |

Engine tests cover named-token validation, causal trailing operators, null and
invalid arithmetic behavior, bounded generation, deterministic candidates,
train-only eligibility/selection, future perturbation, exact checkpoint replay,
and refusal to publish incomplete searches. The existing 45 research-engine
tests continue to pass after extracting support for formula-valued signals.

Persistence checks include tenant ownership, composite dataset foreign keys,
atomic quotas/idempotency, one running job per account, independent PA leases,
claim contention and queue fairness, lease-token fencing including expiry while
waiting for a row lock, cancellation versus checkpoint/final commit, immutable
results, budget accounting and bounded crash recovery. An hour-long outage
charges only the expired lease's unconfirmed work, not the entire offline wait.
All 32 store scenarios and concurrent schema initialization were repeated on
PostgreSQL; the disposable database was dropped and its server stopped.

An actual CLI process was killed after candidate 1 on a 5,000-bar, 128-trial job.
After the real 30-second lease expired, a new process resumed attempt 2 and
completed all candidates. Formula, selection record and full three-segment
backtest were exactly equal to uninterrupted execution. Accumulated time was
33.02 seconds, including conservative unconfirmed lease time. A separate actual
SIGTERM left a queued checkpoint and no active lease. All smoke subprocesses
were stopped. Another real restart after a source fingerprint change failed
with `runtime_changed` and published no artifact, as required.

Browser testing used Python Playwright/system Chromium because Browser plugin
was unavailable. Tests used the real local FastAPI server and real CLI Worker:
offline queue display, reload recovery, cancellation, explicit new-job retry,
deliberately lost POST response and reuse of the same idempotency key after
reload, online completion, formula/metrics/ledger, full export, polling pause,
25-row pagination, mobile layout and account cleanup. All four screenshots
were visually inspected. There were no page errors or unexpected console
errors; the single deliberately aborted network request was recorded separately.
An additional focused check injected a lost submission response followed by HTTP
429, then reloaded and retried. The original key and eight-trial configuration
were retained, and exactly one task existed after three requests; only the
intentional network/429 console events were present. Evidence is
`pa-mining-qa/idempotency-429-results.json` under the directory below.

The maximum synthetic engine check (5,000 bars, 128 candidates) completed in
about 1.4 seconds, with a longest candidate around 0.017 seconds in this machine.
This is not a production performance guarantee or evidence of investment value.

Evidence is outside the checkout: `logs/pa-prediction-fix.*`,
`logs/pa-mining-regression.*`, `pa-research-pg/mining-validation.json`,
`pa-mining-worker-smoke/{validation,upgrade-rejection}.json`, and
`pa-mining-qa/`, all under `/workspace/.cloud-setup/`. The cloud configuration
draft's `start_skill` was updated with the tested independent Worker startup,
shared database requirements and functional readiness checks; saving a draft
does not deploy or publish the environment. No new dependencies, live Birdeye
calls, paid LLM calls, original AlphaGPT training, trades or remote deployment
were performed. The original AlphaGPT checkout remains unchanged.

Earlier sections below retain their historical results; their prediction failure
is resolved by this entry. See [the mining contract](ALPHAGPT_MINING.md).

## Historical research, stage 2 — 2026-10-22

The second stage adds bounded historical research to the existing authenticated
web application. CSV, personal Birdeye history and owned source snapshots become
immutable datasets; a fixed factor rule fits thresholds on the chronological
training segment and reports validation/test separately. The integration uses
the causal factor contract, not the legacy AlphaGPT training/backtest pipeline.
The AlphaGPT checkout remains unchanged.

| Check | Result |
| --- | --- |
| Full offline suite, unchanged live selection | **892 passed, 1 existing failure, 7 deselected** |
| Web suite, included above | **264 passed** |
| Research engine | **45 passed**, included above |
| Research data ingestion | **40 passed**, included above |
| Research persistence | **12 passed**, included above |
| Research authenticated API | **7 passed**, included above |
| SSE parser tests | **4 passed** |
| Runtime-error Ruff, JavaScript syntax, diff whitespace | Passed |
| PostgreSQL 17.11 persistence/schema concurrency | **12 checks passed** in a disposable local cluster |
| Chromium browser workflows | **12 checks passed**, 1440×1000 and 390×844; no page/console errors |

The original prediction tie property still fails with `direction="bearish"`
and an empty probability dictionary. It is the same previously documented
normalizer/property contract mismatch. No test assertions or selections were
weakened; the full CI suite therefore remains red on that existing defect.

Engine checks cover independently calculable next-open buys/sells, fee-inclusive
quantity, forced horizon exits, initial-equity drawdown, ledger reconciliation,
benchmark alignment, no-trade null win rate, and deterministic cost scenarios.
Changing only test OHLCV leaves the fitted thresholds and entire training and
validation results unchanged. Training performance is explicitly in-sample.
Repeated human tuning after viewing test results is described as selection bias.

Data checks cover closed/aligned UTC bars, 300–5000 count bounds, safe CSV error
handling, duplicate/conflict handling, requested leading/trailing coverage,
hash identity including requested bounds, personal Birdeye headers, bounded
attempts/deadline, and no liquidity/FDV filling. HTTP calls are mocked. Uploaded
market identity stays user-declared. Gap/zero-volume datasets cannot run.

API checks exercise two accounts, stale account headers, CSRF, dataset/run/export
ownership, unchanged saved results despite a broken recalculation function,
deletion protection, lease cleanup, error secrecy and the scoped 2.5 MB request
limit (CSV content maximum 2 MB, other endpoints remain 600 KB). An exported
dataset/config replays to the exact original engine result. An independent
review also verified real API ingestion of 5,000 bars and the same ownership
and deletion behavior.

PostgreSQL validation used a fresh local cluster on `127.0.0.1:55433` and a
disposable database. It exercised concurrent schema initialization, duplicate
imports of 5,000-row data, per-account quotas, actual composite foreign-key
enforcement, run/delete races and metadata-only pagination. The database was
dropped and that cluster stopped; existing databases were not touched.

A bounded 5,000-bar synthetic run including parsing completed in 0.531 seconds
in this environment; its JSON data/result export was approximately 2 MB.
This is a local example, not a production latency/load guarantee.

Browser validation used Python Playwright with system Chromium because the
Browser plugin was unavailable. A real local FastAPI server and isolated SQLite
store exercised the no-model-key demo, insufficient current chart, CSV import,
all three segments, curves/ledger/costs, JSON export, saved-result replay without
recomputation, fixed research selection across main-chart changes, late response
suppression, 25-row pagination and account-switch cleanup. Gap quality is shown
before running. Desktop/mobile screenshots were visually inspected; navigation
selection and chart viewport resizing were corrected and the workflow rerun.

Evidence: full run log/JUnit in `/workspace/.cloud-setup/logs/pa-research-regression.*`;
PostgreSQL checks in `/workspace/.cloud-setup/pa-research-pg/validation.{json,log}`.
Browser scripts, results and screenshots are in `/workspace/.cloud-setup/pa-research-qa/`.
No new dependency or cloud startup configuration is required. No live Birdeye
permission check, paid model call, training job, trade or remote deployment was
performed. See [the research contract](ALPHAGPT_BACKTEST.md).

## AlphaGPT factor panel, stage 1 — 2026-10-13

The first integration stage now provides account-owned Birdeye OHLCV access,
full Solana address identity, immutable snapshots shared by PA and factors,
and an independent browser factor panel. Snapshot payloads persist causal
factor results before analysis; PA records copy that evidence with its version,
snapshot ID and dataset hash. Saved evidence remains visible after the
one-hour source snapshot expires. No training, backtest or trading runner is
started by this feature.

| Latest check | Result |
| --- | --- |
| Full offline suite, unchanged live selection | **788 passed, 1 existing failure, 7 deselected** |
| Web suite, also included in full run | **160 passed** |
| Causal factor tests | **22 passed**, included above |
| Birdeye adapter/API tests | **38 passed**, included above |
| Shared snapshot/API evidence tests | **7 passed**, included above |
| Small-price PA prompts and precision helpers | **20 passed**, included above |
| SSE parser tests | **4 passed** |
| Runtime-error Ruff checks, JavaScript syntax, diff whitespace | Passed |
| Chromium browser workflows | **9 checks passed**, 1440×1000 and 390×844 |

The one full-suite failure is the previously reported prediction tie property
described below. No assertions were weakened and no extra exclusions were
added. The full CI gate still fails on that existing property.

Numerical tests verify known raw factor values, warm-up and gaps, invalid or
forming-bar rejection, strict finite JSON, and that changing/appending future
data does not change any historical factor values. Missing historical
liquidity/FDV is explicitly unavailable. Price precision regressions exercise
the actual two-stage prompt builders with `0.00000267613` and `1e-9`, and tick,
structure-level and chart helpers down to `1e-12`. Existing ordinary-price
assertions remain unchanged.

API tests prove that factor viewing needs no LLM key, a second user cannot
access the snapshot/evidence/record, the exact chart indicators reach PA, and
stored evidence is unchanged even if the factor implementation later fails.
The factor failure path emits unavailable data without synthetic zeros or
private exception details, and does not prevent PA from analyzing the input.
Different full addresses, sources, periods and adjustments do not share
incremental context. Birdeye tests use mocked HTTP with two actual authenticated
API clients and check credential encryption, absence of environment-key
fallback, paging, bounded retries, closed data and safe actionable errors.

Browser validation used the real local FastAPI server with an isolated SQLite
database and Python Playwright/Chromium because Browser plugin was not
available. The workflow was: register without an LLM key, calculate demo
factors, inspect history, import CSV into a distinct snapshot, switch accounts,
and reopen a saved record. A delayed response could not repopulate a replaced
chart; deleting the temporary source snapshot did not prevent the saved
factor button from replaying record evidence without network recomputation.
Birdeye address and masked-key fields, low-price displays, responsive layout,
and account cleanup were checked. There were no page or console errors.

Evidence: `/workspace/.cloud-setup/pa-quant-qa/` contains browser scripts,
results and desktop/mobile screenshots. Logs and JUnit results are in
`/workspace/.cloud-setup/logs/pa-quant-*`. Screenshots and test databases are
outside the checkout. No new package dependencies were added.

Live Birdeye access and actual account-plan permissions remain unverified:
no user key was available to these tests, and the official documentation
request was denied by the managed proxy. The adapter uses the v1 OHLCV contract
already present in AlphaGPT; volume is labeled provider-reported. No paid LLM
generation, remote deployment or production-load test was performed. See
[the factor contract](ALPHAGPT_FACTORS.md) for numerical and calendar limits.

## Account isolation follow-up — 2026-10-13

Starting point: `388ec29` on `codex/web-multiuser-vercel`, CPython 3.12.14,
dependencies installed from the existing lockfile. The branch already provided
registration, personal model configuration, encrypted credentials and tenant
storage. This change hardens three cases:

- A stale browser tab sends the account ID it displayed. The server compares
  it with the authenticated Cookie before reading or changing private data.
  An identity mismatch returns HTTP 409 without changing the new account.
  BroadcastChannel and focus/visibility checks clear stale private views.
- Session issuance locks and checks the exact password hash that was verified.
  Password changes use a conditional update and revoke sessions in one
  transaction, preventing old-password verification from issuing a valid
  session after rotation. The database schema is unchanged.
- A provider origin change requires an explicitly supplied API key or explicit
  removal of the stored key. Same-origin path changes retain the key. Both
  settings saves and model-list probes enforce the rule before network access.

| Current check | Result |
| --- | --- |
| Web regression suite | **73 passed** |
| Authentication concurrency on PostgreSQL 17.11 | **4 passed**; disposable database removed |
| Full offline regression | **701 passed, 1 failed, 7 deselected** |
| SSE parser tests | **4 passed** |
| Runtime-error Ruff checks and JavaScript syntax | Passed |
| Chromium at 1440×1000 and 390×844 | Registration, private settings, two accounts, cross-tab identity and provider-change flows passed |

The full-suite failure is the existing property
`tests/property/test_next_bar_prediction.py::test_p2_direction_equals_argmax`.
It demands the first maximum when probabilities tie, while both the normalizer
and validator explicitly preserve/accept any tied winner. An empty probability
dictionary with `direction="bearish"` reproduces the failure; a valid 40/40/20
tie exposes the same contract mismatch. This change does not alter prediction
behavior, weaken the assertion or add test exclusions. The complete CI gate
therefore remains blocked by that failure.

Browser checks used the real local FastAPI server and a disposable SQLite
database, fake model credentials, and no external requests. Disabling
BroadcastChannel verified the server-side fallback: the old tab's settings
save returned the expected 409, cleared its inputs and left the new user's key
unchanged. Reloaded settings returned only credential-presence flags. Desktop
and mobile had no horizontal overflow or JavaScript runtime exceptions; the
deliberate 409 was the only browser console HTTP error. A logged-in reload's
demo-chart clearing issue was corrected by loading identity before rendering
the initial chart.

Local evidence is under `/workspace/.cloud-setup/pa-web-qa/` (browser results,
script and screenshots) and `/workspace/.cloud-setup/logs/` (focused/full
regression, JUnit and PostgreSQL logs). These are local checks; no real model
generation, production deployment or AlphaGPT integration was performed.
AlphaGPT feasibility and required corrections are documented in
[ALPHAGPT_INTEGRATION.md](ALPHAGPT_INTEGRATION.md).

## Earlier migration verification — 2026-10-10

Base: `90924d09f59e4eae7b76b6eb56c21cf446e32096` (`v0/python-4aa3dbb3`).
Earlier local validation: 2026-10-10, Linux, CPython 3.12.14, locked dependencies.

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
