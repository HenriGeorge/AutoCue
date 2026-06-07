import { test, expect } from "@playwright/test";

/**
 * Canonical selector inventory. Every ID the QA agent prompt and the
 * other specs depend on is asserted here. When `docs/index.html`
 * refactors any of these IDs, this is the test that fails first — giving
 * the agent (and the human) a single place to update.
 *
 * Add a new selector here BEFORE referencing it in any other spec.
 */

const REQUIRED_SELECTORS: string[] = [
  // Tab navigation
  "#tab-nav",
  "#tab-cues",
  "#tab-library",
  "#tab-discover",
  // App status row
  "#app-status",
  // Filter controls (Cues tab)
  "#search-input",
  "#phrase-only-cb",
  "#beatgrid-only-cb",
  // Action bar + primary actions
  "#action-bar",
  "#preview-cues-btn",
  "#download-btn",
  // Mini player
  "#mini-waveform-wrap",
  "#mini-waveform",
  // Sticky track filter bar
  "#tracks-sticky",
];

test.describe("selectors exist in docs/index.html", () => {
  test.beforeEach(async ({ page }) => {
    await page.goto("/");
    // Local mode shows #tab-nav once /api/status succeeds.
    await expect(page.locator("#tab-nav")).toBeVisible({ timeout: 10_000 });
  });

  for (const sel of REQUIRED_SELECTORS) {
    test(`selector ${sel} exists`, async ({ page }) => {
      const count = await page.locator(sel).count();
      expect(
        count,
        `${sel} not found in docs/index.html — refactor and update REQUIRED_SELECTORS / agent prompt`,
      ).toBeGreaterThan(0);
    });
  }
});
