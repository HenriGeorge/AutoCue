# AutoCue — UX/UI Audit v0.1 (initial findings, pre-grill)

**Reviewer**: Senior Staff UX/UI Designer (simulated)
**Method**: Live walkthrough of `http://127.0.0.1:7432` (autocue serve, main branch as of 894c154). 1440×900 desktop + 375×667 mobile breakpoints. 14 screenshots captured under `/var/folders/.../T/ux-*.png`.
**Scope**: All three tabs (Cues / Library / Discover), all modals and popovers (detail panel, snooze popover, kbd help, download confirm), settings sub-panel, mobile breakpoint, error states.

---

## Executive Summary

AutoCue is a power-user DJ tool with three distinct surfaces. The Discover v2 work (shipped this session) is the strongest of the three — modern card grid, focus-trapped dialogs, structured empty states. The Cues and Library tabs predate that work and show their age: unvirtualized 3775-track list, dense single-column form layout, redundant connection banner.

Across all surfaces the dominant friction is **chrome heaviness** (status, banners, mode toggles, sticky action bar competing with the actual workspace) and **context bleed** (Cues-specific bottom-bar actions visible on Discover; app-wide help overlay colliding with Discover-specific `?`). The Discover v2 surface itself has six concrete bugs visible in the audit screenshots — most introduced today, some pre-existing.

Final score (justified at bottom): **6.7 / 10 overall**, with Visual Design and Accessibility leading and Efficiency / Conversion lagging.

---

## Top 10 UX Issues

### Issue 1 — `?` triggers two overlapping keyboard-help overlays simultaneously
**Screenshot**: `ux-08-kbd-help.png` (also visible in `ux-09-snooze-pop.png`).
**Description**: Pressing `?` on the Discover tab opens both the app-wide keyboard help (the centered "Keyboard shortcuts" modal that documents tab switching with `1` / `2` / `3` and `Ctrl+A`) AND the Discover v2 `?` overlay (which documents j/k/Enter/s/x/z/D). They render in the same z-stack — the user sees TWO modals atop each other and can't tell which Esc closes first.
**Impact**: Violates Nielsen #4 (Consistency & Standards) and #8 (Aesthetic & Minimalist Design). On a first-time `?` press, the user can't form a model of "which shortcuts apply where". Recovery requires two Escapes.
**Severity**: **High**.
**Recommendation**: One of two paths: (a) the Discover-specific overlay absorbs `?` while the tab is active, suppressing the app-wide handler via `event.stopPropagation()`; or (b) merge them — the app-wide modal grows a context-aware "Discover" section that only shows when that tab is active.

### Issue 2 — Detail panel renders 40,000px tall (position-fixed not effective)
**Screenshot**: `ux-06-detail-panel.png` + `ux-07-detail-actions.png`.
**Description**: `getComputedStyle(panel).height` returns ~40,000px; the panel's `scrollTop = 400` is a no-op because the panel itself is laid out at full-document height instead of viewport-bound. CSS sets `position: fixed; top: 0; bottom: 0`, which should constrain it, but the rendered element is the full document tall. Tracklist + Discogs link + everything below the actions row is therefore unreachable without scrolling the OUTER document (which sends the user away from the panel).
**Impact**: Critical regression on the panel's core utility — the Discogs link and tracklist are designed deliverables but functionally invisible. Violates Nielsen #3 (User Control & Freedom): user clicks card to see album, but can't reach the actual album info.
**Severity**: **Critical**.
**Recommendation**: Audit the CSS chain — most likely an ancestor with `transform`, `perspective`, or `filter` is forming a new containing block and breaking `position: fixed`. Move the panel to a top-level sibling of `<body>` (out from under `#discover-tab-content`) and re-test. Add a Vitest assertion in `discover-v2-detail.test.js` that asserts `clientHeight <= window.innerHeight`.

### Issue 3 — YouTube preview carousel returns unrelated audiobook content
**Screenshot**: `ux-09-snooze-pop.png` (detail panel on the right shows "The Mystery of Angelina Frood by R. Austin Freeman" as a preview for "Forward In Reverse Pt.1").
**Description**: The query string sent to `/api/youtube/search` is built from `{artist} {title}`. For releases where the artist name contains a Discogs disambiguator like "(3)" or a "Various Artists" suffix, the search is poisoned and yt-dlp returns whatever fuzzy match it finds — sometimes audiobook narrations or unrelated genre content. Repeated across multiple cards.
**Impact**: Destroys the trust the preview carousel is supposed to build. Worse, the user might Save / Download the wrong content based on a preview that bears no relation to the album.
**Severity**: **High**.
**Recommendation**: Strip Discogs disambiguators ("(2)", "(3)", "*") from artist names before sending to YouTube. Also try a fallback with `album` only when the first query returns no high-confidence match. Long-term: surface the YouTube result title in the carousel so the user can spot the mismatch.

