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
  const tabId = `#tab-${panel}`;
  await page.locator(tabId).click();
  // Per-tab readiness signal — mirrors the drift guard.
  if (panel === "cues")
    await expect(page.locator("#tracks-section")).toBeAttached();
  else if (panel === "library")
    await expect(page.locator("#health-section")).toBeAttached();
  else if (panel === "discover") {
    await expect(page.locator("#discover-section")).toBeAttached();
    await expect(page.locator("#download-section")).toBeAttached();
  }
}

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

// `buildIdSelector` lives in `./per-control-sweep.helpers` so the regression
// test in `per-control-sweep.selector.test.ts` can import the pure helper
// without importing a Playwright test file — Playwright forbids the latter
// and previously aborted the entire `npm test` run during discovery (#112).

async function safeInteract(page: Page, row: ControlRow) {
  const sel = buildIdSelector(row.id);
  const locator = page.locator(sel);
  await expect(locator, `control ${row.id} is missing`).toHaveCount(1);

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
      // leave the modal up for the next row.
      await locator.click({ trial: false });
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
          expect(
            capture.errors,
            `console errors during ${row.id} interaction`,
          ).toEqual([]);
        } finally {
          capture.detach();
        }
      });
    }
  });
}

// Global controls always run.
runRows("global", INVENTORY.globalControls);

// Panel controls — gated by AUTOCUE_QA_SCOPE.
const panelsToRun: PanelName[] = SCOPE ?? [...PANEL_NAMES];
for (const p of panelsToRun) {
  runRows(p, INVENTORY.panelControls[p]);
}
