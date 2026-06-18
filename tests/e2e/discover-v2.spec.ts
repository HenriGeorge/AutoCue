import { test, expect, Route } from "@playwright/test";

/**
 * Discover v2 e2e (T-039) — drive the UI in a real browser against the live
 * autocue serve, but with the Discover API surface mocked so the test
 * doesn't hit Discogs and doesn't depend on the user's library state.
 *
 * What this covers:
 *   - Discover tab activates without runtime errors
 *   - Onboarding banner shows when no labels are followed
 *   - Mocked SSE feed → cards render
 *   - Clicking a card opens the detail panel
 *   - Escape closes the panel
 *   - `?` opens the keyboard help overlay
 *   - Saving a card flips its action button
 *
 * The test deliberately stubs `/api/discover/*` so it stays green across
 * library shape changes; integration coverage of the real orchestrator is
 * the pytest suite's job (T-014..T-023).
 */

async function activateDiscover(page: import("@playwright/test").Page) {
  // P5: the legacy #tab-discover tab is retired — Discover is now the
  // #wb-disc-place workbench rail place (which calls switchTab('discover') to
  // show #discover-tab-content + re-hosts release detail in the inspector).
  const place = page.locator("#wb-disc-place");
  await expect(place).toBeVisible({ timeout: 10_000 });
  await place.click();
}

async function mockDiscoverApi(page: import("@playwright/test").Page, overrides: {
  saved?: any[]; dismissed?: any[]; snoozed?: any[];
  followedLabels?: any[]; blockedArtists?: any[]; blockedLabels?: any[];
  tokenValid?: boolean;
  feedEvents?: string[];        // raw SSE event lines (each one a `event: X\ndata: {…}\n\n` block)
  releases?: Record<string, any>;
} = {}) {
  const saved = overrides.saved ?? [];
  const dismissed = overrides.dismissed ?? [];
  const snoozed = overrides.snoozed ?? [];
  const labels = overrides.followedLabels ?? [];
  const blockedArtists = overrides.blockedArtists ?? [];
  const blockedLabels = overrides.blockedLabels ?? [];
  const tokenValid = overrides.tokenValid ?? true;

  await page.route("**/api/discover/saved", (r) => r.fulfill({ json: { items: saved } }));
  await page.route("**/api/discover/dismissed", (r) => r.fulfill({ json: { items: dismissed } }));
  await page.route(/\/api\/discover\/snoozed/, (r) => r.fulfill({ json: { items: snoozed } }));
  await page.route("**/api/discover/labels", (r) => r.fulfill({ json: { items: labels } }));
  await page.route("**/api/discover/blocked-artists", (r) => r.fulfill({ json: { items: blockedArtists } }));
  await page.route("**/api/discover/blocked-labels", (r) => r.fulfill({ json: { items: blockedLabels } }));
  await page.route("**/api/discover/token-status", (r) => r.fulfill({ json: { valid: tokenValid } }));
  await page.route(/\/api\/discover\/labels\/suggested/, (r) =>
    r.fulfill({ json: { items: [
      { name: "Stones Throw", weight: 14.5 },
      { name: "Hyperdub", weight: 7.2 },
    ] } }));
  await page.route(/\/api\/discover\/labels\/search/, (r) =>
    r.fulfill({ json: { items: [{ id: 999, name: "Found Label" }] } }));

  // Feed SSE — stream a deterministic sequence of events.
  const defaultEvents = overrides.feedEvents ?? [
    'event: progress\ndata: {"feeder":"artist"}\n\n',
    'event: release\ndata: {"release_key":"rk1","source":"artist","release":{"id":101,"title":"Madvillainy","artist":"Madvillain","label":"Stones Throw","year":2004,"thumb":"","cover_image":""}}\n\n',
    'event: release\ndata: {"release_key":"rk2","source":"label","release":{"id":102,"title":"Donuts","artist":"J Dilla","label":"Stones Throw","year":2006,"thumb":"","cover_image":""}}\n\n',
    'event: done\ndata: {"releases_surfaced":2,"releases_seen":2,"duration_ms":1200}\n\n',
  ];
  await page.route(/\/api\/discover\/feed(\?|$)/, async (route: Route) => {
    await route.fulfill({
      status: 200,
      headers: { "Content-Type": "text/event-stream", "Cache-Control": "no-cache" },
      body: defaultEvents.join(""),
    });
  });

  // Save / dismiss / snooze / follow → simple OK acks.
  for (const path of [
    "/api/discover/save", "/api/discover/dismiss", "/api/discover/snooze",
    "/api/discover/labels/follow", "/api/discover/labels/unfollow",
    "/api/discover/block-artist", "/api/discover/unblock-artist",
    "/api/discover/block-label", "/api/discover/unblock-label",
  ]) {
    await page.route(`**${path}`, (r) => r.fulfill({ json: { ok: true } }));
  }

  // Detail load.
  await page.route(/\/api\/discover\/releases\/\d+/, (r) => {
    const url = r.request().url();
    const id = parseInt(url.split("/").pop() || "0", 10);
    const overrideDetail = overrides.releases?.[String(id)];
    r.fulfill({ json: overrideDetail ?? {
      id, title: `Detail ${id}`, artist: `Artist ${id}`,
      label: "Stones Throw", label_id: 999, year: 2020,
      cover: "", styles: ["Hip Hop"], tracklist: [
        { position: "A1", title: "Track One", duration: "5:30" },
        { position: "A2", title: "Track Two", duration: "4:15" },
      ],
    } });
  });

  // YouTube preview — empty so the carousel renders its placeholder.
  await page.route(/\/api\/youtube\/search/, (r) => r.fulfill({ json: { candidates: [] } }));

  // Stats.
  await page.route("**/api/discover/stats", (r) => r.fulfill({ json: {
    total_scans: 0, avg_duration_ms: null, saves_per_scan: null,
    novelty_share: {}, top_labels: [], top_artists: [],
    followed_count: labels.length, saved_count: saved.length,
    dismissed_count: dismissed.length, snoozed_count: snoozed.length,
    downloaded_count: 0,
    blocked_artist_count: blockedArtists.length,
    blocked_label_count: blockedLabels.length,
  } }));
}


