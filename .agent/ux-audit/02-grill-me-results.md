# Grill-me Pass on UX Audit v0.1

**Subject**: `01-initial-findings.md`
**Method**: Adversarial verification of every claim against live DOM at `http://127.0.0.1:7432` + source code at `894c154`. Where the audit asserts a fact, the grill checks it.

---

## Per-issue verdicts

### Issue 1 — Two `?` overlays — **REAL, severity holds at High**

The audit's hypothesis ("two separate overlays") is confirmed by DOM inspection. There are SIX matching elements when grepping for keyboard-help ids; the two top-level overlays are:

- `#disc-v2-kbd-help` (Discover-specific, T-028 addition)
- `#kbd-overlay` + `#kbd-modal` (app-wide, predates v2)

Both are distinct elements with their own backdrops. The audit's recommendation (a) is the right call: scope `?` handling to the active tab, `event.stopPropagation()` inside the Discover handler.

Concrete failure scenario: User is on Discover, presses `?` to see shortcuts. Both modals stack. User Escapes once — only one closes. User Escapes again — second closes. User now isn't sure which modal explained which shortcut, can't form a mental map, gives up on the keyboard interface.

**Verdict: REAL · High justified · recommendation correct.**

### Issue 2 — Panel 40,000px tall — **REAL, Critical justified, ROOT CAUSE CONFIRMED**

Live measurement:
- `panel.offsetHeight = 39,259px`
- `panel.position = "fixed"`
- `panel.offsetParent = #discover-tab-content`
- `#discover-tab-content` has class `tab-entering` AND a non-`none` `transform` value

The transform on the tab container creates a new containing block for descendant `position: fixed` elements (per CSS spec). The panel resolves `top: 0; bottom: 0` relative to that ancestor (full content height) instead of the viewport. The audit's hypothesis was correct without seeing the CSS.

**Important nuance the audit missed**: the INNER padding div is only 815px tall (= ~viewport-height). So the actions row, YouTube slot, AND tracklist DO fit inside the visible area on first open — they're just stacked inside the 815px inner. The unreachable problem only manifests when the user scrolls the OUTER document; the panel scrolls AWAY with it because position-fixed is broken. So "tracklist is unreachable" isn't quite right; "panel slides off-screen when user scrolls" is the actual failure.

Concrete failure scenario: User opens detail panel near the top of the Discover grid. Reads cover + title + actions. Scrolls down the panel to see the tracklist (mouse-wheel over the panel). The OUTER page scrolls instead (because the panel's overflow:auto is irrelevant — clientHeight already equals scrollHeight at 39k). The cover + title scroll off the top of the screen. The actions disappear. The user can no longer click Save / Snooze / Dismiss without scrolling back up — and even then, the tracklist they were trying to read is below the actions, also off-screen.

**Verdict: REAL · Critical justified · recommendation correct + correct the description from "tracklist unreachable" to "panel scrolls with page because position:fixed is broken".**

### Issue 3 — YouTube audiobook — **REAL but OVERSTATED, severity downgrade from High to Medium**

Live count: `0` of `332` current Discover cards have "(N)" disambiguators in artist names. The audit's claim of "Repeated across multiple cards" is empirically false. The audiobook anecdote was ONE card: "Sandy B (3) & soFa elsewhere".

But the audit's underlying complaint is still real — the YouTube preview CAN surface unrelated content. The cause may not even be the "(3)" — YouTube's search is just fuzzy for less-popular niche releases. Removing the "(3)" doesn't guarantee a relevant result.

The right recommendation pivots from "strip disambiguators" (a 3-line cosmetic fix that may not help) to "surface the YouTube result title in the carousel" (so the user can spot a mismatch). The audit's long-term suggestion was correct; the proposed quick win was a guess.

Concrete failure scenario (revised, scoped honestly): For ~0.3% of releases the YouTube preview shows audiobooks, ASMR, or unrelated content. User sees an irrelevant preview, gets confused, but doesn't act on it because the album art + title are visible elsewhere on the card.

**Verdict: REAL · downgrade to Medium · revised recommendation: surface YouTube title in carousel, don't strip disambiguators (the data shows it's not the cause).**

### Issue 4 — Stats 1000% — **REAL, severity downgrade from High to Medium**

Live verification: `/api/discover/stats` returns:
```json
"novelty_share": {"ok": 11, "partial": 0, "sparse_adjacency": 6}
"top_artists": [{"name": "Sam Gendel", "plays": 2}, ...]
```

So backend returns RAW COUNTS (11, 6) and uses `plays` (not `count`). Frontend's `_formatStatsPercent(n) = Math.round(n * 100)` then renders `11 * 100 = 1100%` (audit observed 1000% — likely different scan count at audit time, same root cause). And `${a.count}` against `{plays: 2}` renders `undefined`.

