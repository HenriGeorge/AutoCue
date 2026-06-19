# Test-verifier — fix/design-workbench

## P4 — GATE-2 VERIFY (build committed: c1756f9 unit B, 741cddb unit A)

Sole live :3003 driver. Full stack run FRESH this turn; live app driven via Chrome DevTools MCP
against the WORKTREE code on `http://127.0.0.1:3003` bound to a **sandbox copy** of master.db
(zero write risk; read-only browse + a route-shim for the score/Discover feed — no real delete,
no real Discover scan). Anchor controlled via the sanctioned `window.ACBridge.nowPlayingId()`
accessor (confirmed live — the implementer's bridge edit is wired).

### STATIC + BEHAVIORAL — exact commands + real output THIS turn
| Leg | Command | Result | Verdict |
|---|---|---|---|
| Python | `python -m pytest -q` | **1477 passed, 7 skipped**, 4 warnings, 23.32s — exit 0 | MATCH (count UNCHANGED — drift proof) |
| Vitest | `npm test` | **62 files, 931 passed**, 5.51s — exit 0 (incl. new `v2-token-provenance` 7 + `v2-inspector-anchor` 9) | MATCH (~931 as expected) |
| e2e (new+extended) | `npx playwright test v2-inspector-anchor.spec.ts v2-duplicates-place.spec.ts` | **14 passed**, 27.6s — exit 0 | MATCH |
| e2e (zone-adjacent) | `npx playwright test v2-nightboard.spec.ts` | **6 passed**, 14.7s — exit 0 | MATCH (no `--zone-*` regression) |

e2e note (#189): A-10 (Discover release re-host) initially flaked when run AFTER the other 13
(auto-scan-on-open is non-deterministic under the single sandbox server's load). Fixed in-spec by
explicitly clicking the feed's `#disc-v2-refresh-btn` (mocked scan) instead of relying on
auto-scan — now 2.2s green both alone and in the combined run. Not a product regression.

### LIVE @127.0.0.1:3003 (Chrome DevTools MCP) — evidence captured this turn
- **B2 toolbar** (screenshot `live-b2-dupes-toolbar.png`): `#wb-dupes-rescan` = `secondary-btn
  wb-toolbar-sm`, text "Rescan", **no inline style**, computed `font-size:12px` / `padding:4px 12px`,
  left. `#wb-dupes-bulk-delete` = `primary wb-toolbar-sm wb-toolbar-spacer`, text "Delete
  non-keepers", `disabled`, **no inline style**, 12px / 4px 12px, `margin-left:auto` → flush-right
  (bulkLeft 1269 > rescanRight 316; parentRight−bulkRight = 0). ids + brand classes intact.
- **A good band** (screenshots `live-a-goodband-light.png` / `-dark.png`): now-playing set to
  William Orbit, focused a DIFFERENT track → "Transition in" card: mono `92` (JetBrains Mono),
  "from William Orbit - Surfin", 3 reasons. LIGHT score `rgb(21,154,5)` = `--green`; DARK
  `rgb(40,226,20)` = dark `--green`; green-dominant both → green=signal, good band only.
- **A hide-clean**: nothing playing + first focus (William Orbit) → sections = Energy/Scores/Cues/
  Similar, **NO "Transition in"**, no empty header.
- **A fallback**: nothing playing, prior focus Metallica, focus Queen → card "from Metallica -
  Nothing Else Matters" (anchor = previously-focused).
- **A release-mode** (screenshot `live-a-release-no-card.png`): Discover release "Madvillainy"
  re-hosted in the inspector → **NO "Transition in" card** (mode 'release' suppressed).
- **B1 token check**: `grep --zone-` → vendored `docs/design/tokens/colors.css` byte-equal
  `docs/css/app.css` both themes (incl. the dark `--zone-peak rgba(224, 48, 30, .06)` `.06` edge);
  `--nb-tile-height`/`--nb-joint-size` absent from the colour mirror; app.css uncommitted-diff = none.

## PER-ITEM parity verdict vs crew/DESIGN.md

### UNIT B1 — vendor `--zone-*` into the mirror
| DESIGN line | Verdict | Evidence |
|---|---|---|
| 4 light values byte-equal in vendored `:root` (B1-1..4) | **MATCH** | colors.css:49-52 == app.css:3528-3531 |
| 4 dark values byte-equal in vendored `html.dark` (B1-5..8, incl. peak `.06`) | **MATCH** | colors.css:105-108 == app.css:3536-3539 |
| sizings NOT vendored (B1-9) | **MATCH** | grep count 0 in colors.css |
| app.css values unchanged (B1-10) | **MATCH** | no working-tree diff vs HEAD |
| both-theme parity (B1-11) | **MATCH** | light+dark both byte-equal |

### UNIT B2 — fold dupes-toolbar inline styles into a class
| DESIGN line | Verdict | Evidence |
|---|---|---|
| no inline font/padding on either button (B2-1/2) | **MATCH** | live `getAttribute('style')` = '' both |
| shared class `.wb-toolbar-sm` 12px/4px 12px (B2-3) | **MATCH** | computed 12px / 4px 12px both (live + e2e) |
| `margin-left:auto` preserved → flush-right (B2-4/10) | **MATCH** | `.wb-toolbar-spacer`; bulk flush-right live + e2e |
| ids unchanged (B2-5/6) | **MATCH** | `#wb-dupes-rescan` / `#wb-dupes-bulk-delete` present |
| `secondary-btn`/`primary` preserved (B2-7) | **MATCH** | live className + e2e class assertions |
| display:none status spans untouched (B2-8) | **MATCH** | implementer left them; not in scope of change |
| `disabled` preserved (B2-9) | **MATCH** | live `disabled:true` + e2e `toBeDisabled` |
| no-build invariant (B2-11) | **MATCH** | HTML/CSS only; vitest collection unchanged (62 files) |

### UNIT A — inspector anchor-transition card
| DESIGN acceptance | Verdict | Evidence |
|---|---|---|
| card scores anchor→focused, mono score + band + reasons (mode 'track') | **MATCH** | live good-band card; e2e A-5 |
| anchor = now-playing; reads `ACBridge.nowPlayingId()` (the one legacy edit) | **MATCH** | accessor present live; good-band path |
| fallback = previously-focused | **MATCH** | live "from Metallica"; e2e A-3 |
| hidden when no anchor / anchor==self / release — clean, no empty header | **MATCH** | live hide-clean + release; e2e A-1/A-2/A-10 |
| band cutoffs ≥85/≥70 map to green/amber/muted; green only on good | **MATCH** | e2e A-5 green-dominant, A-6/A-7 NOT green; live light+dark green |
| score/data fragments mono | **MATCH** | live font "JetBrains Mono"; e2e A-5 |
| stale response discarded on rapid re-focus | **MATCH** | e2e A-11 (one card, anchored to last focus) |
| `!r.ok` silent (no toast, no card) | **MATCH** | vitest (911→931 incl. inspector-anchor 9); covered by unit layer |
| TASK-033/037 + 4 existing consumers stay green | **MATCH** | e2e grid/nightboard green; live sections intact |
| no new backend endpoint; pytest count unchanged | **MATCH** | 1477 passed unchanged |

### DESIGN VERIFY/GATE-2 acceptance block
STATIC ✅ · BEHAVIORAL ✅ (alone) · LIVE B2 ✅ · LIVE A (light+dark + fallback + release) ✅ ·
token check ✅ — **all MATCH**.

STATUS: DONE — full stack green this turn (pytest 1477/7, vitest 931, e2e 14+6), live @127.0.0.1:3003
verified, every B1/B2/A DESIGN acceptance line = MATCH. Build matches the approved design.

## FINAL merge-gate confirmation (HEAD 04f5812)

Full three-leg stack run FRESH this turn at branch HEAD `04f58125` (build unchanged since P4;
docs 6d8ef60 + e2e specs ef0819a + crew 04f5812 landed after — re-confirmed nothing regressed,
committed specs pass from the repo).

| Leg | Command | Real result | Verdict |
|---|---|---|---|
| Python | `python -m pytest -q` | **1477 passed, 7 skipped**, 23.44s — exit 0 | GREEN (count UNCHANGED) |
| Vitest | `npm test` | **62 files, 931 passed**, exit 0 | GREEN |
| e2e blast-radius (alone) | `npx playwright test v2-inspector-anchor.spec.ts v2-duplicates-place.spec.ts v2-nightboard.spec.ts` | **20 passed**, 38.7s — exit 0 | GREEN |
| e2e FULL suite (merge gate) | `npx playwright test` | **223 passed, 12 failed, 20 skipped**, 7.0m | GREEN* (see below) |

**The 12 full-suite failures are ALL the documented #189 Discover-contention baseline** — none
outside it (the change blast radius — inspector-anchor / dupes / nightboard — is in the 223 passed):
- `discover-v2.spec.ts` ×4 (feed-on-Refresh, release re-host, `?` help, Save flip)
- `v2-discover-shell.spec.ts` ×7 (P5 place a/b, c, d, e, f, g, h)
- `per-control-sweep.spec.ts` ×1 (`disc-v2-refresh-btn`)

Re-run ALONE to confirm #189 (server saturation under the full single-worker run), not a regression:
- `npx playwright test discover-v2.spec.ts v2-discover-shell.spec.ts` → **12 passed**, 16.0s — exit 0
- `npx playwright test per-control-sweep.spec.ts -g "disc-v2-refresh-btn"` → **1 passed**, 4.3s — exit 0

All 12 pass in isolation ⇒ confirmed pre-existing #189 contention baseline, NOT introduced by this
branch. No regression in the change set.

VERDICT: **GREEN** — pytest 1477/7 (unchanged), vitest 931, e2e change-blast-radius 20/20, full
e2e 223 passed with only the documented #189 Discover-contention flakes (all green run alone).
Merge gate satisfied.

STATUS: DONE (green)

---

## P3 — Review Dock e2e spec AUTHORED (NOT run; awaiting implementer commit)

Read first: `crew/DESIGN.md` (Review Dock, approved 2026-06-19) + `crew/test-designer.md` coverage map.
Matched repo e2e conventions (127.0.0.1 baseURL via `page.goto("/")`, never localhost; the v2-*.spec.ts
harness — `addInitScript` flag-before-load, route-mock). Syntax check (NOT a run):
`esbuild --bundle --external:@playwright/test` → parse + bundle clean. Suite NOT executed, no :3003 server started.

### NEW `tests/e2e/v2-review-dock.spec.ts`
`/api/review-note` is ALWAYS route-mocked (per-test hit log + controllable status) — the real endpoint
appends to `crew/REVIEW-NOTES.md`, which the e2e never writes (the file-append + prod-403 dev-gate are
pytest's job). Two describes: render-gates (own init script) + gated-on (flag `ac_review_dock='1'` before load).

| State | Test | Asserts |
|---|---|---|
| S-5 | flag unset → no render | `.review-dock` count 0 (local mode, flag absent) |
| S-6 | both gates → render pinned bottom | `.review-dock` visible, `position:fixed`, bottom edge == viewport bottom |
| U-1 | idle form sub-nodes | sr-only `label[for=review-dock-input]` "Describe a change for this page"; `.review-dock-page` mono; input placeholder "describe a change for this page…"; one Send button |
| U-3 | empty submit → noop | whitespace + empty Enter → route mock 0 hits |
| U-6 | submit ok | POST body `{page:<str>, note:<typed>}`; `.review-dock-sent` shows "✓ sent"; input cleared; "sent" auto-clears within ~2s |
| U-7 | submit 500 | `#toast-stack .toast-item` appears; request attempted; input value PRESERVED |
| U-9 | page recompute at submit | enter Nightboard (`#nb-open-btn` → `body.nb-active`) then submit → posted `page === "nightboard"` |
| ST-1 | ink-pill Send | Send computed `background-color` == resolved `--ink`, and **!= `--green`** (green=signal only) |
| ST-5 | fixed bottom bar | `position:fixed`, flush-left, full-width, bottom edge at viewport bottom, `z-index` > `#action-bar` |
| ST-8 | both themes | screenshot light + `html.dark` (toggle via `#theme-toggle`); dock visible in both |

Notes for the P4 live run: toast surfaces as `.toast-item` in `#toast-stack` (`showToast`,
`07-helpers-events.js:121`); confirmation node is `.review-dock-sent`; Send located as `.review-dock button`.
`--ink`/`--green` resolved by painting a temp `<span>` so the assertion survives the implementer's exact
token wiring. (Pure logic — _derivePage map, double-submit guard, fake-timer clear, A11Y attrs — is the
vitest layer; 403 dev-gate + real file append is pytest. Run ALONE per #189.)

STATUS: DONE — spec authored, NOT run; awaiting implementer commit for the P4 live run.

---

## P4 — GATE-2 VERIFY (REVIEW DOCK · branch feat/review-dock @ 6331a8c)

Sole :3003 driver. Full stack run FRESH this turn; live app driven via Chrome DevTools MCP against
the WORKTREE code (cwd = worktree root so the real append lands in `crew/REVIEW-NOTES.md`; DB pointed
at a sandbox copy, removed after). Two server states exercised (ENABLED + DISABLED).

### STATIC + BEHAVIORAL — exact commands + real counts THIS turn
| Leg | Command | Result | Verdict |
|---|---|---|---|
| Python | `python -m pytest -q` | **1488 passed, 7 skipped**, 23.41s — exit 0 (incl. test_review_note 11) | GREEN |
| Vitest | `npm test` | **63 files, 946 passed**, exit 0 (incl. v2-review-dock 14) | GREEN |
| e2e (alone) | `npx playwright test v2-review-dock.spec.ts` | **10 passed, 1 failed** (ST-5b z-order) | RED — see MISMATCH below |

Two spec corrections made this turn (my spec, not the build): confirmation node is `.review-dock-status`
(not `.review-dock-sent`); split ST-5 into layout (passes) + ST-5b z-order (the real DESIGN check).

### LIVE @127.0.0.1:3003 — evidence captured this turn
**A) ENABLED** (`AUTOCUE_REVIEW_DOCK=1`, `localStorage.ac_review_dock='1'`):
- `curl -X POST …/api/review-note -d '{"page":"test","note":"hello from P4"}'` → **`{"ok":true}`** AND
  `crew/REVIEW-NOTES.md` gained `[2026-06-19 11:57:58] [test] hello from P4` — matches
  `^\[\d{4}-\d2-\d2 \d2:\d2:\d2\] \[test\] hello from P4$` (REGEX_MATCH=YES). The ONE real write.
- Dock renders **pinned at the viewport bottom**; page badge `[all]` (current surface); placeholder
  "describe a change for this page…"; ink-pill Send. Type + Send → **"✓ sent"**, input cleared
  (a 2nd real line `[…] [all] P4 live: dock submit works`). Screenshots: `rd-live-light.png`,
  `rd-live-sent.png`, `rd-live-dark.png` (light + `html.dark`).

**B) DISABLED proof (the safety line):**
- Server restarted with `AUTOCUE_REVIEW_DOCK` UNSET → same curl → **HTTP 403**
  `{"detail":"Review dock disabled (set AUTOCUE_REVIEW_DOCK=1)"}`; `REVIEW-NOTES.md` line count
  UNCHANGED (2 → 2, 0 "should be rejected" lines).
- `localStorage.ac_review_dock` cleared + fresh nav (local mode true) → `document.querySelector('.review-dock')` is **null** (dock does NOT render).

### PER-ITEM parity verdict vs crew/DESIGN.md §VERIFY + safety matrix
| DESIGN line | Verdict | Evidence |
|---|---|---|
| S-1 env unset → 403, file untouched | **MATCH** | live 403 + 0 new lines; pytest |
| S-2 env ≠ "1" → 403 | **MATCH** | pytest parametrised; live env-unset = 403 |
| S-3 env "1" → passes gate | **MATCH** | live `{"ok":true}` |
| S-4 localMode false → no render | **MATCH** | vitest (Pages/XML path); inert on Pages |
| S-5 flag unset (local) → no render | **MATCH** | live dock null + e2e S-5 |
| S-6 both gates → render pinned bottom | **MATCH** | live + e2e S-6 |
| API append `[ts] [page] note` + `{"ok":true}` | **MATCH** | live regex line; pytest 11 |
| API empty→422 / page default / newline-strip / autocreate | **MATCH** | pytest test_review_note (1488 total) |
| UI render: sr-only label, mono badge, placeholder, ink-pill Send | **MATCH** | e2e U-1 + live |
| UI empty submit → no request | **MATCH** | e2e U-3 (0 hits) |
| UI submit ok → posts {page,note}, "✓ sent", clears | **MATCH** | e2e U-6 + live "✓ sent" |
| UI error (500) → toast + note preserved | **MATCH** | e2e U-7 |
| UI page recompute at submit (Nightboard) | **MATCH** | e2e U-9 (`page:"nightboard"`) |
| ST-1 ink-pill Send, NEVER green | **MATCH** | e2e ST-1 (sendBg == `--ink`, ≠ `--green`) |
| ST-5 fixed, full-width, pinned bottom | **MATCH** | e2e ST-5 + live |
| ST-8 both themes | **MATCH** | e2e ST-8 + live light/dark |
| index.html markup unchanged (dev-only) | **MATCH** | dock JS-injected |
| **ST-5b z-index ABOVE #action-bar** (DESIGN §STYLE) | **MISMATCH** | dock `z-index:140` < `#action-bar` `z-index:350`. By default the action-bar is off-screen (`translateY(110%)`, pointer-events:none) so the dock is unobstructed — but when a track selection makes the action-bar visible (z350) it overlays the dock (z140), hiding it. DESIGN says "z-index above the action-bar". |

### The one blocker (trivial fix)
`docs/css/app.css` `.review-dock { … z-index: 140 }` → must exceed `#action-bar`'s `z-index: 350`
(e.g. **`z-index: 360`**). DESIGN's "e.g. 140" predates the action-bar being 350; the requirement is
"above the action-bar". Everything else is green + MATCH; the dock is functionally correct in the
default (no-selection) case. One-line CSS change, then re-run e2e (ST-5b) to confirm.

(Live-proof artifact: `crew/REVIEW-NOTES.md` holds the 2 proof lines — left in place as evidence; the
human/AI-tail can clear them.)

STATUS: BLOCKED — ST-5b z-order MISMATCH (dock z-index 140 < #action-bar 350; DESIGN §STYLE requires
above). Static green (pytest 1488/7, vitest 946), all safety gates (403 + no-render) + API + UI + ink-pill
+ both-themes MATCH and live-verified; ONLY the dock z-index needs bumping above 350 for full parity.

---

## P4 RE-VERIFY (z-index blocker fix · 477a9ad)

Fresh independent evidence THIS turn that the ST-5b blocker is resolved (`.review-dock` z-index
140→360, above `#action-bar`'s 350).

- e2e (ALONE): `npx playwright test v2-review-dock.spec.ts` → **11 passed**, 13.1s — exit 0.
  ST-5b (dock z-index above #action-bar) now GREEN along with every other row.
- LIVE @127.0.0.1:3003 (worktree, `AUTOCUE_REVIEW_DOCK=1` + `localStorage.ac_review_dock='1'`):
  selected a track so `#action-bar` is VISIBLE (`.visible` class true, on-screen) — the exact
  previously-broken scenario. Probe: dock `z-index:360` > action-bar `z-index:350`; dock bottom
  = 900 = viewport height (pinned); `elementFromPoint` at the dock input centre returns
  `.review-dock-input` → the dock is ON TOP, NOT occluded by the action bar. Screenshot:
  `rd-reverify-actionbar-visible.png`.

ST-5b: **MISMATCH → MATCH.** All other P4 rows remain MATCH (CSS-only change; pytest/vitest
unchanged this turn). Full parity vs crew/DESIGN.md §VERIFY achieved.

STATUS: DONE — full parity. e2e 11/11 green (incl. ST-5b); live action-bar-visible occlusion test
proves the dock (z360) sits above #action-bar (z350) and stays interactable.
