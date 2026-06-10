# Issue #172 — `beats-only-cb` click hangs / 30s Playwright timeout

## Problem

In `qa-smoke.spec.ts >> filter toggles do not crash the page` the test
fills the search box, toggles `#phrase-only-cb` on then off, then toggles
`#beats-only-cb` on. The third click never completes — Playwright reports
"performing click action" but the click action itself never returns,
ending in a 30 s test timeout. Side-effect of the cumulative work, not a
bug specifically in the beats-only filter handler.

Repro head: `e7d87cb307a1afb005808c3b61cffa6a10065e5f`.
Library size at repro: 3,775 tracks, default `sort_by = 'album'`.

## Root cause

`docs/index.html`:

- Both filter handlers (`#phrase-only-cb`, `#beats-only-cb`,
  `#audio-only-cb`, search) call `AppState.signal('filters')`
  (lines 8873–8891, 8865–8870).
- `AppState.signal` coalesces signals fired in the same microtask
  (line 4035) — but each Playwright synthetic click is its own task,
  so back-to-back `check()`/`uncheck()` produce one render per click.
- The subscriber `AppState.subscribe('filters', () => renderTracks())`
  (line 11605) re-enters the heavy album-mode render path
  (`docs/index.html:10857`) whenever the resulting `orderChanged` is
  true — which happens for any filter toggle (the joined sort key
  changes when the visible set changes).
- In album mode each rebuild walks all 3,775 tracks, rebuilds every
  album group, and re-creates an `<img src="/api/tracks/{id}/artwork">`
  artwork-probing chain per album (line 10906). The previous round's
  in-flight `<img>` requests are orphaned, not aborted. Several rapid
  filter toggles pile up hundreds of pending image requests + DOM
  rebuilds, and the main thread saturates long enough that the next
  CDP click event never gets its handler invoked within 30 s.

The test sequence performs **five** filter signals in a few hundred ms
(search-fill, search-clear, phrase-on, phrase-off, beats-on), and the
last one is the one that exceeds the 30 s budget. `beats-only-cb` is
not special — it is simply the straw that breaks the camel's back.

## Proposed solution

Smallest surgical fix: coalesce filter renders by debouncing the
`'filters'` subscriber. Rapid toggles within ~80 ms collapse into a
single `renderTracks()` call. This:

- Matches the existing `_scheduleSearchRecompute` cadence (80 ms,
  line 8854) — already shipped as the search-input pattern.
- Leaves the microtask-coalesce in `AppState.signal` intact (still
  benefits other consumers).
- Adds no new public API and no new state field.
- Is reversible via a single-line revert.

Concretely, replace the `subscribe('filters', …)` body with a
`setTimeout`-debounced wrapper that schedules `renderTracks()` at the
trailing edge.

## Affected files

- `docs/index.html` — `AppState.subscribe('filters', …)` block at
  line 11605.
- `tests/e2e/qa-smoke.spec.ts` — keep the existing test as the
  regression guard; it already fails without the fix.

## Risks

- Visible filter-change latency increases by ≤80 ms in the
  steady-state case. Acceptable: the search input already lives with
  the same delay.
- Programmatic callers that signal `'filters'` then synchronously
  introspect DOM state (none found via grep) would see a stale read.
  Verified: every signal-then-read pattern goes through `AppState`
  itself, not direct DOM inspection.

## Validation plan

- `cd tests/e2e && AUTOCUE_SOURCE_DB=$HOME/Library/Pioneer/rekordbox/master.db npm test`
  must show `filter toggles do not crash the page` passing on
  `qa-smoke.spec.ts`.
- `npm test --silent` (vitest) — touched `docs/index.html`, leg B
  re-runs.
- `pytest -x -q` — touched no Python files, leg A only runs on the
  first iteration.