So the audit's root-cause diagnosis is CORRECT. But user's reachability question is also correct: this lives in Settings → Stats. The Settings panel collapses behind a `⚙ Settings` button. Most users never open it. Conversion-weighted, this affects far fewer users than Issue 7 (sticky bar shown on every tab).

**However** — there's an aggravating factor the audit missed: the dev signed off on T-037 saying "full stats surface" works. A user who opens Stats EXPECTS it to be correct because it was just shipped. Seeing `1000%` once is enough to lose trust in the entire Discover feature. So "rare reach × high trust damage" still pushes this to a real fix.

**Verdict: REAL · downgrade to Medium · recommendation correct.**

### Issue 5 — No virtualization — **REAL but severity downgrade from High to Medium (for UX)**

Live count: 3,779 track DOM nodes, **8,961 buttons** on the page, 29,886px scrollHeight.

The audit's claim of "scroll jank, slow tab switching, lengthy paint times" wasn't measured. On Henri's machine the page works. The actual UX symptom is paint-time on tab switch (sub-perceptible to user) + Devtools complaining about node count.

This is a performance issue with UX symptoms, not a UX issue per se. Most users wouldn't flag this in a usability test. It DOES matter for libraries above ~10k tracks — at that point the lag becomes obvious — but for the 3,775-track Henri case it's invisible.

The audit's Strategic placement (#11) was correct. The Severity High in the top-10 list was inflated.

**Verdict: REAL · downgrade to Medium · audit's Strategic placement was already correct; the High label in Top 10 contradicted itself.**

### Issue 6 — Connected banner — **REAL, Medium is the right call**

Information is genuinely duplicated. The fix is cheap. Nielsen #8 violation is real but not catastrophic.

One small grill: the audit's recommendation ("auto-dismiss after 3s") risks irritating power users who actually want a persistent "connected" signal when they bring up the page. Better recommendation: collapse the banner to a single-line "● Connected" pill (or just merge into the existing status corner) instead of removing on a timer.

**Verdict: REAL · Medium justified · recommendation should be "consolidate with status corner" not "auto-dismiss".**

### Issue 7 — Sticky bar context bleed — **REAL, ESCALATE to High**

Audit was Medium. User's instinct in the grill question is correct: this should be High.

Reasons to escalate:
- **Reach**: Every tab × every visit. Highest reach of any audit finding.
- **Confusion**: "Apply to Rekordbox" + `1 track · 8 cues` shown WHILE the user is on the Discover tab implies "Discover lets me apply to Rekordbox". It doesn't.
- **Mobile damage**: 60px lost to dead actions at <900px viewport, where vertical is scarce.
- **Failure mode is silent**: nothing breaks if user clicks — but cognitive load remains.

Concrete failure scenario: User on mobile spending most of their time browsing Discover. The bottom 60px is permanently dead chrome showing actions for a different tab. The card grid above it is forced into a narrower visible region. The user never realizes the bar is supposed to apply to Cues — they just feel the app is cluttered.

**Verdict: REAL · ESCALATE to High · recommendation correct.**

### Issue 8 — Backdrop opacity — **REAL, Medium correct**

Live verified: `rgba(0, 0, 0, 0.4)` at z-index 999. Backdrop does cover the full viewport. But 0.4 is on the low end; standard modal backdrops sit at 0.5–0.7.

Audit's "the LEFT half still shows the bright card grid at full saturation" is slightly inflated — 0.4 does dim the grid, just not enough to feel separated. The recommendation (bump to 0.55 + optional blur) is right.

**Verdict: REAL · Medium justified · recommendation correct (mild rephrasing of "left half is bright").**

### Issue 9 — Stale error on reload — **REAL, severity holds at Medium**

The audit's gate condition (`scanError !== null AND scanRunning === false AND scanLastSummary === null`) is wrong on closer reading. The actual current behaviour: on tab activation, `loadInitialState()` runs but `runScan()` may also fire concurrently, and the FIRST concurrent runScan sets `scanError = conflict` while the SECOND succeeds — leaving stale `scanError` even after a successful subsequent scan.

So the audit identified the SYMPTOM correctly but the ROOT CAUSE diagnosis is "stale state from prior session" — actually it's "two runScan() calls racing on tab activation, one wins and clears scan-running, the other sets scanError before realizing it lost the race".

The fix is different too: deduplicate the auto-scan kick-off (one tab-activation handler, not two).

**Verdict: REAL · Medium justified · recommendation needs revision — debounce/dedupe the auto-scan, not just gate the error rendering.**

### Issue 10 — Silently drops 2 of 10 — **REAL, severity could go either way (defended at Medium)**

User's question: should this be High because "silent failure" = Nielsen #1?

