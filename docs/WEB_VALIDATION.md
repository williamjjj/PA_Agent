# Web migration validation

Base: 90924d09f59e4eae7b76b6eb56c21cf446e32096 (v0/python-4aa3dbb3).
Runtime: Linux, CPython 3.12.15, uv lock with production dependencies.

## Verified

The strict Web/core request gate passed **58 tests**. The standalone SSE parser passed **4 tests**. Runtime-error lint and JavaScript syntax checks also passed.

- Locked dependency installation succeeds. The installed development environment occupies approximately 322 MiB; production excludes development tools.
- Root app:app imports successfully without PyQt6. GitHub's Vercel status reports a successful Preview deployment for migration commit f4ceb00.
- Account tests cover encrypted secrets, secret-preserving updates and explicit deletion, tenant ownership, session revocation, password changes, CSRF, invitations, atomic leases and rate limits.
- Snapshot tests cover CSV validation, immutable chart/analysis input, bridge account isolation, source errors, source symbols, adjustment isolation and incremental overlap.
- Model transport tests reject private IPs and verify public-IP pinning with the original Host and TLS SNI.
- Prompt overrides are isolated from the desktop process-wide system-prompt cache.
- SSE tests exercise byte-by-byte UTF-8/CRLF, exactly-once event delivery, error propagation, incomplete EOF and multiline data.
- Browser smoke tests cover demo chart rendering, registration/login, model settings, source selection with an authenticated bridge snapshot, result panels, decision path, history/experience and follow-up UI.
- Browser analysis and follow-up responses use explicit test fixtures, not paid model calls. Account/settings/bridge/history/experience operations use the real local backend.
- Desktop 1440px and mobile 390px layouts have no horizontal overflow. Browser error collection remained empty through the tested flows.
- The remote Preview redirects unauthenticated requests to Vercel login. No deployment protection bypass was used; production database/model behavior has not been asserted.

## Inherited regression failures

The full retained suite, after fixing migration-induced failures, reported **616 passed, 34 failed, 7 deselected** at this checkpoint. Additional web tests are subsequently added; this is not a claim that the complete suite passes.

All 34 failure identities were reproduced on the base source in the same runtime (using headless-compatible selections). Three provider compatibility assertions were reproduced separately using the original provider client. An additional randomized prediction assertion also failed on one base run.

The inherited failures include:
- Old strict/lenient normalization expectations and no-order price repair.
- Continuity rules, pending order normalization, and forecast expectations.
- Older free-chat prefix/message-count expectations.
- A missing tools/stage2_raw_sample.txt test fixture.
- Host timezone assumptions, desktop logging expectations and tests requiring the Windows-only MetaTrader5 package.
- Old KKAI provider-specific thinking parameter expectations.

These tests remain available; their assertions and the corresponding trading rules have not been silently rewritten to make the migration green. CI has a strict Web/core request gate and a separate full inherited report with its failure result and JUnit artifact preserved.

## Still requires the deployment owner's configuration

- Vercel PostgreSQL DATABASE_URL and a stable PA_WEB_ENCRYPTION_KEY.
- Registration configuration: PA_WEB_INVITE_CODE or PA_WEB_REGISTRATION=1.
- Each account's actual model credentials and data-provider permissions.
- Real model billing/latency, production PostgreSQL concurrency and Windows MT5 terminal connectivity.
- Browser tracking is page-bound; it does not provide an always-on server worker.

The GitHub Preview status confirms the build/deployment, not successful paid model requests or production database provisioning.