### Issue 4 — Stats block shows broken percentages and "(undefined)" counts
**Screenshot**: `ux-11-settings-panel.png`.
**Description**: Stats row reads `novelty mix: ok 1000% · partial 0% · sparse_adjacency 500%` (impossible percentages; the formatter is missing a `/total` denominator or treating raw counts as ratios) and `top artist sources: Sam Gendel (undefined) · soFa elsewhere (undefined)` (the `count` field is missing from the backend response for this list).
**Impact**: Trust collapses the moment a user opens Settings → Stats and sees `1000%`. Even if the actual gameplay is fine, the user concludes "this app is broken / unreliable". Violates Nielsen #1 (Visibility of System Status — the system reports nonsense).
**Severity**: **High**.
**Recommendation**: Backend (`/api/discover/stats`) — change `novelty_share` from a count-by-status dict to a ratios-by-status dict (divide by `total_scans`), and ensure `top_labels[].count` / `top_artists[].count` are populated. Frontend (`_renderDiscoverV2Stats`) — clamp any computed percentage to `[0, 100]` and skip rendering when count is null. Add Vitest assertions for both invariants.

### Issue 5 — Cues tab renders ALL 3775 tracks at once (no virtualization)
**Screenshot**: `ux-01-cues-default.png` (full-page screenshot was 59,768px tall).
**Description**: The track list at the heart of the Cues tab does not virtualize. Every card is mounted on first render; scroll jank, slow tab switching, and lengthy paint times follow. A casual measurement of full-page render showed the Cues default state painting a 59k-pixel-tall DOM tree.
**Impact**: For libraries above ~1500 tracks (Henri's is 3775; many DJs are 5–20k) the page becomes laggy on every tab switch and on track-list filter changes. Violates Hick's Law in the worst way — every track is a clickable element competing for attention, slowing every decision.
**Severity**: **High** (but well-known; this is exactly what the Performance v1 PRD targets).
**Recommendation**: Virtualize the track list using a windowing renderer (only DOM-mount the cards visible in the viewport + a 5-row buffer). Performance v1 PRD §3 already covers this — flag T-005 / T-006 in that PRD as the load-bearing tasks. Quick interim win: collapse already-cued tracks behind an "expand cued tracks" toggle so the default render is just the un-cued subset.

### Issue 6 — "Connected to Rekordbox · 3775 tracks" banner is permanent chrome
**Screenshot**: `ux-01a-cues-viewport.png`, `ux-02-library.png`, `ux-03-discover.png`.
**Description**: Every tab shows a green "Connected to Rekordbox · 3775 tracks" panel that occupies ~50px of the viewport on every page, every time. It's a confirmation, not a control — the same information is already in the top-right status corner (`● DB connected · 3,775 tracks · Last scan just now`).
**Impact**: Doubles the cognitive load of every screen visit (two locations report the same fact) and wastes ~5% of viewport vertical on every tab. Violates Nielsen #8 (Aesthetic and Minimalist) and Recognition-over-Recall (the green box recalls a setup state that's already shown elsewhere).
**Severity**: **Medium**.
**Recommendation**: Show the green banner only when the connection state CHANGES (just-connected / just-disconnected) and auto-dismiss after 3s. The persistent status corner stays as the resting state.

### Issue 7 — Sticky bottom action bar shows Cues-only actions on every tab
**Screenshot**: `ux-13-mobile-discover.png` + `ux-04-discover-populated.png`.
**Description**: The fixed bottom bar "Skip already colored · Color tracks by BPM · Preview cues · Delete all cues · Apply to Rekordbox" is permanently visible on the Discover tab AND the Library tab — yet none of those actions apply to those tabs. "Apply to Rekordbox" with `1 track · 8 cues` is especially confusing on Discover, where the user is not editing cues at all.
**Impact**: Violates context (Nielsen #3 User Control: actions appear available that aren't). On Discover the bar is dead weight that distracts from the card grid; on mobile it eats 60px of already-limited vertical.
**Severity**: **Medium**.
**Recommendation**: Make the bottom bar context-aware. On Discover, the bar should either hide entirely or show Discover-specific actions ("X selected · Save all · Dismiss all"). On Library, hide or show import/export actions.

### Issue 8 — Discover detail panel has no visual focus separation from grid
**Screenshot**: `ux-06-detail-panel.png`.
**Description**: When the detail panel opens, the backdrop is `rgba(0,0,0,0.4)` — but the panel sits at z-index 1000 and the backdrop at z-index 999, with the panel right-aligned at 480px wide. Result: the LEFT half of the viewport still shows the bright card grid at full saturation. Visually the panel reads as a "side card" rather than a modal — the user can keep clicking grid cards behind it, which doesn't replace the panel cleanly.
**Impact**: Violates Fitts's Law (cards remain valid targets that compete with panel) and weakens the dialog's modality. Click-outside-to-close (already implemented) becomes confusing because the "outside" looks active.
**Severity**: **Medium**.
**Recommendation**: Increase backdrop opacity to `rgba(0,0,0,0.55)` AND extend it to cover the full viewport (it does — but the visual contrast is too low at 0.4). Optionally add a subtle blur (`backdrop-filter: blur(2px)`) for a stronger "behind-the-glass" feel.

### Issue 9 — Discover tab "Couldn't finish the scan" stale-error state on reload
**Screenshot**: `ux-03-discover.png`.
**Description**: On hard-refresh, the Discover tab briefly shows "**Couldn't finish the scan.** A Discover scan is already running. Wait for the other scan to finish or cancel it." — but the server reports no scan running (`/api/discover/feed/status` says `running: false`). The cached error message survives reload because it's part of the empty-state rendering even though `scanError` has cleared on the fresh page load.
**Impact**: The first 1–3 seconds after the user opens the Discover tab show a scary red error. Violates Nielsen #9 (Help users recover from errors): there's no error to recover from. Trust hit on first impression.
**Severity**: **Medium**.
**Recommendation**: In `_renderDiscoverV2Feed`, gate the error rendering on `state.scanError !== null AND state.scanRunning === false AND state.scanLastSummary === null`. Currently the gate may use stale data populated from prior session if anything is persisted. Also add a server-side `/feed/status` poll on tab activation that overrides any client-side stale state.

### Issue 10 — Onboarding "Add all to watch list" silently drops 2 of 10 labels
**Screenshot**: (captured in this session's chat — `followedLabels: 8` after "Add all" was clicked with 10 suggestions visible).
**Description**: When the user clicks **Add all to watch list**, the chips fan-click and each one resolves a Discogs label_id via `/labels/search`. Two of ten suggestions ("Glossy Mistakes" and "Numero Group - NUM079cd") silently fail to resolve — the chips disable to "✓ Following" anyway in some implementations, OR they're just skipped with no visible feedback. The user thinks they followed 10 labels; only 8 actually saved.
**Impact**: Silent failure violates Nielsen #1 (Visibility of System Status). The user expects all 10 labels to appear in Settings → Followed; finds only 8; can't tell which two went missing or why.
**Severity**: **Medium**.
**Recommendation**: When `_followByName` returns false, the corresponding chip should switch to "⚠ Couldn't find on Discogs" with a tooltip explaining "library label name contains catalog code or disambiguator". A summary toast at the end of "Add all": "Followed 8 of 10 labels — 2 couldn't be matched on Discogs (click ⚠ chips for details)."

---

## Accessibility Review (WCAG 2.2)

| Criterion | Status | Note |
|---|---|---|
| 1.3.1 Info and Relationships | ✓ | Dialogs use `role="dialog" aria-modal="true" aria-labelledby` consistently |
| 1.4.3 Contrast (Minimum) | ⚠ | The "last scanned 18m ago" muted text on `var(--muted)` reads ~3:1 against `var(--surface)` — sub-AA. Likewise the "VIA ARTIST · 2024" caption line on card. |
| 2.1.1 Keyboard | ✓ | j/k/Enter/s/x/z/D/? all wired (T-028); detail panel focus-trapped |
| 2.4.3 Focus Order | ⚠ | When the detail panel opens, the focus trap cycles inside it — but if the panel content overflows (Issue 2), Tab can jump to elements that aren't visible. |
| 2.4.7 Focus Visible | ⚠ | The default browser focus outline is present on buttons but `box-shadow: none` on `.disc-v2-card-action` removes any active-state ring. Hard to see keyboard focus on card actions. |
| 3.2.1 On Focus | ✓ | No surprise navigation on focus events |
| 3.3.1 Error Identification | ⚠ | Issue 9 — stale error message; Issue 4 — "1000%" is gibberish, not an error |
| 4.1.2 Name, Role, Value | ✓ | All interactive elements have `aria-label` where icon-only |
| 4.1.3 Status Messages | ✓ | Live regions via `role="alert"` on `disc-v2-detail-error` |

---

## Mobile Experience Review

- Discover grid collapses to 1 column at <900px (T-024 CSS handles this — good).
- Detail panel goes full-screen at <900px (also T-024 — good).
- The bottom action bar (Cues actions) is **dead weight** on Discover at mobile width — Issue 7 — costs ~60px of vertical that the card grid desperately needs.
- The "Connected to Rekordbox" banner + Playlist dropdown + Analysis-mode toggle take ~155px of vertical before any tab-specific content. Issue 6 amplified on mobile.
- The top-right status corner ("● DB connected · 3,775 tracks · ● Rekordbox ?") collides with the tab nav on mobile (visible in `ux-14-mobile-top.png`) — it stacks below the AutoCue logo and the tabs sit awkwardly between them.

---

## Conversion Friction Review

The "conversion" in AutoCue is the user actually completing the **place-cues → apply-to-Rekordbox** loop, AND in Discover, **find-album → save-or-download**. Friction sites:

1. **Apply-to-Rekordbox** sticky bar is always visible but its enabling condition ("Ready to import: N tracks · M cues") is only clear on the Cues tab. On other tabs it shows the same counter, creating false urgency.
2. **Discover download** has the correct Cancel-default safety modal (T-027) — but the modal's body line "Query: sofa_elsewhere sandy_b_(3)_&..." (the underscored release_key as the query preview) is jargon, not a human-readable confirmation. Should read "We'll search YouTube for: `Sandy B & soFa elsewhere — Forward In Reverse Pt.1`".
3. **Save → see it later** — there's no visible Saved tab. Once a user clicks 💚 Save, they have no place to find that release again except by re-scanning. Discovery without retention.

---

## Design System Consistency Review

- Button styles split into three buckets: `primary` (black), `secondary-btn` (white), and a generic `tag-pill`. Mostly consistent.
- Border-radius varies: cards = 8px, chips = pill (50%), buttons = 6px. Acceptable.
- Color tokens (`--green`, `--muted`, `--surface`) used consistently — except the bottom action bar uses raw hex codes in places.
- Emoji as action affordance is heavy on Discover (💚 Save, 💤 Snooze, ✕ Dismiss, 🚫 Block, ⬇ Download, ⟳ Refresh, ⚙ Settings, ✓ Following) but text-only on Cues / Library. This split is per-feature and fine.
- "Add all to watch list" / "Skip for now" use sentence case; nearby "Refresh" / "Settings" / "Search" use Title Case. Inconsistent.

---

## Quick Wins (< 1 day effort)

1. Fix Issue 4 — stats formatter math (`novelty mix 1000%` and `(undefined)` counts). 2 lines + 2 tests.
2. Fix Issue 9 — gate stale-error rendering on `scanLastSummary === null`. 4 lines + 1 test.
3. Fix Issue 1 — `event.stopPropagation()` on the Discover `?` handler. 1 line + 1 test.
4. Fix Issue 3 partial — strip "(N)" / "*" suffixes from artist names before YouTube search. 3 lines + 2 tests.
5. Fix Issue 6 — auto-dismiss the green Rekordbox banner after 3s. 5 lines + 1 test.

## Medium Improvements (1–2 weeks)

6. Fix Issue 2 — relocate detail panel to top-level sibling of `<body>` and verify viewport-bound height. ~30 lines + a Vitest viewport-clamp assertion.
7. Fix Issue 7 — context-aware sticky bar (hide on Discover, show Discover-actions when N saved/dismissed pending).
8. Fix Issue 10 — surface failed `_followByName` attempts in onboarding with per-chip ⚠ feedback + summary toast.
9. Address Issue 8 — backdrop opacity bump + blur.
10. Address sub-AA contrast in muted captions (WCAG 1.4.3).

## Strategic Improvements (1+ month)

11. Issue 5 — virtualize the Cues track list (already scoped in Performance v1 PRD §3).
12. Add a "Saved" tab to Discover (per PRD §5.6 power-user shortcuts: "Quick download" on every saved-list row — implies a list exists).
13. Refactor the bottom-bar action area into a per-tab plugin slot rather than a global widget.

---

## Final UX Score

| Dimension | Score | Justification |
|---|---|---|
| Learnability | 6 / 10 | Discover tab onboards via banner + Suggest button; Cues tab assumes user knows what "Bar intervals" vs "Phrase analysis" mean with no inline help. |
| Efficiency | 5 / 10 | Keyboard shortcuts present (j/k/s/x/z/D/?), but Issue 1 collides them with app-wide help. The 3775-track list is unvirtualized. Modal overlays sometimes stack. |
| Accessibility | 7 / 10 | Proper dialog semantics, focus traps, aria-labels. Loses points for sub-AA contrast on muted captions and focus visibility on hover-revealed action buttons. |
| Visual Design | 8 / 10 | Modern card grid, consistent token palette, well-balanced spacing on Discover. Cues / Library show their pre-v2 age but remain clean. |
| User Satisfaction | 6 / 10 | First-time scan is fast and produces real results. But Issue 4 ("1000%"), Issue 3 (audiobook previews), and Issue 9 (stale error on reload) chip away at trust. |
| **Overall Score** | **6.7 / 10** | Solid foundation, several high-impact bugs masking the genuinely good architecture beneath. |
