# QA Fixer (`/autocue-fixer`)

Companion to [`/autocue-qa`](./qa_tester.md). The QA agent files issues with
stable fingerprints; the fixer turns those issues into PRs. Two halves of the
same loop.

```text
/autocue-fixer             # all open `bug`-labelled issues
/autocue-fixer 16          # one issue
/autocue-fixer 15 16       # several specific issues
/autocue-fixer --dry-run   # plan only; no `gh` writes, no `git push`
```

There is no `scripts/dispatch.sh`. The slash command, a sub-agent prompt, and
a single Workflow script are the entire mechanism.

---

## 1. End-to-end run

```mermaid
flowchart TD
    A[/autocue-fixer invoked/] --> B[Stale marker reap<br/>~/.claude/state/fixer-running > 6h]
    B --> C[Touch marker<br/>.claude/state/fixer-running]
    C --> D[Invoke Workflow<br/>.claude/workflows/autocue-fixer.js]

    D --> E1[Phase 1: Fetch<br/>gh issue list / explicit args]
    E1 --> E2[Phase 2: Dedup<br/>state + PR head + Closes #N]
    E2 --> E3[Phase 3: Group<br/>file-overlap analysis]
    E3 --> E4[Phase 4: Fix fan-out<br/>one isolated agent per issue]
    E4 --> E5[Phase 5: Merge plan<br/>detect PR overlaps]
    E5 --> E6[Phase 6: Safety scan<br/>grep diffs for forbidden patterns]

    E6 --> F[Trap: rm marker file]
    F --> G[/Done/]

    style B fill:#5f8dd3,color:#fff
    style E2 fill:#5f8dd3,color:#fff
    style E6 fill:#e4384e,color:#fff
```

---

## 2. Closed loop with `/autocue-qa`

The load-bearing semantics: the QA agent's dedup logic only **searches** for
a fingerprint when a failure is detected.

```mermaid
flowchart LR
    QA[/autocue-qa<br/>files issue with fingerprint/] --> backlog[(GitHub issues)]
    backlog -->|/autocue-fixer N| FX[/autocue-fixer<br/>investigates + fixes/]
    FX --> PR[PR with Closes #N]
    PR -->|human merge| main[main]
    main -->|next QA run| QA
    QA -->|no failure ⇒ no fingerprint search| done[Issue stays closed<br/>= fix held]
    QA -->|same-sig regression ⇒<br/>comment on CLOSED issue| regress1[REGRESSION flagged<br/>on the closed issue]
    QA -->|different-sig regression ⇒<br/>NEW issue filed| regress2[NEW issue in backlog<br/>= adjacent break]

    style done fill:#0e8a16,color:#fff
    style regress1 fill:#e4384e,color:#fff
    style regress2 fill:#f0801a,color:#fff
```

- **Silence on the closed issue** = the fix held.
- A `Reoccurred on …` comment on the closed issue = same-signature regression
  (the exact `abrupt-eof` came back).
- A brand-new issue with the same `<surface>:<test-id>` but a different `<sig>`
  = a regression in the same family but a different failure class. Monitor
  both paths.

---

## 3. Per-issue agent — Phase 0 → 4

Each per-issue fix-worktree runs the sub-agent at
`.claude/agents/autocue-fixer.md`. Spawned via the Workflow's
`agent(..., { isolation: "worktree" })` so the worktree is brand new and the
branch is fresh.

```mermaid
flowchart TD
    A[Phase 0: Pre-flight] --> A1[gh issue view N]
    A --> A2[Search 'fixes #N' on existing PRs]
    A --> A3[Two gh pr list calls<br/>head exact + prefix]
    A --> A4[git log main grep 'Closes #N']
    A --> A5[Sandbox reap<br/>PID-alive AND mtime > 6h]

    A1 --> B[Phase 1: Investigate]
    A2 --> B
    A3 --> B
    A4 --> B
    A5 --> B

    B --> B1[Parse fingerprint<br/>surface:test-id:sig]
    B --> B2[Cross-reference<br/>docs/reference/]
    B --> B3[Write investigation PRP]
    B --> B4[git branch -M fix/N-slug<br/>+ git reset --hard origin/main]

    B4 --> C{Phase 2:<br/>Validate loop<br/>max 10}
    C -->|iter k| C1[Implement / fix]
    C1 --> C2[Re-Read every edited file]
    C2 --> C3[Run dirty legs only]
    C3 -->|fail| C
    C3 -->|all clean| D[Phase 3:<br/>Quality + commit]

    D --> D1[Self-review →<br/>Phase 3.5 review PRP]
    D1 --> E[Phase 4: PR]
    E --> E1[git push -u origin]
    E --> E2[gh pr create<br/>+ Closes #N]
    E --> E3[gh pr checks --watch]
    E --> E4[Comment on issue<br/>with PR link]

    style A5 fill:#5f8dd3,color:#fff
    style C fill:#f0801a,color:#fff
    style E fill:#0e8a16,color:#fff
```

