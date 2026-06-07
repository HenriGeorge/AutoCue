# Investigation — Issue #68

**Surface:** Discover tab — card snooze button (💤) tooltip
**Fingerprint:** `feature/discover-v2:snooze-tooltip:stale-text`

## Problem

The `title` attribute on the per-card 💤 (snooze) button reads `"Snooze 30 days"`,
but the backend no longer accepts a `30d` duration. Per PRD §4 and the e2e specs,
the only valid snooze durations are **1w / 1m / 3m** — `'30d'` returns HTTP 400.
The popover that opens on click correctly shows "1 week / 1 month / 3 months";
only the button's hover tooltip is stale.

## Root Cause

`docs/index.html:4888` — the per-card render template hard-codes
`title="Snooze 30 days"` on the snooze action button:

```html
<button class="disc-v2-card-action" data-act="snooze" title="Snooze 30 days">💤</button>
```

The string was never updated when the snooze durations were locked to 1w/1m/3m.
The popover render path (separate code) is correct.

## Proposed Solution

Replace the stale tooltip with one that reflects the actual durations the
backend accepts:

```html
<button class="disc-v2-card-action" data-act="snooze" title="Snooze (1w / 1m / 3m)">💤</button>
```

Update the in-test renderer fixture in `tests/web/discover-v2.test.js` so it
stays in sync (the fixture is a local copy of the production HTML, used by the
`renderCard` describe block). Add a Vitest assertion that fails if the stale
string ever returns.

## Affected Files

- `docs/index.html` (line 4888) — production render template.
- `tests/web/discover-v2.test.js` (line 79) — local fixture + new regression assertion.

## Risks

- Trivial UX-text change. No JS logic, no API surface, no DB. No e2e/Playwright
  selectors target this title attribute (verified via grep).
- The fixture in `tests/web/discover-v2.test.js` is purely local to that file;
  no other test imports it.

## Validation Plan

- Leg A (pytest): SKIP — no `autocue/**.py` or `tests/**.py` touched.
- Leg B (vitest): RUN — touched `docs/index.html` + `tests/web/**`.
- Leg C (Playwright e2e): RUN — touched `docs/index.html`.
