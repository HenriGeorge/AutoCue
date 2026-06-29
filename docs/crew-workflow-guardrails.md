# Crew workflow guardrails

Decision diagrams that turn a `cc-worktrees` crew session's recurring frictions into **branch logic**.
A retrospective lesson in [`lessons.md`](lessons.md) records *what* went wrong; the diagram below makes
the failure mode structurally hard to repeat. These are **crew-mechanism** guardrails — they apply when
a multi-pane crew is running, and live alongside the coordinator methodology in
[`../bin/cc-worktrees`](../bin/cc-worktrees) (`_crew_methodology`). The universal spine stays in
[`WORKFLOW.md`](WORKFLOW.md) / [`VERIFY-WORKFLOW.md`](VERIFY-WORKFLOW.md).

Inline Mermaid is canonical here (the repo's docs convention — no committed images).

| Guardrail | Encodes |
|---|---|
| D1 · Coordinator dispatch loop | #96 idle-pane triage · #26 one live driver · #105 pull-based reporting |
| D2 · Dev-port ownership across worktrees | #103 |
| Dispatch & fresh-keyed wait | #98 |
| Test-ownership partition at P3 | #99 |

---

## D1 — Coordinator dispatch loop

While the single build pane runs a long task, the coordinator must neither leave every other pane idle
(wasted parallelism) nor "keep them busy" by dispatching every phase at once — a reviewer with **no diff**
or a verifier pointed at the **builder's live server** is worse than idle. Dispatch only INPUT-READY,
non-colliding, non-live-driving phases; HOLD the rest; surface the pull-based report trail so a
finished-and-logged pane doesn't read as stalled; roll quality the moment the builder is DONE.

```mermaid
flowchart TD
  B[Builder pane busy] --> S{An idle pane?}
  S -->|no| RPT
  S -->|yes| T1{Input-ready?<br/>diff / built page exists?}
  T1 -->|no| HOLD[HOLD — park it<br/>reviewer w/o diff · verifier w/o page]
  T1 -->|yes| T2{Non-colliding AND<br/>non-live-driving?}
  T2 -->|no| HOLD
  T2 -->|yes| DISP[Dispatch advisory/research only<br/>test-designer map · researcher scout]
  HOLD --> RPT[Report = show BOARD finish-lines + STATUS sentinels<br/>idle = done + reported, pull-based]
  DISP --> RPT
  RPT --> DN{Builder DONE?}
  DN -->|no| S
  DN -->|yes| ROLL[Rolling quality: auditor on diff +<br/>verifier as the ONE live driver]
```

Encodes **#96** (idle-pane triage — front-load only input-ready, non-colliding, non-live-driving phases;
HOLD the rest), **#26** (one live driver at a time), and **#105** ("idle" ≠ "silent" — render the
pull-based report trail so finished panes don't look unresponsive).

---

## D2 — Dev-port ownership across worktrees

Run multiple worktrees/crews in parallel and two dev servers default to the same port; the second to
start can **seize** it, so `:PORT` silently serves the *other* worktree's app — verification then asserts
against the wrong codebase (a 404 on a route only your branch has). Detect by IDENTITY (the listener's
parent-cwd), verify on your OWN free port, and FLAG — the naive "kill whatever's on the port" clobbers a
concurrent human session.

```mermaid
flowchart TD
  N[Need dev app on :PORT] --> L{lsof :PORT listening?}
  L -->|no| START[Start dev on $PORT for THIS worktree]
  L -->|yes| ID{listener parent-cwd == this worktree?}
  ID -->|yes| OK[Ours — use it]
  ID -->|no| SIB[Sibling worktree SEIZED the port]
  SIB --> ALT[Start on free alt port PORT+100, verify there]
  ALT --> FLAG[FLAG: PID + worktree to human<br/>NEVER kill the foreign server]
  START --> OK
```

Encodes **#103** (a sibling worktree can seize your dev port — detect by identity, verify on your own
port, flag never clobber).

---

## Dispatch & fresh-keyed wait

The crew-ops failure: a pane reused one result file across P3→P4, and the wait matched the **stale P3
sentinel** and returned instantly — nearly reporting P4 "done" off an old line. The guard: wait until the
file's **mtime is newer than the dispatch** AND a **phase-marker** is present; only then read + verify it
matches the dispatched phase. A sentinel is fresh only if the file changed AFTER you asked. The
`crew_wait.sh` helper enforces this via `CREW_WAIT_SINCE` (mtime) + `CREW_WAIT_GREP` (phase marker).

```mermaid
flowchart TD
    A["coordinator dispatches to an IDLE pane<br/>crew/dispatch.sh — RECORD dispatch time"] --> B["pane works, writes crew/role.md + STATUS sentinel"]
    B --> W{"FRESH-KEYED WAIT (#98)<br/>file mtime NEWER than dispatch AND phase-marker present?"}
    W -->|"no — only an OLD sentinel exists"| STALE["DO NOT ACT<br/>a stale prior-phase STATUS:DONE is a landmine — keep waiting"]
    STALE --> W
    W -->|"yes — genuinely fresh"| R["read + verify it matches the PHASE you dispatched"]
    R --> P{"implementer DONE?"}
    P -->|"yes"| ROLL["ROLLING PIPELINE (#41)<br/>auditor on git diff + verifier on suite/live IN PARALLEL<br/>while implementer takes the next unit"]
    P -->|"no"| NEXT["route the next phase"]
```

Encodes **#98** (fresh-keyed wait — never act on a stale prior-phase `STATUS: DONE`).

---

## Test-ownership partition at P3

When the implementer (TDD) and the verifier both produce tests, split by file so they never collide:

```mermaid
flowchart LR
    CM["coverage map<br/>test-designer — advisory, writes NO test code"] --> IMP["implementer (TDD)<br/>unit cases in the tests-suite file"]
    CM --> VER["verifier<br/>e2e/*.spec.ts — a NEW, DISJOINT file"]
    IMP -. "never the same file at once (#99)" .-> VER
```

Encodes **#99** (partition test ownership by file — `git diff --name-only` confirms zero overlap;
extends the single-code-owner rule to the test layer).
