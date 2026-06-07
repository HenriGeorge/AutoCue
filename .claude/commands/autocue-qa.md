Run the `autocue-qa` agent.

Boots `autocue serve` against a sandbox copy of master.db (verified via `/api/status` + `X-AutoCue-Diagnostic: 1`), runs the Playwright smoke suite (safety + selectors + API + SSE + UI + Pages mode), the **per-control sweep** (one Playwright test per row in `tests/e2e/control-inventory.json` — ~75 chrome controls + sampled per-track buttons), and the Chrome DevTools **documented feature sweep** (every user-facing flow in `docs/reference/`: cue-generation, library-health, comment-enrichment, cue-library-tools, set-builder, similar-tracks, transition-scoring, playlist-suggest, auto-tag, discogs-and-discovery, youtube-download).

Args:

- `/autocue-qa` — no arg, sweep all three panels (Cues + Library + Discover) plus global controls.
- `/autocue-qa cues` — only the Cues panel rows (plus globals).
- `/autocue-qa library` — only Library.
- `/autocue-qa discover` — only Discover.
- `/autocue-qa cues library` — multiple panels; duplicates dedup silently; unknown tokens refuse with the allowlist.
- `/autocue-qa --dry-run [<panel>...]` — write the report only; skip every `gh` mutation.

The agent sets `AUTOCUE_QA_SCOPE` from the panel args; the per-control spec reads it. Allowlist is hardcoded `{cues, library, discover}` and the source of truth for which panels exist lives in `tests/e2e/control-inventory.json` — update both together if a 4th tab is added.

Files GitHub issues for failures with fingerprints keyed to the reference doc (documented-feature sweep) or the panel + control id (per-control sweep). Issue filing requires one-time per-repo consent and is capped at 10 issues per run.

Summary lands in `.claude/reports/autocue-qa-<date>.md`.
