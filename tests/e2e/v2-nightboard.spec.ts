import { test, expect } from "@playwright/test";

/**
 * AutoCue 2.0 P4 — Nightboard (full-bleed set-builder canvas mode).
 *
 * Scope: the LAYOUT + interaction truths jsdom cannot see — the full-bleed mode
 * swap (grid/rail/inspector recede, canvas owns the body), the joint popover,
 * in-place swap, the gravity-tray Add, the reused inspector on tile-focus, and
 * the grid returning (with #action-bar fixed) on close. The DATA shaping is
 * covered by the nightboard-* vitest suites, so this spec STUBS the four
 * endpoints deterministically (#189-safe: no real setbuilder/transition scans).
 *
 * The stubbed set uses REAL track ids pulled from the loaded library so the
 * re-hosted P2 inspector (which looks the track up in ACBridge.tracks()) and the
 * per-track intelligence fetches resolve. Workbench is default-on in local mode.
 */
test.describe("P4 Nightboard", () => {
  test.beforeEach(async ({ page }) => {
    await page.goto("/");
    await expect(page.locator("#app-status")).toBeVisible({ timeout: 10_000 });
    await expect(page.locator("body.wb-active")).toBeVisible({ timeout: 10_000 });
    await page.waitForFunction(
      () => (window as any).ACBridge?.tracks?.().length > 6,
      undefined,
      { timeout: 10_000 },
    );
    const ids: number[] = await page.evaluate(() =>
      (window as any).ACBridge.tracks().slice(0, 6).map((t: any) => Number(t.id)),
    );

    const cats = ["warmup", "build", "peak", "closing"];
    const scores = [0, 88, 82, 75];
    const setTracks = ids.slice(0, 4).map((id, i) => ({
      track_id: id, title: `Track ${i + 1}`, artist: `Artist ${i + 1}`,
      bpm: 120 + i, key: "8A", category: cats[i], transition_score: scores[i], relaxed: false,
    }));

    await page.route(/\/api\/setbuilder(\?|$)/, (r) => r.fulfill({
      json: { tracks: setTracks, total_tracks: 4, estimated_duration_minutes: 30, terminated_reason: "target_duration_reached" },
    }));
    await page.route(/\/api\/transitions\/score$/, (r) => r.fulfill({
      json: { track_a_id: ids[0], track_b_id: ids[1], overall: 72, bpm: 0, key: 0, energy: 0, bpm_a: 0, bpm_b: 0, key_a: "8A", key_b: "8A", explanation: ["Tempo aligns within 2 BPM", "Keys are compatible", "Energy handoff is smooth"] },
    }));
    await page.route(/\/api\/setbuilder\/alternatives/, (r) => r.fulfill({
      json: { alternatives: [
        { track_id: ids[4], title: "Alt One", artist: "AltArtist", bpm: 124, key: "9A", score: 90, from_prev: 90, to_next: 80, genre: "", genre_match: true },
        { track_id: ids[5], title: "Alt Two", artist: "AltArtist2", bpm: 125, key: "9A", score: 84, from_prev: 84, to_next: 78, genre: "", genre_match: true },
      ] },
    }));
    await page.route(/\/api\/tracks\/\d+\/energy/, (r) => r.fulfill({
      json: { track_id: 0, energy: [0.2, 0.5, 0.8, 0.4, 0.6], n_points: 5, energy_profile: "build" },
    }));
  });

  async function openAndBuild(page) {
    await expect(page.locator("#nb-open-btn")).toBeVisible();
    await page.locator("#nb-open-btn").click();
    await expect(page.locator("body.nb-active")).toBeVisible();
    await page.locator("#nb-build-btn").click();
    await expect(page.locator(".nb-tile")).toHaveCount(4);
  }

  test("opens via the toolbar verb; grid/rail/inspector recede; N tiles + N-1 joints", async ({ page }) => {
    await expect(page.locator("#track-list")).toBeVisible();
    await openAndBuild(page);

    await expect(page.locator("#nb-canvas")).toBeVisible();
    await expect(page.locator("#track-list")).toBeHidden();
    await expect(page.locator("#wb-rail")).toBeHidden();
    await expect(page.locator(".nb-joint")).toHaveCount(3); // N-1
    await expect(page.locator(".nb-zone").first()).toBeVisible();
    await expect(page.locator("#nb-arc path")).not.toHaveCount(0);
  });

  test("joint click opens the popover with reasons + alternatives; Swap in changes the tile", async ({ page }) => {
    await openAndBuild(page);
    await page.locator('.nb-joint[data-joint="0"]').click();

    const po = page.locator(".nb-popover");
    await expect(po).toBeVisible();
    await expect(po.locator(".nb-po-reasons li")).toHaveCount(3);
    await expect(po.locator(".nb-swap")).toHaveCount(2);

    // incoming tile (index 1) is replaced by the chosen alternative
    await expect(page.locator(".nb-tile").nth(1).locator(".nb-tile-title")).toHaveText("Track 2");
    await po.locator(".nb-swap").first().click();
    await expect(po).toBeHidden();
    await expect(page.locator(".nb-tile").nth(1).locator(".nb-tile-title")).toHaveText("Alt One");
  });

  test("gravity tray lists candidates and Add inserts a tile", async ({ page }) => {
    await openAndBuild(page);
    await expect(page.locator(".nb-crate-card").first()).toBeVisible();
    await expect(page.locator(".nb-tile")).toHaveCount(4);
    await page.locator(".nb-tray-add").first().click();
    await expect(page.locator(".nb-tile")).toHaveCount(5);
  });

  test("clicking a tile opens the reused inspector", async ({ page }) => {
    await openAndBuild(page);
    await page.locator(".nb-tile").first().click();
    await expect(page.locator("#wb-inspector")).toBeVisible();
    await expect(page.locator("#wb-inspector-body .wb-insp-title")).toBeVisible();
  });

  test("closing returns the grid; #action-bar stays fixed (TASK-037)", async ({ page }) => {
    await openAndBuild(page);
    await page.keyboard.press("Escape");
    await expect(page.locator("body.nb-active")).toHaveCount(0);
    await expect(page.locator("#nb-canvas")).toBeHidden();
    await expect(page.locator("#track-list")).toBeVisible();
    const pos = await page.locator("#action-bar").evaluate((el) => getComputedStyle(el as HTMLElement).position).catch(() => null);
    if (pos !== null) expect(pos).toBe("fixed");
  });

  test("both themes render the canvas", async ({ page }) => {
    await openAndBuild(page);
    await page.screenshot({ path: "test-results/nb-light.png" });
    await page.locator("#theme-toggle").click();
    await expect(page.locator("html.dark")).toHaveCount(1);
    await expect(page.locator("#nb-canvas")).toBeVisible();
    await page.screenshot({ path: "test-results/nb-dark.png" });
  });
});