### Phase 2 — three test legs, touch-log skip

```mermaid
flowchart LR
    iter[Iteration start] --> tl{Touch log<br/>per leg}
    tl -->|Leg A clean| skipA[skip pytest]
    tl -->|Leg A dirty| runA[pytest -x -q]
    tl -->|Leg B clean| skipB[skip vitest]
    tl -->|Leg B dirty| runB[npm test]
    tl -->|Leg C clean| skipC[skip e2e]
    tl -->|Leg C dirty| runC[tests/e2e npm test]

    runA --> done
    runB --> done
    runC --> done
    skipA --> done
    skipB --> done
    skipC --> done

    shared[Shared roots touched?<br/>pyproject.toml / package.json /<br/>tests/conftest.py / playwright.config.ts /<br/>.claude/fixer.yaml] -->|yes| forceAll[Force ALL legs this iter]
    iter --> shared
    forceAll --> done

    done{All run legs green?}
    done -->|yes| commit[→ Phase 3: commit]
    done -->|no| fix[Read error, fix, loop]
    fix --> iter

    style shared fill:#f0801a,color:#fff
    style forceAll fill:#e4384e,color:#fff
```

---

## 4. Workflow dependency grouping

The Workflow groups multiple issues by **file overlap** so parallel agents
don't collide. `docs/index.html` is **excluded from the overlap key** because
every UI bug names it — grouping on it would serialize the most common bug
class.

```mermaid
flowchart TD
    I[Issues: 15, 16, 22, 31] --> X[Extract file paths from each body<br/>regex covers py, ts, js, html, css,<br/>md, yaml, yml, toml, json]
    X --> S[Strip docs/index.html<br/>from overlap key]
    S --> G{Pairwise overlap?}
    G -->|none| par[All parallel<br/>= 4 worker slots]
    G -->|some| grp[Group with overlap<br/>= sequential within,<br/>parallel across groups]

    par --> fan[pipeline fan-out]
    grp --> fan
    fan --> merge[Phase 5: Merge plan<br/>detect PR-level overlaps,<br/>log to dispatch-coordination.log]

    style S fill:#5f8dd3,color:#fff
    style merge fill:#0e8a16,color:#fff
```

---

## 5. Safety preflight (HARD rules)

```mermaid
flowchart TD
    start[Sub-agent boots] --> hr{HARD rule<br/>trigger?}
    hr -->|fix needs real master.db| refuse1[Refuse + gh issue comment]
    hr -->|fix bypasses<br/>rekordbox_is_running| refuse2[Refuse + comment]
    hr -->|fix commits .env/creds/<br/>Library/Pioneer| refuse3[Refuse + comment]
    hr -->|fix widens CORS| refuse4[Refuse + comment]
    hr -->|fix removes feature row<br/>from qa_tester.md| refuse5[Refuse + comment]
    hr -->|fix uses --force / reset --hard /<br/>--no-verify| refuse6[Refuse + comment]
    hr -->|none triggered| proceed[Proceed to Phase 1]

    proceed --> end0[Phase 0–4 runs]
    end0 --> post[Workflow Phase 6:<br/>grep PR diff for<br/>.env credentials password<br/>api_key secret Library/Pioneer<br/>master.db]
    post -->|match| block[Add safety:blocked label<br/>+ gh pr comment<br/>+ log coordination<br/>DO NOT auto-close PR]
    post -->|clean| green[PR ready for human merge]

    refuse1 --> stop
    refuse2 --> stop
    refuse3 --> stop
    refuse4 --> stop
    refuse5 --> stop
    refuse6 --> stop
    stop[Stop. No branch, no PR.]

    style hr fill:#f0801a,color:#fff
    style post fill:#5f8dd3,color:#fff
    style block fill:#e4384e,color:#fff
    style green fill:#0e8a16,color:#fff
```

