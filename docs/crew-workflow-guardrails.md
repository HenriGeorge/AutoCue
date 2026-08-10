# Crew workflow guardrails

Last updated: 2026-08-09 21:28

Decision diagrams that turn a `cc-worktrees` crew session's recurring frictions into **branch logic**.
A retrospective lesson in [`lessons.md`](lessons.md) records *what* went wrong; the diagram below makes
the failure mode structurally hard to repeat. These are **crew-mechanism** guardrails — they apply when
a crew is running (one coordinator pane driving Agent-tool teammates over SendMessage). They
complement the coordinator's own methodology (`write_coordinator_prompt` in
[`../bin/cc-worktrees`](../bin/cc-worktrees), generated into `crew/prompts/coordinator.md`) and the
shared per-worker methodology (`_crew_methodology`, same file). The universal spine stays in
[`WORKFLOW.md`](WORKFLOW.md) / [`VERIFY-WORKFLOW.md`](VERIFY-WORKFLOW.md).

Inline Mermaid is canonical here (the repo's docs convention — no committed images).

| Guardrail | Encodes |
|---|---|
| No idle teammate — fill or park before you wait | no-idle-wait / idle-triage / one-live-driver / autonomy-contract |
| Dev-port ownership across worktrees | #103 |
| Test-ownership partition at P3 | #99 |
| N-instance crews — count writers, not agents | #162 / #163 |
| Shutdown handshake by agent-type | #156 |

---

## No idle teammate — fill or park before you wait

This is the decision-diagram restatement of the **no-idle-wait / idle-triage** discipline; the
authoritative version is ENCODED IN the coordinator methodology (`write_coordinator_prompt` →
`crew/prompts/coordinator.md`) — this doc is the human-facing diagram, NOT a competing authority.

**★ HARD GATE.** Letting a teammate sit idle with ready work while you wait on another is a GATE
VIOLATION on par with GATE-1/GATE-2 — the #1 coordinator failure. In crew replies return
automatically (SendMessage — never poll), so "waiting" means *proceeding while a teammate works*:
before you settle into that, check the BOARD and, for EVERY idle teammate, SendMessage its next
input-ready / non-colliding / non-live-driving task — or explicitly **park** it with a one-line reason
on the BOARD.

**IDLE-FILL LADDER** — per idle teammate, assign the FIRST applicable: **rolling** (audit/verify the
last diff) → **pre-spec N+1/N+2** (research / test-design the next units) → **on-call research** (an
open data/surface question) → **park** (none apply — park with a reason; idle is not silent — surface
the pull-based report trail).

```mermaid
flowchart TD
  W[A teammate is working — before you proceed to wait on it] --> SCAN[check the BOARD — enumerate every teammate's state]
  SCAN --> S{An idle teammate?}
  S -->|no| GO[proceed — SendMessage replies return automatically, never poll]
  S -->|yes| T1{Input-ready?<br/>a diff / built page / open question exists?}
  T1 -->|no| HOLD[park it with a one-line reason on the BOARD]
  T1 -->|yes| T2{Non-colliding AND<br/>non-live-driving?}
  T2 -->|no| HOLD
  T2 -->|yes| LAD[SendMessage a task by ladder:<br/>rolling → pre-spec N+1/N+2 → on-call research]
  HOLD --> S
  LAD --> S
  GO --> DN{Builder reports DONE?}
  DN -->|no| W
  DN -->|yes| ROLL[Rolling quality: auditor on the diff +<br/>verifier as the ONE live driver, in parallel]
```

Encodes the **no-idle-wait** hard gate, **idle-triage** (only input-ready, non-colliding,
non-live-driving work), **one-live-driver** (only one teammate drives the live app at a time), and
**idle ≠ silent** (surface the pull-based report trail so a finished teammate never looks stuck).

**AUTONOMY CONTRACT.** The coordinator runs autonomously; the ONLY three sanctioned human pauses are
(a) **GATE-1 design sign-off**, (b) **cannot-converge** — GATE-2 still red after a bounded retry
budget, and (c) a genuine **scope fork** / destructive op / missing credential. Everything else
proceeds and reports — no "is this ok?" round-trips.

---

## Dev-port ownership across worktrees

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

## Test-ownership partition (COVER/P2 + P3)

The verifier's first failing `e2e/*.spec.ts` is written at COVER (P2) and made green in BUILD (P3);
the implementer's unit cases grow during BUILD. When both produce tests, split by file so they never
collide — the partition holds regardless of which phase writes first:

```mermaid
flowchart LR
    CM["coverage map<br/>test-designer — advisory, writes NO test code"] --> IMP["implementer (TDD)<br/>unit cases in the tests-suite file"]
    CM --> VER["verifier<br/>e2e/*.spec.ts — a NEW, DISJOINT file"]
    IMP -. "never the same file at once (#99)" .-> VER
```

Encodes **#99** (partition test ownership by file — `git diff --name-only` confirms zero overlap;
extends the single-code-owner rule to the test layer).

---

## N-instance crews — count writers, not agents

**#162/#163: the test-ownership partition, one level up** — multiple writers, disjoint FILES
becomes multiple implementers, disjoint WORKTREES. The full lifecycle (numbers = order; verified
end-to-end by two live runs — the claude_template acceptance trial and the fresh-project rollout
verification, both GO):

