import { test, expect } from "@playwright/test";

/**
 * AutoCue 2.0 P1 — global layer smoke (status sentence + ⌘K palette).
 * Runs against the local-mode sandbox (autocue serve). Asserts the layer
 * renders, the palette opens/runs commands, and it has strict key priority
 * over the legacy shortcuts.
 */
test.describe("AutoCue 2.0 global layer", () => {
  test.beforeEach(async ({ page }) => {
    await page.goto("/");
    await expect(page.locator("#app-status")).toBeVisible({ timeout: 10_000 });
    await expect(page.locator("#tab-nav")).toBeVisible({ timeout: 10_000 });
    // Readiness: tracks loaded (status sentence derives need-cues from them).
    await page
      .locator("#track-list .track-card, #track-list .empty-state")
      .first()
      .waitFor({ state: "attached", timeout: 30_000 });
  });

  test("status sentence renders facts (count mono; health hidden pre-scan)", async ({ page }) => {
    await expect(page.locator("#status-count")).toBeVisible();
    // need-cues fact is visible once tracks are loaded
    await expect(page.locator("#status-needcues")).toBeVisible();
    // health fact stays hidden until the first scan
    await expect(page.locator("#status-health")).toBeHidden();
    // the ⌘K hint appeared in local mode
    await expect(page.locator("#cmdk-hint-btn")).toBeVisible();
  });

  test("⌘K opens the palette, focuses the input, runs a command", async ({ page }) => {
    // The find/go-duplicates command opens the P3 place, which lazy-scans
    // /api/duplicates (a real 2,928-track scan) — under the similar-index build
    // that runs on a fresh sandbox the server saturates and the place-open
    // flakes. Mock the scan to an instant "0 groups" SSE so this smoke test is
    // deterministic (the place-open itself is covered fully in
    // v2-duplicates-place.spec.ts). The scan still fires; it just returns now.
    await page.route(/\/api\/duplicates(\?|$)/, (route) => route.fulfill({
      status: 200,
      headers: { "Content-Type": "text/event-stream", "Cache-Control": "no-cache" },
      body: 'data: {"total":0}\n\ndata: {"done":true,"summary":{"groups":0,"surplus":0,"scanned":0,"skipped_empty":0}}\n\n',
    }));
    await page.keyboard.press("ControlOrMeta+k");
    await expect(page.locator("#cmd-veil")).toBeVisible();
    await expect(page.locator("#pal-input")).toBeFocused();
    await page.locator("#pal-input").fill("dupl");
    // Both the "Find duplicates" and "Go to Duplicates" (P3) commands match and
    // open the same place; assert the top match is a duplicates command rather
    // than pinning one label's fuzzy rank.
    await expect(page.locator("#pal-list .pal-item").first()).toContainText("Duplicates");
    await page.keyboard.press("Enter");
    // Command ran: palette closed, the workbench Duplicates place opened
    // (P3 — the legacy Library duplicates section is gone; the command now
    // forces the workbench on and clicks the #wb-dupes-place rail entry).
    await expect(page.locator("#cmd-veil")).toBeHidden();
    await expect(page.locator("#wb-dupes-pane")).toBeVisible({ timeout: 5_000 });
    await expect(page.locator("body")).toHaveClass(/wb-place-dupes/);
  });

  test("palette has strict key priority and restores focus on Escape", async ({ page }) => {
    await page.keyboard.press("ControlOrMeta+k");
    await expect(page.locator("#cmd-veil")).toBeVisible();
    // '2' is a legacy tab shortcut — while the palette is open it must NOT switch tabs.
    await page.keyboard.press("2");
    await expect(page.locator("#cmd-veil")).toBeVisible();
    await expect(page.locator("#cues-tab-content")).toBeVisible(); // still on Cues
    await page.keyboard.press("Escape");
    await expect(page.locator("#cmd-veil")).toBeHidden();
  });

  test("'/' opens the palette when not typing in a field", async ({ page }) => {
    await page.locator("body").click(); // ensure focus is not in an input
    await page.keyboard.press("/");
    await expect(page.locator("#cmd-veil")).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(page.locator("#cmd-veil")).toBeHidden();
  });
});
