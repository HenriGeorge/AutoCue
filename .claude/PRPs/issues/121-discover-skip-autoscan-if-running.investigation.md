# Issue #121 — Discover auto-scan 409 leaks to console on reload

## Problem

When the Discover tab is activated (or the page is reloaded with Discover
active) and a previous Discover scan from another tab/session is still
streaming, the frontend's auto-scan calls `/api/discover/feed?...`. The
server correctly returns 409 ("A scan is already running for this
database"). The JS catches the 409 and preserves the prior feed (issue
#67), but the browser still emits a native `Failed to load resource: 409`
console error before user-space code can intervene.

Every Playwright test that asserts `console.errors === []` flags this —
~14 per-control-sweep rows fail purely because of the expected 409.

## Root cause

`docs/index.html:7598-7615` (init block at the bottom of
`initDiscoverV2`). The post-`loadInitialState()` block already calls
`/api/discover/feed/status`, but only uses the result to clear a stale
`scanError`. It then unconditionally fires `DiscoverV2.runScan()` when
`tokenValid && followedLabels.length > 0` — regardless of whether the
status response indicated a scan is in flight. The result is an
unavoidable 409 fetch in that case.

Relevant snippet (current, lines 7598-7615):

```js
DiscoverV2.loadInitialState().then(async () => {
  try {
    const status = await fetch('/api/discover/feed/status').then(r => r.json());
    if (status && status.running === false) {
      DiscoverV2.state.scanError = null;
    }
  } catch (_) { /* ignore, fall through */ }
  if (DiscoverV2.state.tokenValid && DiscoverV2.state.followedLabels.length > 0) {
    DiscoverV2.runScan();
  }
});
```

## Proposed solution

Issue's option 1 (cheapest, respects in-flight scan). Capture the
`status` response in an outer variable and gate the auto-scan on
`status.running !== true`. When a scan is already running, do nothing —
the SSE consumer of the existing scan already surfaces results when
ready, and `runScan()` reads progress via the user's existing in-flight
session if relevant.

Mechanics:
1. Hoist the `status` fetch result out of the `try` so the gating block
   can see it.
2. Add `if (status && status.running === true) return;` before the
   `runScan()` call.
3. Leave the `status === null` (fetch threw) path falling through to
   `runScan()` — same behavior as today, conservative.

Net diff: ~6 lines in `docs/index.html`.

## Affected files

- `docs/index.html` — `initDiscoverV2` init block at the bottom of the
  IIFE (~line 7598-7615).
- `tests/web/ux-pr-c.test.js` — extend the "stale scanError clear" suite
  with a regression test for the auto-scan-skip path.

## Risks

- Behavior change is observable: if the in-flight scan was started by
  the same browser session (rare on a fresh reload, but possible across
  tabs), the user no longer sees a fresh scan kick off automatically.
  They still see the in-flight progress through the existing SSE chain
  + can click Refresh manually. Acceptable per issue's UX trade-off.
- No backend change → no e2e leg disruption beyond the cleaned-up
  console-error noise.

## Test plan

1. Unit: vitest extension — verify the gating logic produces the
   correct outcomes for `{running: true}`, `{running: false}`, and the
   fetch-threw fall-through.
2. Regression guard: assert that with `{running: true}`, `runScan` is
   NOT called (the entire point of the fix).
3. Boundary: assert that with `{running: false}` and labels + token,
   `runScan` IS called (we did not break the happy path).