The post-fix safety scan in the Workflow is **independent of the agent's
self-assessment**. LLM agents can violate HARD rules; the deterministic grep
catches it post-hoc.

---

## 6. Issue dedup — the three checks

```mermaid
flowchart LR
    issue[#N] --> c1{Still open?}
    c1 -->|CLOSED| skip[Skip]
    c1 -->|OPEN| c2{PR fix/N exists?<br/>gh pr list --head<br/>EXACT and PREFIX,<br/>two separate calls}
    c2 -->|yes| skip
    c2 -->|no| c3{git log main<br/>grep 'Closes #N'?}
    c3 -->|yes| close[gh issue close +<br/>'Fixed on main: SHA']
    c3 -->|no| proceed[→ Phase 0 sub-agent]

    style c2 fill:#5f8dd3,color:#fff
```

Why TWO `gh pr list --head` calls? Because `gh` honours only one `--head` per
invocation. The first matches `fix/<N>` exactly; the second matches the
prefix `fix/<N>-` for slug-suffixed branches.

---

## 7. Hooks

The fixer relies on hooks already registered in `.claude/settings.json`:

| Hook | Event | Purpose for the fixer |
|---|---|---|
| `pre_tool_use_branch_check.py` | PreToolUse | Blocks commits on `main`. Trust it. |
| `safe_git_add.sh` | PreToolUse (Bash) | Blocks `git add -A`. Trust it. |
| `stop_log.py` | Stop | Logs unpushed-work reminders. **Optional bypass.** |

### Optional `stop_log.py` bypass

If `stop_log.py`'s reminder text starts polluting agent context during fixer
runs, add this snippet at the top of `.claude/hooks/stop_log.py` (the hook
lives outside git):

```python
from pathlib import Path
if Path(".claude/state/fixer-running").exists():
    raise SystemExit(0)
```

The slash command body already creates `.claude/state/fixer-running` before
the Workflow runs and deletes it in a trap. The marker is a filesystem flag
— any process (parent slash command, Workflow runner, sub-agents) sees the
same answer. Whether the project Stop hook fires on sub-agent Stops is
runtime-dependent; the marker works regardless.

---

## 8. PRP artifact lifecycle

Each fix writes three files under `.claude/PRPs/`:

- `issues/<num>-<slug>.investigation.md` — root cause + proposed solution
- `reviews/<num>-<slug>.review.md` — self-review verdict + verification
- `reports/<num>-<slug>.report.md` — changes + tests + validation summary

These are **tracked in git** because they're evidence on the PR.
**Squash-merge preserves the files** but loses the per-iteration commit
chronology — that's the chosen tradeoff (per-iter trail is rarely useful
post-merge; the artifacts themselves are).

Periodic cleanup (~monthly): move resolved artifacts to
`.claude/PRPs/archive/YYYY-MM/` so the live directories don't pile up.

---

## 9. Where things live

| Concern | File |
|---|---|
| Slash command | `.claude/commands/autocue-fixer.md` |
| Sub-agent prompt | `.claude/agents/autocue-fixer.md` |
| Workflow script | `.claude/workflows/autocue-fixer.js` |
| Config | `.claude/fixer.yaml` |
| Marker file (runtime) | `.claude/state/fixer-running` (gitignored) |
| Stop hook bypass (optional) | `.claude/hooks/stop_log.py` |
| Coordination log | `.claude/reports/dispatch-coordination.log` |
| PRP artifacts | `.claude/PRPs/{issues,reviews,reports}/` |
| User doc (this file) | `docs/qa_fixer.md` |
| Companion doc | `docs/qa_tester.md` |

---

## 10. Maintenance

- When `docs/reference/` changes a documented behavior, the corresponding
  fingerprint stops firing and the fixer's investigation artifact will note
  the doc as the source.
- When CI changes test commands (new lint step, etc.), update
  `.claude/fixer.yaml` `test_cmd_*` keys.
- Coordination log noise: archive `.claude/reports/dispatch-coordination.log`
  periodically; it grows unboundedly.
- If `safe_git_add.sh` rejection rate spikes, audit the agent prompt — it
  probably learned to retry with `-A`, which is wrong.