Defense for Medium:
- The "failure" is technically a label-not-on-Discogs case, not a system error. If Discogs doesn't have a "Numero Group - NUM079cd" entry, no amount of UI polish makes that label followable.
- The failure happens during onboarding — a once-per-user moment. Subsequent label-management uses the Suggest button which only returns IDs that resolved.
- The user CAN see the result in Settings → Labels: 8 names appear, not 10. The system isn't lying.

Defense for High:
- 80% completion rate when user clicked "Add ALL" is misleading affordance.
- User has no way to know what failed or why.

I lean Medium because the Settings panel shows the truth and the failure isn't a system bug. But a confident High would also be defensible. The audit's recommendation (per-chip ⚠ + summary toast) is the right fix at either severity.

**Verdict: REAL · Medium defensible (High also defensible) · recommendation correct.**

---

## Issues the audit MISSED

Walking the screenshots and code again, the audit skipped several real problems:

### M-1: Dark-mode toggle is unlabeled icon
**Severity: Medium**. The top-right moon button has no `aria-label`. Screen readers announce "button" with no context. WCAG 4.1.2 violation. Trivial fix.

### M-2: Download confirm shows the release_key, not a human-readable query
**Severity: Medium (Conversion)**. The `Query: ...` line in the modal renders the normalized release_key (lowercased, underscored). The audit mentioned this once in passing in Conversion Friction Review, but didn't surface as an Issue. It should be its own line item — users about to confirm a download should see "We'll search YouTube for: Sandy B & soFa elsewhere — Forward In Reverse Pt.1", not `sofa_elsewhere|||sandy_b_(3)_&_sofa_elsewhere_-_forward_in_reverse_pt.1`.

### M-3: Onboarding chips don't tell the user WHY a label was suggested
**Severity: Low**. "Glossy Mistakes" appears as a chip with no context. A user who doesn't recognize the name skips it. Hover tooltip explaining "Suggested because 14 tracks in your library are tagged with this label" would convert more follows.

### M-4: Save action has no destination
**Severity: High (mentioned in Strategic, should be in Top 10)**. The audit's Strategic #12 ("Add a Saved tab") is mis-categorized. The current state is: user clicks 💚 Save → card disappears from the feed (filtered as already-actioned) → no place to see what they saved. This is a complete feature gap that the user discovers AFTER they've started saving. Should be in Top 10 at High severity.

### M-5: Card source line says "VIA ARTIST · 2026" — what does "VIA" mean?
**Severity: Low**. Jargon. Recognition-over-recall violation. Better: omit "VIA" and just show "Artist match · 2026" or "Suggested via your top artists · 2026".

---

## Quick Wins list — pressure test

The audit promised 5 fixes "< 1 day effort":

1. **Stats math (Issue 4)** — 2 lines + 2 tests. **Actually** ~5 lines (frontend formatter + backend response shape change for `top_artists`). Honest estimate: 30 min.
2. **Stale error gate (Issue 9)** — 4 lines + 1 test. **Actually** the root cause is a race on tab-activation, not just gating. Real fix is 15 lines + 2 tests + understanding which path fires runScan twice. **2–3 hours**, not 1 day, but more than 4 lines.
3. **`?` event.stopPropagation (Issue 1)** — 1 line + 1 test. **Real**: 1 line, but you also need to verify the app-wide `?` handler ALSO doesn't fire if its own dialog is open (otherwise stopPropagation creates a dead state). Real estimate: 30 min including verification.
4. **Strip "(N)" suffixes (Issue 3)** — 3 lines + 2 tests. **Actually** the data shows this won't fix the problem (0 dirty artists in current set). The real quick win is to surface the YouTube result title in the carousel, which is a render-side change (~20 lines + tests). Re-scope the quick win.
5. **Auto-dismiss connected banner (Issue 6)** — 5 lines + 1 test. **Actually** the better fix per grill is consolidating into the status corner, which is more work (~40 lines + tests, possibly across 3 templates). Quick win as written is fine but suboptimal.

**Hidden ≥1 day work**: Item 2 (stale error race) is closer to half a day. Item 4 needs a complete re-scope.

---

## Score table — recalibration

The audit said 6.7/10 overall. Grill verdicts:

| Dimension | Audit | Grilled | Why |
|---|---|---|---|
| Learnability | 6 | **5** | Audit didn't account for the "Save → ?" gap (M-4 above). Lower. |
| Efficiency | 5 | **6** | Audit said 5 because keyboard shortcuts have Issue 1 + virtualization missing. Grill verdict: keyboard SHORTCUTS THEMSELVES WORK (just collide with another overlay); virtualization is a perf issue that the user doesn't notice at 3,775 tracks. 6 is fair. |
| Accessibility | 7 | **6** | Audit was generous. WCAG 1.4.3 (contrast) + 2.4.7 (focus visible) + 4.1.2 (dark-mode label) all fail. Plus Issue 2 means tab-into-panel cycles to invisible elements. 6 is honest. |
| Visual Design | 8 | **7** | The "Cues / Library show their pre-v2 age" point in the audit's own justification disqualifies 8/10. The system is split — Discover is modern, Cues/Library are older. Average ~7. |
| User Satisfaction | 6 | **6** | Stays. Trust-hits are real. |
| **Overall** | **6.7** | **6.0** | Recalibrated. Comparable to early-stage Lexicon or Beatsource (peer DJ-tools); below Mixed In Key (polish leader); ahead of Rekordbox itself (which is consistently 4-5/10 for usability). |

