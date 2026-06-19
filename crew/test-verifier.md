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
