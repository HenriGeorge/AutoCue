import { test, expect } from "@playwright/test";

/**
 * AutoCue 2.0 — Library as a place (tab-bar retirement).
 *
 * Scope: the LAYOUT truths jsdom can't see (jsdom-layout-blind-spot rule) — the
 * Library rail place swapping the workbench centre to the library tools, the
 * Cues/Library tab bar being retired (#tab-group hidden, the #tab-nav status row
 * kept), the sticky/Virtualizer invariants surviving a hide/show round-trip
 * (TASK-033/037), and the fixed action bar. The library TOOLS themselves
 * (health / cue-tools / discogs / comments / playlist-suggest / set-builder) are
 * unchanged legacy surfaces covered elsewhere; this spec exercises the door only.
 *
 * Runs against the local-mode sandbox; the workbench is default-on (c3dcff0).
 */
test.describe("Library place (tab-bar retirement)", () => {
  test.beforeEach(async ({ page }) => {
    await page.goto("/");
    await expect(page.locator("#app-status")).toBeVisible({ timeout: 10_000 });
    await expect(page.locator("body.wb-active")).toBeVisible({ timeout: 10_000 });
    await expect(page.locator("#wb-library-place")).toBeVisible();
  });

  test("tab bar retired: #tab-group hidden, #tab-nav status row + status sentence stay", async ({ page }) => {
    await expect(page.locator("#tab-nav")).toBeVisible();   // the status row remains
    await expect(page.locator("#tab-group")).toBeHidden();  // the Cues/Library buttons are gone
    await expect(page.locator("#app-status")).toBeVisible(); // status sentence intact
  });

  test("rail place swaps the centre pane: grid/sticky/inspector hide, action bar stays fixed", async ({ page }) => {
    // #tracks-section is display:none until /api/tracks loads and adds .visible;
    // against the real-sized sandbox DB that paint can exceed the 5s default, so
    // wait explicitly (same pattern as the "swapping back" test below).
    await expect(page.locator("#track-list")).toBeVisible({ timeout: 20_000 });
    await page.locator("#wb-library-place").click();

    await expect(page.locator("#health-section")).toBeVisible(); // a library tool now owns the centre
    await expect(page.locator("#track-list")).toBeHidden();
    await expect(page.locator("#tracks-sticky")).toBeHidden();
    await expect(page.locator("#wb-grid-head")).toBeHidden();
    await expect(page.locator("#wb-inspector")).toBeHidden();
    await expect(page.locator("body")).toHaveClass(/wb-place-library/);
    await expect(page.locator("#wb-library-place")).toHaveClass(/active/);

    // #action-bar stays fixed against the viewport (TASK-037).
    const pos = await page.locator("#action-bar").evaluate(
      (el) => getComputedStyle(el as HTMLElement).position).catch(() => null);
    if (pos !== null) expect(pos).toBe("fixed");
  });

  test("renders the redesigned 'Cue-readiness scan' surface (ring + header, auto-scan fires on entry)", async ({ page }) => {
    await page.locator("#wb-library-place").click();
    await expect(page.locator("#health-section.lh-surface")).toBeVisible();
    await expect(page.locator("#health-section .lh-h1")).toHaveText("Cue-readiness scan");
    // the 124px cue-readiness ring arc exists with the r=54 circumference dasharray
    await expect(page.locator("#health-ring-arc")).toHaveAttribute("stroke-dasharray", "339.3");
    // Auto-scan-on-entry: the scan fires automatically on first Library place activation.
    // `scanLibraryHealth()` sets `summary.style.display = ''` at scan START (before any
    // SSE events), so #health-summary becoming visible proves the scan was triggered.
    // Note: we can't assert on #health-scan-label-text — _setBtnCancellable() replaces
    // the button's innerHTML (removing the span) while the scan is in-flight, making
    // the element absent from DOM until the scan completes and _setBtnLoading restores it.
    await expect(page.locator("#health-summary")).toBeVisible({ timeout: 10_000 });
    // At scan START scanLibraryHealth() explicitly hides #health-fixes
    // (`if (_fixesBox) _fixesBox.style.display = 'none'`). It must remain
    // hidden until the summary SSE event lands — a premature appearance is a regression.
    await expect(page.locator("#health-fixes")).toBeHidden();
  });

  test("swapping back restores the grid and the sticky bar still pins on scroll", async ({ page }) => {
    // #tracks-section { display: none } by default; only becomes visible (.visible class)
    // once /api/tracks loads. We must wait for that before testing grid visibility after
    // the place swap-back — otherwise the restored grid would still appear "hidden" because
    // its container (#tracks-section) hasn't received .visible yet.
    await expect(page.locator("#track-list")).toBeVisible({ timeout: 20_000 });

    await page.locator("#wb-library-place").click();
    await expect(page.locator("#health-section")).toBeVisible();

    // Leaving via a crate click is a grid intent — it exits the place.
    await page.locator("#wb-crates .wb-crate").first().click();
    await expect(page.locator("#track-list")).toBeVisible();
    await expect(page.locator("body")).not.toHaveClass(/wb-place-library/);

    // The document-level sticky survived the hide/show round-trip: once scrolled
    // past its pin point a FURTHER scroll leaves its top unchanged.
    const topAt = () => page.locator("#tracks-sticky").evaluate(
      (el) => el.getBoundingClientRect().top);
    await page.mouse.wheel(0, 1500);
    await page.waitForTimeout(150);
    const pinned1 = await topAt();
    await page.mouse.wheel(0, 800);
    await page.waitForTimeout(150);
    const pinned2 = await topAt();
    expect(pinned1).toBeGreaterThanOrEqual(0);
    expect(Math.abs(pinned2 - pinned1)).toBeLessThan(12);
  });

  test("⌘K 'Go to Library' opens the place", async ({ page }) => {
    await page.keyboard.press("ControlOrMeta+k");
    await expect(page.locator("#pal-input")).toBeFocused();
    await page.locator("#pal-input").fill("go to libr");
    const opt = page.locator("#cmd-palette", { hasText: "Go to Library" })
      .getByText("Go to Library");
    await expect(opt).toBeVisible();
    await opt.click();
    await expect(page.locator("#wb-library-place")).toHaveClass(/active/);
    await expect(page.locator("#health-section")).toBeVisible();
  });

  test("both themes render the place without error", async ({ page }) => {
    await page.locator("#wb-library-place").click();
    await expect(page.locator("#health-section")).toBeVisible();
    await page.locator("#theme-toggle").click();
    await expect(page.locator("html.dark")).toHaveCount(1);
    await expect(page.locator("#health-section")).toBeVisible();
    await expect(page.locator("#wb-library-place")).toHaveClass(/active/);
  });
});
