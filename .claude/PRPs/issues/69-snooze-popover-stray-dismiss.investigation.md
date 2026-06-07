# Issue #69 — Discover snooze popover close dismisses adjacent card

**Issue**: `[autocue-qa] feature/discover-v2:snooze-popover-close:stray-dismiss`
**Surface**: Discover tab
**Severity**: medium / impact: small / labels: bug, ux

## Problem

After clicking a snooze duration (`1w` / `1m` / `3m`) in the snooze popover, an
adjacent card is dismissed seconds later with no further explicit user action.
`dismissed_count` increased by 5 across a session that had only one explicit
dismiss click.

## Root cause

Three issues compound to produce the stray dismiss:

1. **`docs/index.html:6022-6031` — `_runSnoozeWithDuration` closes the popover BEFORE awaiting the snooze POST.** `_closeSnoozePopover` (line 6007) restores focus to `_snoozePopReturnFocusEl` (the original 💤 button on the card). Then `await DiscoverV2.snooze(...)` resolves and triggers a feed re-render via `notify()` → `_renderDiscoverV2Feed` (line 4943) which does `grid.innerHTML = ''`. The 💤 button we just focused is destroyed; the browser moves focus to `document.body` (or the next focusable ancestor).

2. **`docs/index.html:6196-6208` — feed-render subscriber re-applies `.active` to whatever card now sits at the *preserved* `_activeCardIndex`.** Since Card A was removed, the card that was at `index+1` is now at `index`, and inherits the `.active` class. `_activeRelease()` (line 5924) now returns this adjacent neighbor.

3. **`docs/index.html:6186-6188` — popover button `click` handlers call `ev.preventDefault()` but NOT `ev.stopPropagation()`.** Defensive hardening.

When the user taps any key (Space, Enter) after the snooze, `_handleDiscoverKeydown` fires `DiscoverV2.dismiss(_activeRelease())` against the adjacent card. The QA report's "3 seconds later" gap is the human reaction time between completing the snooze click and pressing the next key.

## Proposed solution

A tight, defensive, ≤ 50-line fix in `docs/index.html`:

1. **Track active card by `release_key`, not by index.** Add a module-scoped
   `_activeReleaseKey` that the keyboard navigation updates alongside the
   numeric index. The feed-render subscriber re-derives the active index from
   that key on every re-render — if the key no longer exists in the rendered
   list, drop the active state entirely instead of silently shifting to a
   neighbor. This is the core fix; the other two are belt-and-braces.

2. **Run snooze BEFORE closing popover** in `_runSnoozeWithDuration`. The
   re-render then happens while the popover is still focused, and focus
   restoration on close moves to a still-existing element (or harmlessly to
   body if the original is gone).

3. **`ev.stopPropagation()`** on popover button clicks. Prevents any future
   bubble from being misinterpreted.

## Affected files

- `docs/index.html` — `_runSnoozeWithDuration`, `_setActiveCard`,
  `_activeRelease`, the feed-render active-class subscriber, popover button
  wiring.
- `tests/web/discover-v2-snooze.test.js` — add regression test that asserts
  the post-snooze re-render does NOT shift `.active` to an adjacent card.

## Risks

- The active-card-by-key change touches `j`/`k`/`Enter`/`s`/`x`/`z`/`D`
  keyboard shortcuts indirectly. We keep the numeric `_activeCardIndex` as
  the visual cursor and add a separate sticky key — minimizes blast radius.
- No backend changes; no impact on `master.db`; no CORS/auth surface.
