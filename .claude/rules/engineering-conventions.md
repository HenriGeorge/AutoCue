# Engineering conventions (R4–R7)

Last updated: 2026-08-14 18:30

> **Source of truth & sync.** Repo snapshot of the machine-global `~/.claude/rules/engineering-conventions.md`
> (via `sync-rules.sh`). Listed in `sync-rules.sh`'s `HAND_RECONCILED` — captured once to its global counterpart (2026-08-09); now
> hand-maintained on BOTH sides (kept in `HAND_RECONCILED` so the blind name-sync won't clobber it).

Three baseline engineering habits that don't fit `workflow.md`'s phase spine but apply at every
phase where they're relevant. None of these are deterministically checkable at a tool call (see
`hooks/README.md`'s boundary test) — they're judgment calls a careful agent makes, not something a
hook can hard-block.

## R4 — Prefer editing over creating

Before writing a new file, check whether an existing one already owns this concern. A second
`utils.ts`, a parallel `types/` directory, a duplicate config file — these fragment a codebase and
create "which one is canonical?" confusion for the next person (human or agent) who touches it.

- Grep/glob for existing files that plausibly already cover the thing you're about to write before
  creating a new one. `context-map` (this repo's file-discovery skill) is the tool for this.
- Extending an existing module's surface (a new exported function, a new case in a switch, a new
  route in an existing router file) beats a new file that duplicates the module's purpose.
- A genuinely new concern *does* warrant a new file — this isn't "never create," it's "check
  first." The tell: if you're about to write something that could plausibly have gone in a file
  you already have open, look there first.
- Never create documentation files unless the user explicitly asked for one (`CLAUDE.md` project
  instructions already state this for `*.md`/README files specifically — this generalizes it: the
  same "did they ask for this artifact" check applies to code files too).

## R5 — Justify new dependencies

A new package is a standing cost: supply-chain surface, a version to keep patched, a transitive
tree the next audit has to reason about, and (for a template like this repo) a dependency every
scaffolded project inherits.

- Before adding a dependency, check whether the standard library or an already-present dependency
  already does this. A one-off string-manipulation helper doesn't need a package; a well-tested
  crypto primitive almost always should use one rather than hand-rolling it — the tradeoff runs
  BOTH directions, weigh it, don't default to either extreme.
- State the reason in the commit/PR, not just the dependency name — "why this one, why now" beats
  a bare `package.json` diff. A reviewer (human or `code-reviewer`) shouldn't have to guess.
- Prefer a dependency that's actively maintained, has a reasonable install-size footprint for what
  it's used for, and doesn't itself pull in a large transitive tree for a small piece of
  functionality.
- This project (`claude_template`) in particular: `setup.sh`/hooks are deliberately dependency-free
  (stdlib-only Python, Node core modules only) — see the `# dependencies = []` header on the
  runtime hooks (`hooks/*.py`) — because every scaffolded project inherits whatever the template hooks need.
  A new hook dependency isn't a "just this once," it's a cost on every future scaffold.

## R6 — Security basics

Baseline hygiene that applies regardless of stack or profile:

- **No secrets in code — enforced by H3 (staged-diff secret scan) + H10 (secret-file staging); see
  `hooks/README.md`.** Use env vars / a secrets manager / a gitignored `.env`; the hooks are a
  backstop, not a substitute for not writing the secret.
- **Validate/sanitize external input.** Anything crossing a trust boundary — a request body, a
  query param, a file upload, an env var sourced from outside your own deploy config — gets
  validated before use, not trusted implicitly. This includes output encoding at the point of use
  (SQL params, not string-concatenated queries; HTML-escaped interpolation, not raw string
  insertion) — see `/security-review` for the deeper pass.
- **Least-privilege tokens.** A token/credential should carry the minimum scope the task needs —
  a read-only DB user for a reporting job, not the admin credential; a repo-scoped GitHub token,
  not an org-wide one. When creating a new credential, ask "what's the narrowest scope that still
  works" before reaching for the broadest one that's convenient.

## R7 — Avoid unnecessary work (Ponytail)

Do what was asked; resist the pull to do more. Before adding, ask "was this requested, and does
it serve the current goal?" Concrete failure modes to stop yourself on:

- Building features/options nobody asked for; speculative abstraction for a second use case that
  doesn't exist yet; gold-plating a working solution.
- Refactoring beyond the change's scope; adding a new dep or file when an existing one suffices
  (that's R4/R5 — this is the general habit behind them).

Ship the smallest thing that fully satisfies the request. Pairs with `lean-output.md` (Caveman) —
Ponytail cuts unnecessary *work*, Caveman cuts unnecessary *words*. See also: brainstorming's
"YAGNI ruthlessly."

Two self-checks that keep this honest:
- **The overcomplication check:** before calling a change done, ask *"would a senior engineer say
  this is overcomplicated?"* If yes, rewrite it smaller — if 200 lines could be 50, it's the 50.
- **Make success verifiable, per step:** for a multi-step task, state a brief numbered plan where
  each step carries its own check — `1. <step> → verify: <check>`. Strong, checkable criteria let
  you loop to done independently; a weak "make it work" forces constant re-clarification.

## See also

`hooks/README.md` (the enforcement-vs-instruction boundary — R6's secrets point is partially
backstopped by H3/H10, but the rule itself is the judgment call, the hook is the safety net) ·
`gates.md` (GATE 2's silent-failure check overlaps R6's "validate external input" — a swallowed
error on unvalidated input is a related failure mode) · `/security-review` (the deeper security
pass this rule is a baseline for, not a replacement for).
