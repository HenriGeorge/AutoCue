# AutoCue e2e (Playwright)

End-to-end smoke harness driven by the `autocue-qa` Claude Code sub-agent
(`/autocue-qa`). Also runnable directly during development.

## SAFETY

The harness boots `autocue serve` against a SANDBOX copy of `master.db`
created in a temp dir by `globalSetup.ts`. `0-safety.spec.ts` runs first
(the `0-` prefix forces it ahead of every other spec in alphabetical
discovery order) and verifies — via `/api/status` + `X-AutoCue-Diagnostic: 1` header —
that the server is bound to the sandbox copy and not your real library.
If verification fails, the rest of the run is aborted before any other
test executes.

Confirm Rekordbox is NOT running before launching the suite. The server
refuses every write endpoint while it is.

## One-time setup

```bash
cd tests/e2e
npm install
npm run install:browsers
```

The harness assumes `autocue serve` is on PATH (`pip install -e .` in the
project root).

## Run

```bash
cd tests/e2e
npm test                 # smoke suite (safety + selectors + API + SSE + UI + Pages)
RUN_FULL=1 npm test      # full suite (writes to sandbox DB; not yet implemented)
```

Override the source DB (CI / fixture mode):

```bash
AUTOCUE_SOURCE_DB=/path/to/some/master.db npm test
```

Ports are allocated by `globalSetup.ts` (port-0 trick) — the harness
never uses the production port 7432.

Outputs:

- `tests/e2e/playwright-report/` — HTML report
- `tests/e2e/results.json` — machine-readable run summary
- Screenshots, video, traces on failure only

Sandbox dir is cleaned up by `globalTeardown.ts`.

## What runs

| Spec | Purpose |
|------|---------|
| `0-safety.spec.ts` | Verifies server is bound to sandbox DB; aborts run otherwise. `0-` prefix forces it to run first under alphabetical spec discovery |
| `selectors-exist.spec.ts` | Single source of truth for DOM selectors the suite depends on |
| `qa-smoke.spec.ts` | Read-only API + bounded SSE + UI smoke (console / pageerror / requestfailed) |
| `pages-smoke.spec.ts` | Pages-mode static serve — server-only panels must be hidden, no crashes |
| `qa-full.spec.ts` | Write endpoints + every SSE; gated by `RUN_FULL=1` (stub) |

## Extending

When `docs/index.html` adds a new ID the agent needs, add it to
`REQUIRED_SELECTORS` in `selectors-exist.spec.ts` first. That spec is the
canonical list — the agent prompt does not duplicate it.
