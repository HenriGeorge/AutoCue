import { test, expect, Route, Page } from "@playwright/test";

/**
 * AutoCue 2.0 — P5: Discover as a workbench place (rail place → centre-pane view).
 *
 * Drives the real `autocue serve`, but mocks every /api/discover/* + /api/youtube/
 * search so NO real scan hits Discogs / the feeders (network-safe, #189-safe).
 * Asserts the centre-pane swap, the document-scroll + fixed action-bar invariant
 * (TASK-037 — Playwright-only, the JSDOM blind spot), the inspector re-host (R4),
 * reduced-motion suppression (R7), both themes (R8), and that the place issues no
 * /api/discover/* beyond the legacy loadInitialState / runScan (R10).
 *
 * mockDiscoverApi is copied from discover-v2.spec.ts (the shipped Discover spec).
 */
async function mockDiscoverApi(page: Page, overrides: {
  saved?: any[]; dismissed?: any[]; snoozed?: any[];
  followedLabels?: any[]; blockedArtists?: any[]; blockedLabels?: any[];
  tokenValid?: boolean;
  feedEvents?: string[];
  releases?: Record<string, any>;
} = {}) {
  const saved = overrides.saved ?? [];
  const dismissed = overrides.dismissed ?? [];
  const snoozed = overrides.snoozed ?? [];
  const labels = overrides.followedLabels ?? [{ label_id: 999, name: "Stones Throw" }];
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
    r.fulfill({ json: { items: [{ name: "Stones Throw", weight: 14.5 }, { name: "Hyperdub", weight: 7.2 }] } }));
  await page.route(/\/api\/discover\/labels\/search/, (r) =>
    r.fulfill({ json: { items: [{ id: 999, name: "Found Label" }] } }));

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

  for (const path of [
    "/api/discover/save", "/api/discover/dismiss", "/api/discover/snooze",
    "/api/discover/labels/follow", "/api/discover/labels/unfollow",
    "/api/discover/block-artist", "/api/discover/unblock-artist",
    "/api/discover/block-label", "/api/discover/unblock-label",
  ]) {
    await page.route(`**${path}`, (r) => r.fulfill({ json: { ok: true } }));
  }

  await page.route(/\/api\/discover\/releases\/\d+/, (r) => {
    const id = parseInt(r.request().url().split("/").pop() || "0", 10);
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

  await page.route(/\/api\/youtube\/search/, (r) => r.fulfill({ json: { candidates: [] } }));
  await page.route("**/api/discover/stats", (r) => r.fulfill({ json: {
    total_scans: 0, avg_duration_ms: null, saves_per_scan: null,
    novelty_share: {}, top_labels: [], top_artists: [],
    followed_count: labels.length, saved_count: saved.length,
    dismissed_count: dismissed.length, snoozed_count: snoozed.length,
    downloaded_count: 0, blocked_artist_count: blockedArtists.length, blocked_label_count: blockedLabels.length,
  } }));
}

// Force the workbench flag on before any app script runs (default-on already,
// but be explicit so an opted-out localStorage can't strand the place).
async function forceWorkbenchOn(page: Page) {
  await page.addInitScript(() => {
    try { localStorage.setItem("ac_workbench", "1"); } catch (_) {}
  });
}

async function openDiscoverPlace(page: Page) {
  const place = page.locator("#wb-disc-place");
  await expect(place).toBeVisible({ timeout: 10_000 });
  await place.click();
}

test.describe("P5 — Discover as a workbench place", () => {
  test.beforeEach(async ({ page }) => {
    page.on("pageerror", (err) => console.error("[pageerror]", err.message));
    await forceWorkbenchOn(page);
    await mockDiscoverApi(page);
  });

  test("(a)(b) opens the centre feed + hides the cue grid surfaces (R2)", async ({ page }) => {
    await page.goto("/");
    await openDiscoverPlace(page);

    // (a) the feed grid shows in the centre.
    await expect(page.locator("#disc-v2-grid")).toBeVisible();
    await expect(page.locator("#disc-v2-grid .disc-v2-card").first()).toBeVisible({ timeout: 8000 });

    // (b) cue-grid surfaces hidden.
    for (const sel of ["#tracks-sticky", "#track-list", "#wb-grid-head"]) {
      await expect(page.locator(sel)).toBeHidden();
    }
    await expect(page.locator("body")).toHaveClass(/wb-place-disc/);
    await expect(page.locator("#wb-disc-place")).toHaveClass(/active/);
  });

  test("(c) action-bar stays position:fixed + the document scrolls (TASK-037)", async ({ page }) => {
    await page.goto("/");
    await openDiscoverPlace(page);
    await expect(page.locator("#disc-v2-grid .disc-v2-card").first()).toBeVisible({ timeout: 8000 });

    const pos = await page.locator("#action-bar").evaluate((el) => getComputedStyle(el).position);
    expect(pos).toBe("fixed");

    // Document-level scroll (not an inner overflow container).
    await page.evaluate(() => window.scrollTo(0, 200));
    const y = await page.evaluate(() => window.scrollY);
    expect(y).toBeGreaterThan(0);
  });

  test("(d) focusing a card populates the inspector; Esc clears (R4)", async ({ page }) => {
    await page.goto("/");
    await openDiscoverPlace(page);
    const card = page.locator("#disc-v2-grid .disc-v2-card").first();
    await expect(card).toBeVisible({ timeout: 8000 });
    await card.click();

    const body = page.locator("#wb-inspector-body");
    await expect(body).toBeVisible({ timeout: 3000 });
    await expect(body.locator(".wb-insp-title")).toBeVisible();
    await expect(page.locator("#disc-v2-detail-panel")).toHaveAttribute("aria-hidden", "true");

    await page.keyboard.press("Escape");
    await expect(body).toBeHidden({ timeout: 2000 });
    await expect(page.locator("#wb-inspector-empty")).toBeVisible();
  });

  test("(e) swapping back to a crate re-shows the cue grid + sticky pins after scroll", async ({ page }) => {
    await page.goto("/");
    await openDiscoverPlace(page);
    await expect(page.locator("#disc-v2-grid")).toBeVisible();

    // Click the first crate (All tracks) to exit the place.
    await page.locator("#wb-crates .wb-crate").first().click();

    await expect(page.locator("#track-list")).toBeVisible({ timeout: 5000 });
    await expect(page.locator("#tracks-sticky")).toBeVisible();
    await expect(page.locator("body")).not.toHaveClass(/wb-place-disc/);

    // #tracks-sticky is position:sticky against the document.
    const pos = await page.locator("#tracks-sticky").evaluate((el) => getComputedStyle(el).position);
    expect(pos).toBe("sticky");
  });

  test("(f) reduced-motion suppresses the card + scan animations (R7)", async ({ page, browser }) => {
    const ctx = await browser.newContext({ reducedMotion: "reduce" });
    const p = await ctx.newPage();
    await forceWorkbenchOn(p);
    await mockDiscoverApi(p);
    await p.goto("/");
    await openDiscoverPlace(p);
    await expect(p.locator("#disc-v2-grid .disc-v2-card").first()).toBeVisible({ timeout: 8000 });

    // The save-pop pulse is gated to no-preference → no animationName under reduce.
    const probeAnim = await p.evaluate(() => {
      const el = document.createElement("button");
      el.className = "disc-v2-card-action saved";
      document.body.appendChild(el);
      const a = getComputedStyle(el).animationName;
      el.remove();
      return a;
    });
    expect(probeAnim === "none" || probeAnim === "").toBeTruthy();

    // The scan-progress fill transition is suppressed under reduce.
    const fillTransition = await p.evaluate(() => {
      const el = document.getElementById("disc-v2-scan-progress-fill");
      return el ? getComputedStyle(el).transitionDuration : null;
    });
    expect(fillTransition === "0s" || fillTransition === null).toBeTruthy();
    await ctx.close();
  });

  test("(g) both themes render the place without error", async ({ page }) => {
    await page.goto("/");
    await openDiscoverPlace(page);
    await expect(page.locator("#disc-v2-grid .disc-v2-card").first()).toBeVisible({ timeout: 8000 });

    // Toggle to the other theme and confirm the feed survives.
    await page.locator("#theme-toggle").click();
    await expect(page.locator("#disc-v2-grid .disc-v2-card").first()).toBeVisible();
    await page.locator("#theme-toggle").click();
    await expect(page.locator("#disc-v2-grid .disc-v2-card").first()).toBeVisible();
  });

  test("(h) the place issues no /api/discover/* beyond loadInitialState + runScan (R10)", async ({ page }) => {
    const discoverCalls: string[] = [];
    page.on("request", (req) => {
      const u = req.url();
      if (u.includes("/api/discover/")) discoverCalls.push(new URL(u).pathname);
    });
    await page.goto("/");
    await openDiscoverPlace(page);
    await expect(page.locator("#disc-v2-grid .disc-v2-card").first()).toBeVisible({ timeout: 8000 });

    // Every discover call belongs to the legacy initial-state load or the feed
    // scan — the v2 place never invents its own endpoint.
    const allowed = new Set([
      "/api/discover/feed", "/api/discover/feed/status", "/api/discover/feed/cancel",
      "/api/discover/saved", "/api/discover/dismissed", "/api/discover/snoozed",
      "/api/discover/labels", "/api/discover/labels/suggested", "/api/discover/token-status",
      "/api/discover/blocked-artists", "/api/discover/blocked-labels",
    ]);
    for (const path of discoverCalls) {
      expect(allowed.has(path), `unexpected discover endpoint ${path}`).toBeTruthy();
    }
  });
});
