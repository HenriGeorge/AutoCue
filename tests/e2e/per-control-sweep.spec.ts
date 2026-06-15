import { test, expect, type Page } from "@playwright/test";
import {
  INVENTORY,
  PANEL_NAMES,
  type ControlRow,
  type PanelName,
} from "./control-inventory";
import { buildIdSelector } from "./per-control-sweep.helpers";

/**
 * Per-control sweep — the behavioural layer of the QA agent's harness.
 *
 * Each row in the inventory becomes its own Playwright test() so the report
 * shows row-level pass/fail. v1 of the sweep checks the minimum that's
 * worth its CI cost: the control is present, reachable (after expanding
 * collapsed sections), and clicking / focusing it does not throw an
 * uncaught error in the page console. Richer verify objects (network
 * expectations, SSE assertions, requiresState, forbiddenRequests) layer
 * on top as the sweep stabilises — they live in `verify` fields on the
 * inventory rows.
 *
 * Scope: an env var `AUTOCUE_QA_SCOPE` is a comma-separated subset of
 *   cues,library,discover
 * If unset → all three panels. Global controls always run regardless of
 * scope.
 */

function parseScope(): PanelName[] | undefined {
  const raw = process.env.AUTOCUE_QA_SCOPE?.trim();
  if (!raw) return undefined;
  const allowed = new Set<PanelName>(PANEL_NAMES);
  const requested = raw
    .split(/[,\s]+/)
    .map((s) => s.toLowerCase())
    .filter(Boolean);
  const out = new Set<PanelName>();
  for (const r of requested) {
    if ((allowed as Set<string>).has(r)) out.add(r as PanelName);
    else
      throw new Error(
        `AUTOCUE_QA_SCOPE: unknown panel '${r}'. Allowed: ${[...allowed].join(", ")}`,
      );
  }
  return [...out];
}

const SCOPE = parseScope();

async function gotoPanel(page: Page, panel: PanelName | "global") {
  await page.goto("/");
  await expect(page.locator("#app-status")).toBeVisible({ timeout: 10_000 });
  if (panel === "global") return; // global rows interact with persistent chrome
  if (panel === "discover") {
    // P5: #tab-discover retired — Discover is now the #wb-disc-place workbench
    // rail place (workbench is default-on in local mode). Mirrors the same
    // navigation fix applied to control-inventory.spec.ts. Without this, these
    // rows (network-gated-skipped today) would click a removed element if they
    // ever un-skip with a Discogs token present.
    await page.locator("#wb-disc-place").click();
  } else if (panel === "library") {
    // Tab bar retired — Library is the #wb-library-place workbench rail place.
    await page.locator("#wb-library-place").click();
  }
  // panel === "cues" is the default workbench centre — no navigation click needed.
  // Per-tab readiness signal — mirrors the drift guard.
  if (panel === "cues")
    await expect(page.locator("#tracks-section")).toBeAttached();
  else if (panel === "library")
    await expect(page.locator("#health-section")).toBeAttached();
  else if (panel === "discover") {
    // Discover tab wrapper was renamed to `#disc-v2-section` during the
    // Discover v2 rebuild (see `docs/index.html` § initDiscoverV2 guard).
    await expect(page.locator("#disc-v2-section")).toBeAttached();
    await expect(page.locator("#download-section")).toBeAttached();
  }
}

