import { test, expect, type Page } from "@playwright/test";

/**
 * AutoCue 2.0 P2 — workbench dense thin-row grid (TASK part 2b).
 *
 * The workbench is flag-gated (localStorage.ac_workbench === '1') and reads the
 * flag at module load, gated on the local-mode event. So we set the flag via an
 * init script BEFORE navigation, then load the page — initWorkbench() picks it
 * up and `body.wb-active` flips on once local mode is detected.
 *
 * Asserts the three load-bearing grid invariants:
 *   (a) every visible .wb-row reports the SAME fixed height (Virtualizer O(1)),
 *   (b) scrolling keeps the mounted row count bounded (virtualization recycles),
 *   (c) clicking a row populates #wb-inspector-body (inspector wiring).
 */

async function bootWorkbench(page: Page) {
  await page.addInitScript(() => {
    try { localStorage.setItem("ac_workbench", "1"); } catch (_) {}
  });
  await page.goto("/");
  await expect(page.locator("#tab-nav")).toBeVisible({ timeout: 10_000 });
  // The workbench only activates in local mode; wait for it to take over.
  await expect(page.locator("body")).toHaveClass(/wb-active/, { timeout: 15_000 });
  // Wait for the library load to settle: either real rows appear, or the
  // genuinely-empty empty-state. On a cold sidecar the first paint is the
  // empty-state and rows mount once /api/tracks resolves — so prefer .wb-row.
  await page
    .locator("#track-list .wb-row, #track-list .empty-state")
    .first()
    .waitFor({ state: "attached", timeout: 30_000 });
  // Give /api/tracks a beat to replace a transient empty-state with rows.
  await page
    .locator("#track-list .wb-row")
    .first()
    .waitFor({ state: "attached", timeout: 30_000 })
    .catch(() => {
      /* genuinely-empty sandbox: leave rows at 0, tests self-skip */
    });
}

test.describe("AutoCue 2.0 workbench dense grid", () => {
  test.beforeEach(async ({ page }) => {
    await bootWorkbench(page);
  });

  test("activates: body.wb-active + sticky column header visible, no stray empty-state", async ({ page }) => {
    await expect(page.locator("body")).toHaveClass(/wb-active/);
    await expect(page.locator("#wb-grid-head")).toBeVisible();
    // The stray "No library loaded" empty-state must NOT bleed through once
    // tracks are loaded. (Genuinely-empty sandbox would have zero .wb-row, in
    // which case this test is skipped below.)
    const rowCount = await page.locator("#track-list .wb-row").count();
    test.skip(rowCount === 0, "sandbox library is empty — grid assertions need tracks");
    await expect(page.locator("#track-list .empty-state")).toHaveCount(0);
  });

  test("(a) all visible wb-rows report identical height", async ({ page }) => {
    const rows = page.locator("#track-list .wb-row");
    const n = await rows.count();
    test.skip(n === 0, "sandbox library is empty");
    const heights = await rows.evaluateAll((els) =>
      els.map((el) => Math.round((el as HTMLElement).getBoundingClientRect().height)),
    );
    const unique = [...new Set(heights)];
    expect(
      unique.length,
      `wb-rows must be uniform height; saw ${JSON.stringify(unique)}`,
    ).toBe(1);
    // Matches WB_ROW_H = 46 in 06-render.js.
    expect(unique[0]).toBe(46);
  });

  test("(b) scrolling keeps the mounted row count bounded (recycling)", async ({ page }) => {
    const rows = page.locator("#track-list .wb-row");
    const initial = await rows.count();
    test.skip(initial === 0, "sandbox library is empty");

    // Page-level scroll (the Virtualizer uses document scroll, TASK-037).
    for (let i = 1; i <= 6; i++) {
      await page.mouse.wheel(0, 4000);
      await page.waitForTimeout(60);
    }
    const afterScroll = await rows.count();
    // The mounted window is ~viewport+buffer rows — never the whole library.
    // A generous cap that still catches a "render-everything" regression.
    expect(afterScroll).toBeLessThan(120);
    // And it didn't collapse to zero (rows still recycle into view).
    expect(afterScroll).toBeGreaterThan(0);
  });

  test("(c) clicking a row populates #wb-inspector-body", async ({ page }) => {
    const rows = page.locator("#track-list .wb-row");
    const n = await rows.count();
    test.skip(n === 0, "sandbox library is empty");

    // Inspector starts on its empty-state.
    await expect(page.locator("#wb-inspector-empty")).toBeVisible();

    // The first virtualized row is snapped under the sticky #tracks-sticky bar
    // (topOcclusionFn) and won't reliably receive a click; scrolling to reach a
    // mid-list row recycles the node mid-action. Instead, find a row whose box
    // sits clearly below the sticky bar and click its title cell at coordinates
    // — stable against virtualization (no node-handle to detach).
    const stickyBottom = await page
      .locator("#tracks-sticky")
      .evaluate((el) => el.getBoundingClientRect().bottom);

    const box = await rows.evaluateAll((els, sb) => {
      for (const el of els) {
        const cell = el.querySelector(".wb-c-title") as HTMLElement | null;
        if (!cell) continue;
        const r = cell.getBoundingClientRect();
        if (r.top > sb + 8 && r.width > 0 && r.height > 0) {
          return { x: r.left + r.width / 2, y: r.top + r.height / 2 };
        }
      }
      return null;
    }, stickyBottom);
    expect(box, "expected a wb-row clear of the sticky bar").not.toBeNull();

    await page.mouse.click(box!.x, box!.y);

    await expect(page.locator("#wb-inspector-body")).toBeVisible({ timeout: 5_000 });
    await expect(page.locator("#wb-inspector-empty")).toBeHidden();
    // The focused row gets the green-rail class.
    await expect(page.locator("#track-list .wb-row.wb-focused")).toHaveCount(1);
    // Inspector header carries the track title section.
    await expect(page.locator("#wb-inspector-body .wb-insp-title")).toBeVisible();
  });
});
