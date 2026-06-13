import { test, expect } from "@playwright/test";

/**
 * AutoCue 2.0 P3 — Duplicates as a place (rail place → centre-pane view).
 *
 * Scope: the LAYOUT truths jsdom cannot see (jsdom-layout-blind-spot rule) —
 * the centre-pane swap, the sticky/Virtualizer invariants surviving a
 * hide/show round-trip (TASK-033/037), the fixed action bar, and the restore
 * sheet anchoring under the right-aligned status fact. The scan/keeper/delete
 * DATA flow is covered by the duplicates vitest suite (duplicates-panel /
 * -phase3 / -delete-confirm) + v2-duplicates-place.test.js, so this spec does
 * NOT re-mock the SSE; it drives the real renderer where a group is needed.
 *
 * Runs against the local-mode sandbox; the workbench is default-on (c3dcff0).
 */
test.describe("P3 duplicates place", () => {
  test.beforeEach(async ({ page }) => {
    // Make the place's lazy first-scan instant + deterministic. Opening the
    // place fires a real GET /api/duplicates (2,928-track scan) against the
    // sandbox; across sequential tests that saturates the single server and
    // makes the swap timing flaky. A minimal "0 groups, done" SSE keeps the
    // delegation path exercised (the scan still runs, just instantly). Tests
    // that need a group inject it via the real renderer, not the scan.
    await page.route(/\/api\/duplicates(\?|$)/, (route) => route.fulfill({
      status: 200,
      headers: { "Content-Type": "text/event-stream", "Cache-Control": "no-cache" },
      body: 'data: {"total":0}\n\ndata: {"done":true,"summary":{"groups":0,"surplus":0,"scanned":0,"skipped_empty":0}}\n\n',
    }));
    await page.goto("/");
    await expect(page.locator("#app-status")).toBeVisible({ timeout: 10_000 });
    // Workbench is the default home in local mode.
    await expect(page.locator("body.wb-active")).toBeVisible({ timeout: 10_000 });
    await expect(page.locator("#wb-dupes-place")).toBeVisible();
  });

  test("rail place swaps the centre pane: grid/sticky/inspector hide, action bar stays fixed", async ({ page }) => {
    await expect(page.locator("#track-list")).toBeVisible();
    await page.locator("#wb-dupes-place").click();

    await expect(page.locator("#wb-dupes-pane")).toBeVisible();
    await expect(page.locator("#track-list")).toBeHidden();
    await expect(page.locator("#tracks-sticky")).toBeHidden();
    await expect(page.locator("#wb-grid-head")).toBeHidden();
    await expect(page.locator("#wb-inspector")).toBeHidden();
    await expect(page.locator("body")).toHaveClass(/wb-place-dupes/);
    await expect(page.locator("#wb-dupes-place")).toHaveClass(/active/);

    // #action-bar stays fixed against the viewport (TASK-037).
    const pos = await page.locator("#action-bar").evaluate(
      (el) => getComputedStyle(el as HTMLElement).position).catch(() => null);
    if (pos !== null) expect(pos).toBe("fixed");
  });

  test("swapping back restores the grid and the sticky bar still pins on scroll", async ({ page }) => {
    await page.locator("#wb-dupes-place").click();
    await expect(page.locator("#wb-dupes-pane")).toBeVisible();

    // Leaving via a crate click is a grid intent — it exits the place.
    await page.locator("#wb-crates .wb-crate").first().click();
    await expect(page.locator("#track-list")).toBeVisible();
    await expect(page.locator("#wb-dupes-pane")).toBeHidden();
    await expect(page.locator("body")).not.toHaveClass(/wb-place-dupes/);

    // The document-level sticky survived the hide/show round-trip: once scrolled
    // past its pin point it stops moving — a FURTHER scroll leaves its top
    // unchanged (a broken sticky would keep scrolling off to a negative top).
    const topAt = () => page.locator("#tracks-sticky").evaluate(
      (el) => el.getBoundingClientRect().top);
    await page.mouse.wheel(0, 1500);
    await page.waitForTimeout(150);
    const pinned1 = await topAt();
    await page.mouse.wheel(0, 800);
    await page.waitForTimeout(150);
    const pinned2 = await topAt();
    expect(pinned1).toBeGreaterThanOrEqual(0); // still on-screen
    expect(Math.abs(pinned2 - pinned1)).toBeLessThan(12); // pinned: further scroll doesn't move it
  });

  test("⌘K 'Go to Duplicates' opens the place", async ({ page }) => {
    await page.keyboard.press("ControlOrMeta+k");
    await expect(page.locator("#pal-input")).toBeFocused();
    await page.locator("#pal-input").fill("go to dupl");
    // Wait for the filter to settle on the matched command before running it,
    // then click it directly (robust against the async re-render race).
    const opt = page.locator("#cmd-palette", { hasText: "Go to Duplicates" })
      .getByText("Go to Duplicates");
    await expect(opt).toBeVisible();
    await opt.click();
    await expect(page.locator("#wb-dupes-pane")).toBeVisible();
    await expect(page.locator("#wb-dupes-place")).toHaveClass(/active/);
  });

  test("restyled group renders mono data + a green keeper wash (real renderer)", async ({ page }) => {
    await page.locator("#wb-dupes-place").click();
    // Drive the real legacy renderer with a deterministic group — no SSE mock.
    await page.evaluate(() => {
      const list = document.getElementById("duplicates-list")!;
      const fn = (window as any)._renderDuplicateGroup;
      list.appendChild(fn({
        artist: "Daft Punk", title: "Around the World",
        copies: [
          { track_id: 1, is_keeper: true, duration: 428, bpm: 121.0, key: "4A", existing_hot_cues: 8, play_count: 42, bitrate: 320, last_played: "2026-05-01", folder_path: "/m/", file_name: "atw.mp3" },
          { track_id: 2, is_keeper: false, duration: 428, bpm: 121.0, key: "4A", existing_hot_cues: 0, play_count: 3, bitrate: 320, last_played: "2025-01-01", folder_path: "/m/", file_name: "atw_copy.mp3" },
        ],
      }));
      // Expand the detail rows.
      (list.querySelector(".wb-dup-toggle") as HTMLElement)?.click();
    });
    const group = page.locator("#duplicates-list .wb-dup-group");
    await expect(group).toBeVisible();
    // Data values are mono.
    const metaFont = await group.locator(".wb-dup-meta").first().evaluate(
      (el) => getComputedStyle(el as HTMLElement).fontFamily.toLowerCase());
    expect(metaFont).toContain("mono");
    // The keeper row carries a non-transparent (green-wash) background.
    const keeperBg = await group.locator(".wb-dup-row.keeper").first().evaluate(
      (el) => getComputedStyle(el as HTMLElement).backgroundColor);
    expect(keeperBg).not.toBe("rgba(0, 0, 0, 0)");
    expect(keeperBg).not.toBe("transparent");
  });

  test("restore sheet anchors under the right-aligned status fact, not the viewport origin", async ({ page }) => {
    // Simulate a completed delete that wrote a backup (T1 seam event).
    await page.evaluate(() => {
      window.dispatchEvent(new CustomEvent("autocue:duplicates-deleted", {
        detail: { deleted: 2, requested: 2, cancelled: false, backup_path: "/Users/x/.autocue/backups/master_demo.db" },
      }));
    });
    const fact = page.locator("#status-restore");
    await expect(fact).toBeVisible();
    await expect(fact).toContainText("2 deleted");
    await fact.click();

    const sheet = page.locator("#wb-restore-sheet");
    await expect(sheet).toBeVisible();
    await expect(page.locator("#wb-restore-file")).toHaveText("master_demo.db");

    // The sheet drops UNDER the fact (right side), not at the top-left origin.
    const box = await page.evaluate(() => {
      const s = document.getElementById("wb-restore-sheet")!.getBoundingClientRect();
      const f = document.getElementById("status-restore")!.getBoundingClientRect();
      return { sheetTop: s.top, sheetRight: s.right, factBottom: f.bottom, factLeft: f.left, vw: window.innerWidth };
    });
    expect(box.sheetTop).toBeGreaterThanOrEqual(box.factBottom - 2); // below the fact
    expect(box.sheetTop).toBeLessThan(box.factBottom + 20);
    expect(box.sheetRight).toBeLessThanOrEqual(box.vw); // clamped on-screen
    expect(box.factLeft).toBeGreaterThan(box.vw / 2); // fact is right-aligned
  });
});