async function expandHiddenSections(page: Page) {
  // Force every collapsible / panel container into an open, reachable state
  // so per-control-sweep rows aren't blocked by inline display:none or by
  // class-based collapse (e.g. `#settings-section.collapsed` set via
  // `_collapseSettings`, applied automatically when local mode boots).
  //
  // We mutate inline style + className directly rather than clicking title
  // toggles: the same approach the drift guard
  // (control-inventory.spec.ts:25-47) uses, and it avoids firing onclick
  // handlers that may persist state or fire network requests.
  //
  // Timing: the boot path in `docs/index.html` runs a staggered fade-in
  // chain that adds `visible` to `#settings-section` and, in local mode,
  // immediately calls `_collapseSettings()` inside the same setTimeout
  // callback. If we strip `collapsed` before that callback fires, the
  // collapse re-applies a tick later and the click intercept returns.
  // Wait for `#settings-section.visible` first so we know the boot fade
  // chain has reached the settings entry, THEN strip — the class won't
  // be re-applied by any later code path (only manual toggle would).
  await page.waitForFunction(() => {
    const el = document.getElementById("settings-section");
    // If the section isn't on the page (Pages mode / unusual fixture),
    // skip the wait — nothing to collapse-race against.
    if (!el) return true;
    return el.classList.contains("visible");
  });
  await page.evaluate(() => {
    const SELECTORS = [
      "[id$='-section']",
      "[id$='-body']",
      "[id$='-panel']",
      "section",
      "[class*='-params']",
    ];
    const COLLAPSE_CLASSES = ["collapsed", "is-collapsed", "hidden"];
    const seen = new Set<HTMLElement>();
    for (const sel of SELECTORS) {
      for (const el of Array.from(
        document.querySelectorAll<HTMLElement>(sel),
      )) {
        if (seen.has(el)) continue;
        seen.add(el);
        if (el.style.display === "none") el.style.display = "";
        for (const cls of COLLAPSE_CLASSES) {
          if (el.classList.contains(cls)) el.classList.remove(cls);
        }
      }
    }
  });
}

async function forceShowAncestors(page: Page, controlId: string) {
  // Walk up from the control's element clearing inline `display:none` on
  // every ancestor, and stripping a small allowlist of collapse classes.
  // This catches the "container ID doesn't match *-section/-body/-panel"
  // shapes that the broad sweep helper deliberately doesn't try to
  // enumerate — e.g. `#existing-cues-info` (state-gated info row) or
  // `#skip-colored-label` (gated on color-by-bpm visibility). Safe
  // because we only walk a single chain (the row's ancestors), so we
  // never reveal sibling modals / drawers.
  //
  // Besides inline display:none + collapse classes, two more hiding
  // mechanisms appear on this chain in docs/index.html:
  // - the `hidden` ATTRIBUTE (e.g. `#dl-wav-warning`, shown only after the
  //   user picks the WAV format), and
  // - `aria-hidden="true"` driving a stylesheet rule (e.g.
  //   `.disc-v2-dl-confirm { display:none }` +
  //   `.disc-v2-dl-confirm[aria-hidden="false"] { display:block }`), which
  //   inline-style clearing can't override.
  // Clear both, same single-chain scope.
  await page.evaluate((id) => {
    const COLLAPSE_CLASSES = ["collapsed", "is-collapsed", "hidden"];
    const el = document.getElementById(id);
    if (!el) return;
    let node: HTMLElement | null = el;
    while (node && node !== document.body) {
      if (node.style.display === "none") node.style.display = "";
      if (node.hasAttribute("hidden")) node.removeAttribute("hidden");
      if (node.getAttribute("aria-hidden") === "true") {
        node.setAttribute("aria-hidden", "false");
      }
      for (const cls of COLLAPSE_CLASSES) {
        if (node.classList.contains(cls)) node.classList.remove(cls);
      }
      node = node.parentElement;
    }
  }, controlId);
}

