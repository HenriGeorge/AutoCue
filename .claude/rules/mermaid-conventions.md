# Mermaid-where-possible (authoring convention)

Last updated: 2026-08-09 21:55

> **Source of truth & sync.** Repo snapshot of the machine-global `~/.claude/rules/mermaid-conventions.md`
> (via `sync-rules.sh`). Listed in `sync-rules.sh`'s `HAND_RECONCILED` — captured once to its global counterpart (2026-08-09); now
> hand-maintained on BOTH sides (kept in `HAND_RECONCILED` so the blind name-sync won't clobber it).

**Default to an inline Mermaid diagram over a committed image whenever you're describing a flow,
a state machine, or an architecture/dependency shape.** Prose alone forces the reader to
reconstruct the shape in their head; a screenshot/PNG goes stale the moment the thing it depicts
changes and nobody remembers to regenerate it. A Mermaid block lives in the same file as the prose
it supports, diffs like text, and renders natively wherever Markdown does (GitHub, this repo's
docs, Claude Code's own rendering).

## When to reach for it

- **Flows** — a sequence of phases/steps with a clear order (`flowchart LR`/`TD`), e.g. the design
  or verify pipeline diagrams in `design-workflow.md` / `verify-workflow.md`.
- **State** — anything with named states and transitions between them (`stateDiagram-v2`), e.g. a
  hook's ALLOW/DENY decision tree, a UI component's loading/empty/error/loaded states.
- **Architecture / dependency shape** — components and how they call each other (`flowchart`/`graph`),
  e.g. which service calls which, or how a request crosses layers.

## When NOT to

- A single linear list with no branching or state (a plain numbered list reads faster).
- A one-off, throwaway note not meant to outlive the current conversation.
- Anything that would need to encode real pixel layout/spacing — that's a screenshot of the
  rendered artifact (see `design-workflow.md`'s GATE-1 rendered-design requirement), not a diagram.

## How

- Fence with ` ```mermaid ` (this repo's Markdown renderers, including Claude Code's own, render it
  inline — no external tool, no committed image asset).
- Keep node labels short; put detail in surrounding prose, not crammed into the box.
- Prefer `flowchart LR` for a left-to-right pipeline (matches how most of this repo's phase
  diagrams already read) and `stateDiagram-v2` for anything with a real ALLOW/DENY or
  loading/success/error branch — don't force a flowchart to represent state transitions.
- Keep the diagram and the prose it accompanies in sync — if you touch one, check the other still
  matches (same discipline as any other doc-impact check, see `workflow.md`'s DOCUMENT phase).

## See also

`design-workflow.md` (its SHAPE→BUILD pipeline is the canonical example already using this
pattern) · `verify-workflow.md` (its STATIC→CLAIM pipeline, same pattern) · `workflow.md` (the
top-level 9-phase diagram this convention is modeled on).
