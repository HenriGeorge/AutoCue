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
  // P5: #tab-discover retired — Discover is the #wb-disc-place rail place.
  // App status row (AutoCue 2.0 status sentence — facts are buttons)
  "#app-status",
  "#status-needcues",
  "#status-health",
  // AutoCue 2.0 command palette (⌘K)
  "#cmdk-hint-btn",
  "#cmd-veil",
  "#cmd-palette",
  "#pal-input",
  // Filter controls (Cues tab)
  "#search-input",
  "#phrase-only-cb",
  "#beats-only-cb",
  // Action bar + primary actions
  "#action-bar",
  "#preview-cues-btn",
  "#download-btn",
  // Mini player
  "#mini-waveform-wrap",
  "#mini-waveform",
  // Sticky track filter bar
  "#tracks-sticky",
  // P2 workbench dense-grid column header (in DOM always; hidden until body.wb-active)
  "#wb-grid-head",
  // P5 discover place — rail entry that swaps the centre pane to the feed
  "#wb-disc-place",
  // Library place — rail entry (tab-bar retirement); swaps the centre to the tools
  "#wb-library-place",
  // P3 duplicates place — rail entry + centre-pane view (hidden until opened)
  "#wb-dupes-place",
  "#wb-dupes-pane",
  "#wb-dupes-rescan",
  "#wb-dupes-bulk-delete",
  // P3 restore sheet — A-layer undo off the status sentence (hidden until a delete)
  "#status-restore",
  "#wb-restore-sheet",
  "#wb-restore-go",
  "#wb-restore-dismiss",
  // P4 Nightboard — full-bleed set canvas mode (hidden until opened) + its verb
  "#nb-canvas",
  "#nb-open-btn",
  "#nb-tray-toggle",
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