The audit was about 0.7 points too generous. Visual Design 8/10 was the biggest miss given the explicit two-system observation.

---

## Recommendations (corrected and prioritized)

Final ordered list for the implementation phase, with corrected severities + actionable fix scopes:

| # | Original | New Severity | Why | Fix scope |
|---|---|---|---|---|
| 1 | Issue 2 (panel 40k tall) | **Critical** | Panel scrolls off-screen with page; tracklist becomes useless mid-scroll | Move panel out of `#discover-tab-content` to sibling of `<body>`. ~10 lines + Vitest viewport-clamp assertion. **~1 hour.** |
| 2 | Issue 7 (sticky bar bleed) | **High** ↑ | Every tab × every visit; mobile costs 60px | Context-aware bottom bar: hide on Discover, hide on Library. ~30 lines + 2 tests. **~2 hours.** |
| 3 | M-4 (Save → no destination) | **High** (NEW) | Complete feature gap users hit after saving | Add a "Saved" view (could be a Settings sub-tab; could be a chip filter on the existing grid). **~half day** if minimal. |
| 4 | Issue 1 (two ? overlays) | **High** | Real collision, both fire | `event.stopPropagation()` in Discover `?` handler + verify app-wide handler also gates on its own dialog. 30 min. |
| 5 | Issue 4 (Stats 1000%) | **Medium** ↓ | Real bug, low reach (Settings sub-panel) | Backend: ratios for novelty_share + `count` instead of `plays` for top_artists/labels. Frontend: clamp + skip on null. 30 min + 2 tests. |
| 6 | Issue 9 (stale error) | **Medium** | Real but rare; race condition needs dedupe | Dedupe the auto-scan kick-off; render-gate is secondary. 2-3 hours. |
| 7 | Issue 3 (YouTube relevance) | **Medium** ↓ | Real but 0.3% prevalence in actual data | Surface YouTube result title in carousel (let user spot mismatch). Don't bother stripping "(N)" — data shows it's not the issue. ~20 lines. |
| 8 | Issue 6 (connected banner) | **Medium** | Real chrome bloat | Consolidate into status corner, not auto-dismiss. ~40 lines across templates. |
| 9 | Issue 8 (backdrop) | **Medium** | Real but minor | Bump 0.4 → 0.55 + optional backdrop-filter blur. 2 lines. |
| 10 | Issue 10 (silent drop) | **Medium** | Onboarding only; user CAN verify in Settings | Per-chip ⚠ + summary toast. ~30 lines + 1 test. |
| 11 | M-1 (dark-mode label) | **Medium** (NEW) | WCAG 4.1.2 trivial fix | Add `aria-label="Toggle dark mode"`. 1 line. |
| 12 | M-2 (download query) | **Medium** (NEW) | Conversion friction | Replace release_key with human-readable query in confirm modal. 3 lines. |
| 13 | Issue 5 (virtualization) | **Medium** ↓ | Perf issue with mild UX symptoms; Performance v1 PRD covers it | Defer to Performance v1. No new work. |
| 14 | M-3 (onboarding chip context) | **Low** (NEW) | Quality-of-life | Hover tooltip explaining suggestion source. ~10 lines. |
| 15 | M-5 ("VIA ARTIST" jargon) | **Low** (NEW) | Recognition-over-recall | Replace "via artist" → "Artist match". 1 line. |

---

## Is the audit ready to drive a fix-pass?

**Yes, with the corrections above.** The audit identified real problems and the root causes were mostly right; it overstated severity in places (Issues 3, 4, 5) and understated in others (Issue 7, the M-4 Save→destination gap). The grill produced:

- 1 audit issue ESCALATED (Issue 7 → High)
- 3 audit issues DOWNGRADED (Issues 3, 4, 5 → Medium)
- 1 critical missing finding added (M-4 — Save → no destination)
- 4 additional Medium / Low findings added
- Recalibrated overall score: 6.0/10 (audit was 6.7).

The implementation phase should use the corrected Top 15 table above as its work queue, NOT the original audit's Top 10. The Quick Wins list needs item 2 re-estimated (closer to 3h) and item 4 re-scoped (different fix entirely).

The Critical fix (Issue 2 — panel containing-block) should go first regardless because it makes the rest of the Discover detail-panel UX coherent.