async function applySetup(page: Page, row: ControlRow) {
  if (row.setup !== "select-track") return;
  // Tick the first track card's bulk-select checkbox so `selectedTrackIds`
  // is non-empty and `updateSelectionBar` slides in #action-bar (adds
  // .visible + aria-hidden=false). This is the real user path to the
  // action-bar buttons — force-clicking the off-viewport bar (the previous
  // approach) fails with "Element is outside of the viewport".
  await page
    .locator("#track-list [data-testid='track-card']")
    .first()
    .waitFor({ state: "attached", timeout: 15_000 });
  // In album-group view (the default for an album-rich library) every track
  // card sits inside a collapsed `.album-tracks` container (display:none
  // until `.open`), so no checkbox is visible until a group is expanded.
  // Expand the first album header in that case; flat/virtualized view has
  // visible checkboxes immediately.
  const visibleCb = page.locator("#track-list .track-select-cb:visible");
  if ((await visibleCb.count()) === 0) {
    await page.locator("#track-list .album-header").first().click();
  }
  const cb = visibleCb.first();
  await expect(
    cb,
    `setup select-track for ${row.id}: no selectable track checkbox`,
  ).toBeVisible({ timeout: 15_000 });
  // Issue #219: the first visible track-select checkbox sits UNDER the sticky
  // `#top-bar` + `#tracks-sticky`/`#wb-grid-head` chrome, so a real pointer
  // click — even `{ force: true }` — lands on the occluding header rather than
  // the checkbox (`.check()` times out, or reports "did not change its state").
  // `force` skips the actionability *assertions*, not the physical occlusion of
  // the synthetic click point. This is a SETUP step: its only job is to put a
  // track in `selectedTrackIds` so `updateSelectionBar` slides #action-bar in
  // for the row under test. So we set the state directly and fire the same
  // `change` event the card wires (06-render.js) — the mutate-and-dispatch
  // pattern this helper already uses for expandHiddenSections/forceShowAncestors
  // rather than fighting the sticky chrome. The action-bar button itself is
  // still clicked for real by safeInteract downstream.
  await cb.evaluate((el) => {
    if (!(el instanceof HTMLInputElement) || el.checked) return;
    el.checked = true;
    el.dispatchEvent(new Event("change", { bubbles: true }));
  });
  await expect(
    page.locator("#action-bar"),
    `setup select-track for ${row.id}: #action-bar did not become visible`,
  ).toHaveClass(/\bvisible\b/);
}

async function safeInteract(page: Page, row: ControlRow) {
  const sel = buildIdSelector(row.id);
  const locator = page.locator(sel);
  await expect(locator, `control ${row.id} is missing`).toHaveCount(1);
  // Row-declared state precondition (e.g. select a track so #action-bar
  // slides in) — must run before the reveal walk and the interaction.
  await applySetup(page, row);
  // Per-row last-mile reveal: even after the panel-wide
  // `expandHiddenSections`, some controls live inside state-gated
  // wrappers (#existing-cues-info, #skip-colored-label, …) that the
  // sweep helper doesn't try to enumerate by selector. Walk this row's
  // ancestor chain and clear inline display:none / collapse classes so
  // the click / focus interaction below can land on the actual element.
  await forceShowAncestors(page, row.id);

  // Per-kind interaction. v1 is conservative — for safeOnRealDb:false rows
  // (writes / mutations) we ONLY confirm presence + focusability. Real
  // exercise lands when we wire per-row verify objects.
  if (row.safeOnRealDb === false) {
    await expect(locator).toBeAttached();
    return;
  }

  switch (row.kind) {
    case "button":
      // For modal-trigger buttons we click, then close via Escape so we don't
      // leave the modal up for the next row. `clickStrategy: "force"` on the
      // inventory row bypasses Playwright's scrollIntoView precheck — needed
      // for `position: fixed` action-bar buttons (issue #190): Playwright
      // sees `position:fixed` as "outside viewport" and burns 30s retrying
      // `scrollIntoView` on an element that ignores it by definition.
      await locator.click({
        trial: false,
        force: row.clickStrategy === "force",
      });
      await page.keyboard.press("Escape").catch(() => {});
      break;
    case "checkbox": {
      const wasChecked = await locator.isChecked();
      await locator.click();
      const isChecked = await locator.isChecked();
      expect(
        wasChecked,
        `checkbox ${row.id} did not toggle on click`,
      ).not.toBe(isChecked);
      // restore
      await locator.click();
      break;
    }
    case "select": {
      const optCount = await locator.locator("option").count();
      expect(
        optCount,
        `select ${row.id} has no options`,
      ).toBeGreaterThan(0);
      // Focus only — don't change the selection (some selects fire fetches).
      await locator.focus();
      break;
    }
    case "number":
    case "text":
    case "search":
      // Focus only — typing into search-driven controls fires fetches we'd
      // want to assert about with verify.network later.
      await locator.focus();
      break;
    case "password":
      // Never type into password fields. Confirm focusable presence only.
      await locator.focus();
      break;
    default:
      await locator.focus();
  }
}

