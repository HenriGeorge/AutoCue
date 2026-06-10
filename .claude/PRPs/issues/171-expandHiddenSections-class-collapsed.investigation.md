# Issue #171 — expandHiddenSections does not strip class-based collapse

## Problem

`tests/e2e/per-control-sweep.spec.ts` `expandHiddenSections()` only clears
inline `style.display === 'none'` on `*-section`, `*-body`, `*-panel`
elements. It does NOT:

1. Strip the `collapsed` class from `#settings-section` (which gets
   `class="visible collapsed"` after the on-load auto-collapse). This
   causes 30s click-intercepted timeouts on every checkbox/button inside
   the Cues panel's `#settings-section`:
   `add-fill-cues`, `skip-existing-cues`, `skip-colored-cb`, etc.
2. Reach `cue-tools-params-auto-classify` (a `display:none` `<div>` with
   class `cue-tools-params` — none of the suffix selectors match). This
   keeps `at-category`, `at-vocal`, `at-energy-level`, `at-energy-profile`,
   `at-intro-outro`, `at-decade`, `at-bpm-tier`, `at-play-history`
   unreachable.

Net effect: ~25 `per-control sweep` rows time out at 30 000 ms each,
turning the harness wall-clock into 12+ min of dead air.

## Root Cause

`tests/e2e/per-control-sweep.spec.ts:67-75`:

```ts
async function expandHiddenSections(page: Page) {
  await page.evaluate(() => {
    for (const sel of ["[id$='-section']", "[id$='-body']", "[id$='-panel']"]) {
      for (const el of Array.from(
        document.querySelectorAll<HTMLElement>(sel),
      )) {
        if (el.style.display === "none") el.style.display = "";
      }
    }
  });
}
```

Two gaps:
- Only inspects three id-suffix selectors. The `cue-tools-params-*` divs
  match none of them.
- Only inspects `style.display`. Class-based collapse (`.collapsed` on
  `#settings-section`, which uses `#settings-section.collapsed .settings-body { display:none }`
  in CSS) is invisible to the inline check.

`docs/index.html:274` `#settings-section.collapsed .settings-body { display:none }`
plus the auto-collapse at `docs/index.html:4164-4165` (`_collapseSettings` invoked
on local mode startup) is what wedges the Cues panel rows.

## Proposed Solution

Extend `expandHiddenSections` so it both:

1. Strips a small allowlist of collapse classes (`collapsed`,
   `is-collapsed`, `hidden`) from every `*-section`, `*-body`, `*-panel`,
   plain `section`, and `[class*='-params']` element.
2. Clears inline `display:none` across the same widened selector set
   (matching the drift guard at `control-inventory.spec.ts:25-47`, which
   also force-expands `section`).
3. Preserves existing behavior — never throws when a selector matches
   nothing.

Implementation: rewrite the helper as a single `page.evaluate` that walks
a widened selector list once and applies both fixes. Idempotent and safe
to call repeatedly.

We do NOT click section title-toggles because that fires the page's
onclick handlers (which may persist state to localStorage or fire
network requests in some panels). Inline-style/class mutation in-place
is the same pattern the drift guard uses and is by-design side-effect
free.

## Affected Files

- `tests/e2e/per-control-sweep.spec.ts` (the helper itself)

## Risks

- **False expand of unrelated `[class*='-params']` divs** — these are
  benign UI subpanels; widening to include them only exposes more
  controls, never hides any. Risk: zero.
- **`hidden` class collision** — used in `docs/index.html:9555`
  (`.load-audio-btn.hidden`). Stripping the `hidden` class from a
  button would reveal it, but `.load-audio-btn` is on track cards, not
  inside `*-section`/`*-body`/`*-panel`/`section`/`*-params`. Risk: low.
- **Test flakiness from race** — `expandHiddenSections` runs after
  `gotoPanel` already waits for the per-panel readiness signal. No
  additional race introduced.

## Validation

- Leg C (e2e) is the relevant one — re-run the sweep and confirm the
  previously-timing-out rows now pass under 30s. Touch log: only
  `tests/e2e/per-control-sweep.spec.ts` changes, so only Leg C runs.
- Add a unit-level Playwright assertion (regression guard): after
  navigating to the Cues panel and calling `expandHiddenSections`, the
  `#settings-section` must not have the `collapsed` class.
