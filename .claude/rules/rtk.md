# RTK — token-optimized command output (opt-in, guard-safe)

Last updated: 2026-08-09 20:45

> **Source of truth & sync.** Repo snapshot of the machine-global `~/.claude/rules/rtk.md` (via
> `sync-rules.sh`). Listed in `sync-rules.sh`'s `HAND_RECONCILED` — a brand-new rule with no existing
> global counterpart, kept out of the blind name-sync path until it's captured once (same precedent
> as `engineering-conventions.md` / `workflow-adherence.md`).

RTK ("Rust Token Killer") is an **optional** CLI proxy that compresses noisy command output before it
reaches the LLM. This template ships it **opt-in and OFF by default** — nothing about RTK is active
until a project enables it (`RTK_ENABLE=1` at scaffold, or `rtk init` later). Full doc:
[`docs/RTK.md`](../docs/RTK.md); decision: `docs/decisions/feat-rtk-template.md`.

## What to know when RTK is enabled

- **It's instructions, not a hook.** Project-level `rtk init` writes a "prefix commands with `rtk`"
  block into `CLAUDE.md` — it installs **no `PreToolUse` hook** (the real hook is `--global`-only,
  which this template does not do). RTK never sits in the tool-call chain, so it cannot mutate a
  command before the H1–H10 guards inspect it.
- **The safety guards still fire.** RTK's rewrite is additive (`git … → rtk git …`), so the
  template's deterministic guards (force-push to main, destructive DB ops, conventional-commit,
  read-only `rm -rf`, secret scans) still block the `rtk `-prefixed forms. This is pinned by
  `tests/test_rtk_guard_safety.sh` — if you change `pre_tool_use.*`, keep that test green.
- **Telemetry is forced OFF.** The template disables RTK telemetry on enable and never opts a
  downstream project in. Don't re-enable it in a scaffolded project without the user's explicit say.
- **Green ≠ accurate.** RTK summarizes output; a filter can occasionally drop a line you needed. When
  a compressed result looks wrong, re-run the command raw (no `rtk ` prefix) to compare. Never trust
  project-local `.rtk` filters from a repo you don't control (`rtk trust` is the gate; avoid
  `--trust-filters`).

## See also

`docs/RTK.md` (setup/enable/disable, mechanism, caveats) · `engineering-conventions.md` (R5 — RTK is
a justified opt-in dependency, not a default one) · `hooks/README.md` (the PreToolUse guard chain RTK
must not defeat) · `workflow.md` (GATE 2 — "green ≠ works" applies to RTK's compressed output too).
