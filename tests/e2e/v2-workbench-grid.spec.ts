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

  test("(d) phrase-mode lazy load: _updateTrackCardCues keeps 46px row height (TASK-033)", async ({ page }) => {
    /**
     * Regression spec for the phrase-mode grid overlap bug (pre-existing on main):
     * _updateTrackCardCues() used buildTrackCard (160px) unconditionally when phrase
     * cues lazy-landed, violating the Virtualizer's fixed-height invariant (TASK-033).
     * After lazy load, EVERY visible .track-card must still be 46px = WB_ROW_H and
     * consecutive translateY steps must be exactly 46px (no slot overlap).
     *
     * Fails before the fix (160px cards appear); passes after the fix (always buildWbRow
     * in wb-active mode, mirroring the main render dispatch at lines 1735-1747).
     */
    test.setTimeout(60_000);

    // Mock /api/generate → instant phrase data for any track IDs so the test
    // doesn't depend on the sandbox DB having phrase-analysed tracks.
    const MOCK_CUES = [
      { position_ms:     0, label: "Intro", slot: 0, name: "", confidence: 1, phrase_bars: 16 },
      { position_ms:  8000, label: "Build", slot: 1, name: "", confidence: 1, phrase_bars: 16 },
      { position_ms: 16000, label: "Drop",  slot: 2, name: "", confidence: 1, phrase_bars: 16 },
      { position_ms: 24000, label: "Outro", slot: 3, name: "", confidence: 1, phrase_bars: 16 },
    ];
    await page.route(/\/api\/generate(\?|$)/, async (r) => {
      let ids: number[] = [];
      try { ids = r.request().postDataJSON()?.track_ids || []; } catch (_) {}
      await r.fulfill({ json: { tracks: ids.map((id) => ({ id, mode_used: "phrase", cues: MOCK_CUES })) } });
    });

    // Wait for the initial wb-rows to be visible
    const rows = page.locator("#track-list .wb-row");
    const n = await rows.count();
    test.skip(n === 0, "sandbox library is empty");

    // Force tracks' hasPhrase=true so _collectPhraseLazyIds queues them.
    // The real /api/tracks field has_phrase reflects ANLZ data; we patch in-memory
    // so the lazy loader sees them as phrase-ready regardless of actual analysis.
    await page.evaluate(() => {
      const AC = (window as any).ACBridge;
      if (!AC?.tracks) return;
      // Patch the in-memory parsedTracks so hasPhrase=true for visible cards.
      const cards = Array.from(
        document.querySelectorAll("#track-list .track-card[data-track-id]")
      ) as HTMLElement[];
      const visibleIds = new Set(cards.map(c => String(c.dataset.trackId)));
      const tracks: any[] = AC.tracks();
      for (const t of tracks) {
        if (visibleIds.has(String(t.id))) t.hasPhrase = true;
      }
    });

    // Enable phrase analysis mode — the lazy loader will then queue visible tracks.
    await page.evaluate(() =>
      (document.querySelector("#mode-phrase-btn") as HTMLElement | null)?.click()
    );

    // Immediately flush the lazy queue (bypasses the 120ms debounce).
    await page.evaluate(() => {
      try { (window as any)._flushPhraseLazyQueue?.(); } catch (_) {}
    });

    // Wait until at least one track has phrase state populated (mock responded).
    await page.waitForFunction(() => {
      const AC = (window as any).ACBridge;
      if (!AC?.phraseState) return false;
      const cards = Array.from(
        document.querySelectorAll("#track-list .track-card[data-track-id]")
      ) as HTMLElement[];
      return cards.some(c => {
        const s = AC.phraseState(c.dataset.trackId!);
        return Array.isArray(s) && s.length > 0;
      });
    }, undefined, { timeout: 15_000 });

    // Give _updateTrackCardCues a render tick to complete its DOM replacement.
    await page.waitForTimeout(200);

    // ── ASSERTIONS ──────────────────────────────────────────────────────────────
    // Every visible .track-card must be 46px (WB_ROW_H).
    // Before the fix: rebuilt cards are 160px (buildTrackCard); after: 46px (buildWbRow).
    const cards = page.locator("#track-list .track-card");
    const cardCount = await cards.count();
    expect(cardCount, "need at least 1 card to measure").toBeGreaterThan(0);

    const heights = await cards.evaluateAll((els) =>
      (els as HTMLElement[]).map((el) => ({
        id: el.dataset.trackId || "?",
        h: Math.round(el.getBoundingClientRect().height),
      }))
    );

    for (const { id, h } of heights) {
      expect(
        h,
        `track-card [data-track-id="${id}"] height is ${h}px — must be WB_ROW_H=46px (TASK-033 violated by phrase lazy-load)`
      ).toBe(46);
    }

    // Consecutive translateY steps must be exactly 46px (no slot overlap or gap).
    const transforms = await cards.evaluateAll((els) =>
      (els as HTMLElement[]).map((el) => {
        const m = /translateY\(\s*([0-9.]+)px\s*\)/.exec(el.style.transform || "");
        return m ? Number(m[1]) : null;
      })
    );
    const ys = (transforms.filter((y) => y !== null) as number[]).sort((a, b) => a - b);
    for (let i = 1; i < ys.length; i++) {
      const delta = Math.round(ys[i] - ys[i - 1]);
      expect(
        delta,
        `consecutive card translateY delta ${delta}px — must be 46px (slot overlap if <46, gap if >46)`
      ).toBe(46);
    }
  });
});