test.describe("Discover v2", () => {
  test.beforeEach(async ({ page }) => {
    // Surface uncaught errors so a regression in the DiscoverV2 init bubbles up.
    page.on("pageerror", (err) => {
      // eslint-disable-next-line no-console
      console.error("[pageerror]", err.message);
    });
  });

  test("onboarding banner appears when no labels are followed", async ({ page }) => {
    await mockDiscoverApi(page, { followedLabels: [] });
    await page.goto("/");
    await activateDiscover(page);

    const banner = page.locator("#disc-v2-onboarding-banner");
    await expect(banner).toBeVisible({ timeout: 5000 });
    await expect(banner).toContainText(/Pick labels|library/i);

    // The auto-load fires suggested labels — chips should appear within ~3s.
    await expect(banner.locator("button").first()).toBeVisible({ timeout: 5000 });
  });

  test("mocked feed renders cards on Refresh", async ({ page }) => {
    await mockDiscoverApi(page, {
      followedLabels: [{ label_id: 999, name: "Stones Throw" }],
    });
    await page.goto("/");
    await activateDiscover(page);

    // Auto-scan fires once labels are present + token valid.
    const grid = page.locator("#disc-v2-grid");
    await expect(grid.locator(".disc-v2-card")).toHaveCount(2, { timeout: 8000 });
    await expect(grid.getByText("Madvillainy")).toBeVisible();
    await expect(grid.getByText("Donuts")).toBeVisible();
  });

  test("focusing a card re-hosts the release detail in the inspector; Escape clears it", async ({ page }) => {
    // P5: inside the workbench place, the legacy slide-in is suppressed — the
    // release detail re-hosts in the right inspector (#wb-inspector-body).
    await mockDiscoverApi(page, {
      followedLabels: [{ label_id: 999, name: "Stones Throw" }],
    });
    await page.goto("/");
    await activateDiscover(page);

    const grid = page.locator("#disc-v2-grid");
    await expect(grid.locator(".disc-v2-card").first()).toBeVisible({ timeout: 8000 });
    await grid.locator(".disc-v2-card").first().click();

    const inspectorBody = page.locator("#wb-inspector-body");
    await expect(inspectorBody).toBeVisible({ timeout: 3000 });
    await expect(inspectorBody.locator(".wb-insp-title")).toBeVisible();
    // The legacy slide-in stays suppressed.
    await expect(page.locator("#disc-v2-detail-panel")).toHaveAttribute("aria-hidden", "true");

    // Tracklist loads from the mocked /releases/{id} into the re-hosted detail.
    await expect(inspectorBody.locator(".disc-v2-detail-tracklist li")).toHaveCount(2, { timeout: 3000 });

    // Escape clears the focused release back to the empty inspector state.
    await page.keyboard.press("Escape");
    await expect(inspectorBody).toBeHidden({ timeout: 2000 });
    await expect(page.locator("#wb-inspector-empty")).toBeVisible();
  });

  test("`?` opens the keyboard help overlay", async ({ page }) => {
    await mockDiscoverApi(page, {
      followedLabels: [{ label_id: 999, name: "Stones Throw" }],
    });
    await page.goto("/");
    await activateDiscover(page);

    // Wait until the grid is populated, otherwise `?` may dispatch before
    // the keyboard handler is fully wired.
    await expect(page.locator("#disc-v2-grid .disc-v2-card").first()).toBeVisible({ timeout: 8000 });

    await page.keyboard.press("?");
    const help = page.locator("#disc-v2-kbd-help");
    await expect(help).toHaveAttribute("aria-hidden", "false", { timeout: 2000 });
    await expect(help).toContainText(/Save/);
    await expect(help).toContainText(/Snooze/);
  });

  test("Save action flips the card's save button to ✓", async ({ page }) => {
    await mockDiscoverApi(page, {
      followedLabels: [{ label_id: 999, name: "Stones Throw" }],
    });
    await page.goto("/");
    await activateDiscover(page);

    const firstCard = page.locator("#disc-v2-grid .disc-v2-card").first();
    await expect(firstCard).toBeVisible({ timeout: 8000 });

    // P5 redesign: the action pills are always visible (no longer hover-revealed)
    // and the Save pill carries a text label. Click Save.
    const saveBtn = firstCard.locator('[data-act="save"]');
    await expect(saveBtn).toBeVisible();
    await expect(saveBtn).toHaveText("Save");
    await saveBtn.click();

    // The card renderer redraws on notify(); the save pill should now read
    // "Saved ✓" (the saved state — green fill + check).
    await expect(saveBtn).toHaveText("Saved ✓", { timeout: 3000 });
  });
});