```mermaid
flowchart TD
    subgraph project["ONE PROJECT — one coordinator · one DB · one test lock · one merge queue"]
      CO["COORDINATOR<br/>records + merge queue — never edits source"]
      subgraph wtA["worktree feat/story-a · PORT 3001"]
        IA["implementer-a<br/>sole writer HERE"]
      end
      subgraph wtB["worktree feat/story-b · PORT 3002"]
        IB["implementer-b<br/>sole writer HERE"]
      end
      AU["auditor(s) — read-only<br/>share any worktree"]
    end
    CO -->|"① cc-worktrees add feat/story-a"| wtA
    CO -->|"① cc-worktrees add feat/story-b"| wtB
    CO -->|"② spawn + select-pane -T implementer-a"| IA
    CO -->|"② spawn + title implementer-b"| IB
    IA -->|"③ TDD · lock-held suite<br/>result → crew/implementer-a.md"| DA["diff A<br/>feat/story-a"]
    IB -->|"③ result → crew/implementer-b.md"| DB["diff B<br/>feat/story-b"]
    DA -->|"④ NAMED input: branch + range + file"| AU
    DB -->|"④"| AU
    AU -->|"findings → crew/auditor-&lt;slug&gt;.md"| CO
    DA --> MQ
    DB --> MQ
    MQ{"⑤ MERGE QUEUE — serial<br/>re-verify base AT merge time<br/>merge-base --is-ancestor per story"}
    MQ -->|"green + audit clean"| MAIN[("main")]
    MAIN --> GATE["⑥ DELIVERABLE-EXISTENCE GATE<br/>commits on origin · PR # on BOARD · records committed"]
    classDef gate fill:#ffe9e9,stroke:#d33,stroke-width:2px,color:#333;
    class MQ,GATE gate;
```

The six numbered legs: **①** the COORDINATOR provisions each extra implementer's worktree with
`cc-worktrees add` (worktree + free PORT + env carry-in — no session, no claude; a teammate never
runs cc-worktrees itself, the create default spawns a whole crew); **②** spawn + `tmux
select-pane -T <role>-<slug>` — the title is how reconcile-on-resume tells twin panes apart;
**③** each instance writes the coordinator-NAMED result file (`crew/<role>-<slug>.md`); **④** an
auditor's input is named, never inferred — branch + diff range + result file per assignment (two
auditors on ONE diff through different lenses is diversity, not duplication); **⑤** merges are
serial with the base re-verified at merge time (a comparison taken earlier is a photograph, not a
fact — fresh PR for post-merge follow-ups); **⑥** nothing is "done" until the work provably exists
outside the worktrees.

**When to scale — and when not to:**

```mermaid
flowchart LR
    Q{"need more throughput?"} -->|"more review lenses"| A["add AUDITORS — free<br/>read-only · share worktrees<br/>correctness vs security on ONE diff"]
    Q -->|"parallel stories"| B{"file-disjoint AND<br/>not DB-heavy?"}
    B -->|yes| C["add an IMPLEMENTER<br/>= its OWN worktree via cc-worktrees add<br/>coordinator provisions, teammate never"]
    B -->|no| D["DON'T scale —<br/>one DB · one lock · one PR queue<br/>parallel bulk work = negative throughput"]
    classDef stop fill:#ffe9e9,stroke:#d33,stroke-width:2px,color:#333;
    class D stop;
```

---

## Shutdown handshake — crew-native ack vs general idle-out

Only ONE of the 5 worker roles is a **crew-native** agent type built specifically for this
methodology; the other 4 map to **general** subagent types that were never built to speak the
`shutdown_request` → ack protocol at all. A coordinator that waits for an ack from a general type
would wait forever — this is the failure mode lesson #156 records.

| Crew role | `subagent_type` | Sends a shutdown ack? |
|---|---|---|
| `implementer` | `crew-implementer` (crew-native) | **Yes** — `STATUS: DONE — standing down` |
| `researcher` | `codebase-explorer` (general) | No — emits `idle_notification` instead |
| `auditor` | `code-reviewer` (general) | No — emits `idle_notification` instead |
| `test-designer` | `test-designer` (general) | No — also has no Bash tool, can't even BOARD-append |
| `test-verifier` | `playwright-tester` (general) | No — emits `idle_notification` instead |

```mermaid
flowchart TD
  SR[Coordinator sends shutdown_request to a teammate] --> WHO{Which agent type?}
  WHO -->|crew-native — implementer| ACK[Expect STATUS: DONE — standing down<br/>on its NEXT reply]
  WHO -->|general — researcher/auditor/<br/>test-designer/test-verifier| NOACK[No ack is coming — structurally never sent<br/>these types emit idle_notification, not a shutdown ack]
  ACK --> GOT{Ack received?}
  GOT -->|yes| DONE1[Stop tracking — teammate is down]
  GOT -->|no| RESEND[Re-send shutdown_request ONCE]
  NOACK --> RESEND
  RESEND --> GOT2{Ack received this time?}
  GOT2 -->|yes| DONE1
  GOT2 -->|no| BLOCKED["Mark BLOCKED-shutdown on crew/BOARD.md and STOP<br/>(never re-send indefinitely)"]
  BLOCKED --> REAP["cc-worktrees rm reaps the idle pane at session teardown —<br/>this is the EXPECTED outcome for general types, not a hang"]
```

**Decision note:** don't treat a second no-ack as an anomaly to debug — for 4 of the 5 roles it's
the *only possible* outcome, by construction. The re-send-once-then-BLOCKED-then-STOP rule already
in the coordinator's own methodology (`_crew_coordinator_methodology`, `bin/cc-worktrees`) is
sufficient; the fix here is purely in the REASONING each side carries, not new mechanics — the
worker-side tail (`_crew_methodology`) is now role-conditional (only `implementer` gets the ack
instruction; the other 4 are told plainly they don't need to send one), and the coordinator-side
text now states explicitly *why* a second no-ack is expected rather than alarming for those roles.