function makeConsoleCapture(page: Page) {
  const errors: string[] = [];
  const onMsg = (msg: import("@playwright/test").ConsoleMessage) => {
    if (msg.type() === "error") errors.push(msg.text());
  };
  const onPageError = (err: Error) => errors.push(`[pageerror] ${err.message}`);
  page.on("console", onMsg);
  page.on("pageerror", onPageError);
  return {
    errors,
    detach: () => {
      page.off("console", onMsg);
      page.off("pageerror", onPageError);
    },
  };
}

function runRows(panel: PanelName | "global", rows: ControlRow[]) {
  test.describe(`per-control sweep: ${panel}`, () => {
    for (const row of rows) {
      if (row.skipSweep) {
        test.skip(`${row.id} (${row.skipReason ?? "skipped"})`, () => {});
        continue;
      }
      test(`${row.id}`, async ({ page }) => {
        const capture = makeConsoleCapture(page);
        try {
          await gotoPanel(page, panel);
          await expandHiddenSections(page);
          await safeInteract(page, row);
          // 100ms tolerance for the action's microtasks to settle before
          // sampling console.
          await page.waitForTimeout(100);
          // Row-declared expected errors (substring match) are filtered out —
          // e.g. disc-v2-refresh-btn's /api/discover/feed 400 in the
          // token-less sandbox. Anything else still fails the row.
          const allowed = row.allowedConsoleErrors ?? [];
          const unexpected = capture.errors.filter(
            (e) => !allowed.some((a) => e.includes(a)),
          );
          expect(
            unexpected,
            `console errors during ${row.id} interaction`,
          ).toEqual([]);
        } finally {
          capture.detach();
        }
      });
    }
  });
}

// Regression guard for issue #171 — expandHiddenSections must strip the
// class-based collapse on `#settings-section` (auto-applied when local
// mode boots via `_collapseSettings`) AND must reach the auto-classify
// param sub-panel (a `cue-tools-params-auto-classify` div hidden by
// inline display:none, which the previous *-section/-body/-panel
// selector trio did NOT match). Without these, ~25 inventory rows time
// out at 30s each on click-intercepted.
test.describe("expandHiddenSections (helper)", () => {
  test("strips collapsed class from #settings-section after Cues tab load", async ({
    page,
  }) => {
    await gotoPanel(page, "cues");
    // Sanity: confirm the bug's precondition — the page auto-collapses
    // the settings section on local-mode boot. If this ever stops being
    // true, this regression test still holds (the assertion below is
    // about the helper, not about the initial state).
    const collapsedBefore = await page
      .locator("#settings-section")
      .evaluate((el) => el.classList.contains("collapsed"));
    await expandHiddenSections(page);
    const collapsedAfter = await page
      .locator("#settings-section")
      .evaluate((el) => el.classList.contains("collapsed"));
    expect(
      collapsedAfter,
      "expandHiddenSections must clear the .collapsed class so sweep rows are reachable",
    ).toBe(false);
    // Boundary: if precondition holds, helper truly flipped the bit; if
    // not, helper is at least idempotent (still false).
    if (collapsedBefore) expect(collapsedAfter).toBe(false);
  });

  test("clears inline display:none on cue-tools-params-* sub-panels (Library)", async ({
    page,
  }) => {
    await gotoPanel(page, "library");
    await expandHiddenSections(page);
    // All five `cue-tools-params-*` divs are inline `display:none` by
    // default (only the currently-selected op is shown). After the
    // helper runs, every one of them must be reachable so the at-* /
    // cue-* inputs inside the auto-classify sub-panel can be exercised.
    const stillHidden = await page.evaluate(() => {
      const out: string[] = [];
      for (const el of Array.from(
        document.querySelectorAll<HTMLElement>("[id^='cue-tools-params-']"),
      )) {
        if (el.style.display === "none") out.push(el.id);
      }
      return out;
    });
    expect(
      stillHidden,
      "expandHiddenSections must un-hide cue-tools-params-* sub-panels",
    ).toEqual([]);
  });
});

// Global controls always run.
runRows("global", INVENTORY.globalControls);

// Panel controls — gated by AUTOCUE_QA_SCOPE.
const panelsToRun: PanelName[] = SCOPE ?? [...PANEL_NAMES];
for (const p of panelsToRun) {
  runRows(p, INVENTORY.panelControls[p]);
}
