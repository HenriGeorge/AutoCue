# RTK — token-optimized command output (opt-in)

Last updated: 2026-08-10 01:49

RTK ("Rust Token Killer") is an optional CLI proxy that compresses the output of common dev commands
(git, cargo/npm/pnpm, test runners, linters, psql, aws, …) **before it reaches the LLM context** —
typically 60–90% fewer output bytes on noisy commands. It's a single Rust binary with no runtime
dependencies. This template ships RTK **opt-in and OFF by default**; nothing about RTK is active in a
project until you enable it.

> Decision record: [`docs/decisions/feat-rtk-template.md`](decisions/feat-rtk-template.md).
> Rule (auto-loaded): `.claude/rules/rtk.md`.

## How it integrates (important: no hook at project scope)

At **project scope**, `rtk init` is **instruction-based**: it writes a `<!-- rtk-instructions -->`
block into `CLAUDE.md` ("Golden Rule: prefix commands with `rtk`") and a `.rtk/filters.toml`
template. It does **NOT** install a `PreToolUse` hook — verified: `rtk init --hook-only` only applies
with `--global`. So RTK never sits in this project's tool-call chain; Claude simply *chooses* to run
`rtk git status` instead of `git status`, and RTK's rewrite is purely **additive** (it prepends
`rtk `; unknown commands pass through unchanged).

Consequence for safety: the template's `PreToolUse` guard chain (H1–H10 in
`.claude/hooks/pre_tool_use.py`) still inspects the real command Claude runs. Because the `rtk `
prefix never hides the dangerous substring, every guard still fires on the prefixed form — a
force-push to `main`, a destructive `psql` DROP, a non-conventional commit, and `rm -rf` on a
read-only path are all still blocked with RTK enabled. This is proven and pinned by
`tests/test_rtk_guard_safety.sh` (Python + Node parity).

The global `rtk init -g` mode (a real `PreToolUse` hook in `~/.claude/settings.json`, affecting every
project) is **out of scope for this template** — that's a personal, machine-wide choice, not a
per-project one.

## Enable / disable

```bash
# Prerequisite (assumed on PATH, like cc-worktrees — the template does NOT vendor it):
brew install rtk            # or: cargo install rtk

# At scaffold time: answer [y] to "Enable RTK output compression?", or preset RTK_ENABLE=1.
# Later, in an existing project:
RTK_TELEMETRY_DISABLED=1 rtk init      # writes the CLAUDE.md block + .rtk/filters.toml
rtk telemetry disable                  # belt-and-suspenders (see Telemetry)

# Disable: remove the rtk-instructions block from CLAUDE.md and delete the .rtk/ directory.
rtk init --show                        # inspect current state
```

`permissions.allow` in the scaffold always includes `Bash(rtk *)` so the agent can invoke RTK without
a prompt — inert when RTK is disabled.

## Telemetry — forced OFF

RTK telemetry is opt-in upstream (anonymous daily ping, GDPR-compliant, no source/paths/args/secrets).
This template **forces it off** whenever it runs `rtk init` (`RTK_TELEMETRY_DISABLED=1` + an explicit
`rtk telemetry disable`), so a scaffolded project never opts a downstream user in. Hard override any
time: `export RTK_TELEMETRY_DISABLED=1`. Erase local data: `rtk telemetry forget`.

## Caveats

- **Green ≠ works.** RTK filters/summarizes output — occasionally a filter can drop a line you needed.
  If a command's compressed output looks wrong, re-run it raw (drop the `rtk ` prefix) to compare.
- **Custom filters need trust.** Project-local `.rtk/filters.toml` filters are trust-gated
  (`rtk trust`); never `--trust-filters` on a repo you don't control.
- **Savings are output-bytes, not your bill.** Bash output is one input among prompt + history +
  system prompt; RTK reduces one contributor, it doesn't linearly cut cost.
