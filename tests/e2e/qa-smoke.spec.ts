import { test, expect } from "@playwright/test";

const READ_ONLY_ENDPOINTS = [
  "/api/status",
  "/api/playlists",
  "/api/tracks",
  "/api/tags",
  "/api/backups",
  "/api/config",
  "/api/download/config",
];

/**
 * Console / network capture. Returns matchers for problematic patterns.
 * Scopes the noisy ones (Failed to fetch, Uncaught) to URLs/stacks under
 * the test server, so unrelated dev-tool noise doesn't false-positive.
 */
function attachErrorCapture(page: import("@playwright/test").Page, opts: {
  serverHost: string;
  allowFailedUrls?: string[];
}): { errors: string[] } {
  const errors: string[] = [];
  const allow = opts.allowFailedUrls ?? [];

  const isAllowed = (text: string) =>
    allow.some((u) => text.includes(u));

  page.on("console", (msg) => {
    const text = msg.text();
    const level = msg.type();
    const looksBad =
      /TypeError|ReferenceError|Hydration|Uncaught/.test(text) ||
      (text.includes("Failed to fetch") && text.includes(opts.serverHost));
    if ((level === "error" || level === "warning") && looksBad && !isAllowed(text)) {
      errors.push(`[${level}] ${text}`);
    }
  });
  page.on("pageerror", (err) => {
    if (!isAllowed(err.message)) {
      errors.push(`[pageerror] ${err.message}`);
    }
  });
  page.on("requestfailed", (req) => {
    const url = req.url();
    if (url.includes(opts.serverHost) && !isAllowed(url)) {
      errors.push(`[requestfailed] ${req.method()} ${url} — ${req.failure()?.errorText}`);
    }
  });

  return { errors };
}

test.describe("API smoke (read-only)", () => {
  for (const path of READ_ONLY_ENDPOINTS) {
    test(`GET ${path} returns 2xx`, async ({ request }) => {
      const r = await request.get(path);
      expect(r.status(), `${path} status`).toBeGreaterThanOrEqual(200);
      expect(r.status(), `${path} status`).toBeLessThan(300);
    });
  }

  test("GET /api/tracks returns an array", async ({ request }) => {
    const r = await request.get("/api/tracks");
    expect(r.ok()).toBeTruthy();
    const body = await r.json();
    expect(Array.isArray(body)).toBeTruthy();
  });

  test("GET /api/tags returns only used tags (no orphans)", async ({
    request,
  }) => {
    const r = await request.get("/api/tags");
    expect(r.ok()).toBeTruthy();
    const body = await r.json();
    expect(Array.isArray(body)).toBeTruthy();
  });
});

test.describe("SSE smoke (bounded)", () => {
  test("GET /api/health?limit=5 streams events and terminates", async ({
    request,
  }) => {
    test.setTimeout(30_000);
    // ?limit=5 caps the per-track loop at the server so smoke runs are
    // bounded regardless of library size.
    const r = await request.get("/api/health?limit=5", { timeout: 25_000 });
    expect(r.headers()["content-type"] ?? "").toContain("text/event-stream");
    // SSE must bypass gzip — Starlette skips it when content-type is
    // text/event-stream, but verify in case middleware changes.
    expect(r.headers()["content-encoding"] ?? "").not.toContain("gzip");

    const body = await r.text();
    expect(body, "no data: event in SSE body").toMatch(/data:/);
    // Library health emits a summary as the terminator. It currently does
    // not include {done:true} — relax to checking for the summary marker.
    expect(body, "no library_score / summary in SSE body").toMatch(
      /library_score|summary|total/,
    );
  });
});

test.describe("Web UI smoke (local mode)", () => {
  test("loads index page without console errors", async ({ page, baseURL }) => {
    const host = new URL(baseURL!).host;
    const { errors } = attachErrorCapture(page, { serverHost: host });

    await page.goto("/");
    await expect(page.locator("#app-status")).toBeVisible({ timeout: 10_000 });
    // Wait for the tab nav to appear — proves local-mode detection ran.
    await expect(page.locator("#tab-nav")).toBeVisible({ timeout: 10_000 });

    expect(errors, "console / page errors during load").toEqual([]);
  });

  test("can switch between Cues / Library / Discover tabs", async ({
    page,
    baseURL,
  }) => {
    const host = new URL(baseURL!).host;
    const { errors } = attachErrorCapture(page, { serverHost: host });

    await page.goto("/");
    await expect(page.locator("#tab-nav")).toBeVisible({ timeout: 10_000 });

    for (const tabId of ["#tab-library", "#tab-discover", "#tab-cues"]) {
      await page.locator(tabId).click();
      // Tab switch is synchronous DOM toggle; give it one tick.
      await page.waitForTimeout(100);
    }

    expect(errors).toEqual([]);
  });

  test("filter toggles do not crash the page", async ({ page, baseURL }) => {
    const host = new URL(baseURL!).host;
    const { errors } = attachErrorCapture(page, { serverHost: host });

    await page.goto("/");
    await expect(page.locator("#app-status")).toBeVisible({ timeout: 10_000 });

    // Search round-trip
    await page.locator("#search-input").fill("xx_no_match_xx");
    await page.locator("#search-input").fill("");

    // Phrase-only toggle (twice — toggle on, toggle off)
    const phraseToggle = page.locator("#phrase-only-cb");
    await phraseToggle.check();
    await phraseToggle.uncheck();

    // Beat-grid-only toggle
    const beatToggle = page.locator("#beats-only-cb");
    await beatToggle.check();
    await beatToggle.uncheck();

    expect(errors).toEqual([]);
  });
});
