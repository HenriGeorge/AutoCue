# Verify Workflow (the *how* of GATE 2)

Last updated: 2026-08-10 01:49

> **Source of truth & sync.** Repo snapshot of the machine-global `~/.claude/rules/verify-workflow.md`
> (via `sync-rules.sh`). `docs/VERIFY-WORKFLOW.md` is the expanded shipped companion (same spine + a
> by-profile EXERCISE table). Keep the two reconciled in substance — this file stays compact, the doc
> carries the tables. There is no auto-sync between them; edit both.

The Prove leg of **Design → Code → Prove** (`workflow.md`). This is *how* you satisfy
GATE 2 — earn the word "done" with FRESH evidence THIS turn. The discipline: **RUN it →
READ it → SHOW it.** No "should / probably / seems" — run the check, read the real output,
and surface the evidence to the user. A suite the same agent just wrote is not independent
proof, and green unit tests ≠ "works" (drive the running app too). `CLAUDE.md` wins; the
gate is never skipped.

## The verify pipeline (fast → slow; run the WHOLE thing every cycle)

```mermaid
flowchart LR
    S["STATIC<br/>typecheck · lint · unit<br/>/validate"] --> B["BEHAVIORAL<br/>behaviour-test regression<br/>cc-worktrees test"]
    B --> L["EXERCISE<br/>drive the REAL artifact<br/>(by profile) · /verify"]
    L --> SF{"silent-failure check<br/>does the client THROW or return an error value?<br/>a catch that can't fire is DEAD code"}
    SF --> Q{evidence green?}
    Q -->|no| D["systematic-debugging<br/>root cause → 1 fix → re-verify"]
    D --> S
    Q -->|yes| C["CLAIM with evidence<br/>RUN → READ → SHOW it in the report<br/>verification-before-completion → REVIEW"]
```

- **STATIC** *(seconds — fail fast)* — typecheck + lint + unit via `/validate` (or the project's command). Always-on, zero flakiness.
- **BEHAVIORAL** *(codified regression — run EVERY cycle)* — the behaviour-test suite for your stack (web: `e2e/*.spec.ts` via `playwright-tester`, `frontend-testing` for vitest; service: integration tests; CLI/lib: golden/property tests; data: fixture tests). Hold the per-repo lock: `cc-worktrees test -- <cmd>`.
- **EXERCISE** *(green ≠ works)* — start the artifact and drive the RUNNING thing (`/verify`). _How_ depends on the profile: **Web UI** → browser @`http://127.0.0.1:PORT` with **Chrome DevTools MCP** (`webapp-testing`, never the blocked extension — `local-browser-testing.md`), screenshots; **Service/API** → hit endpoints (`curl`/HTTP client), assert status+body; **CLI/Library** → run it, assert stdout+exit code; **Data** → run on fixtures, assert output schema/row counts. Codify anything you check by hand as a test.
- **LOCAL-STACK OWNERSHIP** *(before you conclude "data loss")* — every cc-worktrees project runs `supabase start` on the same default ports (54321/54322), so a sibling project can silently squat them and your app drives the *wrong, empty* DB — which reads as vanished data. Before believing it: `docker ps` for **who binds 54321/54322**, `docker ps -a` for your own db container's real state (`Created` / `Exited (137)` ≠ running); fix with `supabase stop && supabase start` (volumes preserved). (#178)
- **SILENT-FAILURE CHECK** — *best-effort ≠ unobservable.* For each `try/catch` around a client call, confirm the client actually *throws* on the failure mode — many (e.g. `supabase-js rpc()`) **return `{ error }` and do NOT throw**, so the `catch` is dead code and the error is silently dropped. Read the error and log a breadcrumb even when you intentionally continue. (Pairs with `silent-failure-hunter` at REVIEW.)
- **ON FAIL** — `systematic-debugging` (root cause → ONE hypothesis → failing test → single fix), then re-run the WHOLE pipeline. Don't patch symptoms; ≥3 failed fixes → question the architecture.
- **CLAIM** — only via `verification-before-completion`: the exact command + its real output, THIS turn, **and SHOW that evidence in your report** (a screenshot/response captured but never surfaced is half-wasted). GATE 2 passed → `workflow.md` REVIEW (`/code-review`, **distinct** from verify) takes over.

## What counts as "fresh evidence" (the only thing GATE 2 accepts)

| ✅ evidence | ❌ not evidence |
|---|---|
| command run THIS turn + its real output (`1446 passed in 12.3s`, exit 0) | "I ran it earlier / it should pass / the code looks right" |
| the evidence **shown in the report** — a screenshot of the RUNNING app (web) · the real HTTP response (API) · stdout + exit code (CLI/lib) · output schema + row counts (data) | a test *description* with no run; mocked / asserted output; **evidence captured but never surfaced** |
| the failing test, now green after the fix | "all tests are green" with no command, count, or durations |

**Checklist before "done":** ran this turn · exit 0 · pass-count shown · nothing wrongly skipped/xfailed · no flaky/retry · real artifact driven (web→screenshot · API→response · CLI→stdout/exit · data→output) · **evidence SHOWN in the report** · **no dead `try/catch` swallowing a returned error** · full suite re-run as regression (not a subset).

## See also
`verification-before-completion` (RUN→READ→SHOW) · `systematic-debugging` (4-phase root cause) · `/validate` · `/verify` · `local-browser-testing.md` (`127.0.0.1`, not the extension) · `agent-delegation.md` (delegate to playwright-tester / code-reviewer). Worked example — a 3-leg local gate with no CI: unit + lint, the e2e suite, and a live-app smoke, all green in-session before an admin-merge to `main`.
