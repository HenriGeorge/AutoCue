import { test, expect, Route, Page } from "@playwright/test";

/**
 * AutoCue 2.0 — Unit A: inspector "anchor-transition card" (DESIGN.md UNIT A).
 *
 * A new `_section('Transition in')` block inside the workbench inspector
 * (mode 'track' only) that scores the transition anchor → focused track and
 * shows the band + reasons. Reuses POST /api/transitions/score; zero backend
 * change. Coverage map: crew/test-designer.md (Unit A state machine).
 *
 * Scope here = the LAYOUT + state truths jsdom cannot see (band → colour token,
 * mono data, "from <anchor>" copy, hidden states, release-mode suppression,
 * rapid-selection settle). The DATA-shaping / state-guard logic is covered by
 * the vitest unit (tests/web/v2-inspector-anchor.test.js); this spec drives the
 * REAL renderer in local mode.
 *
 * Two deterministic seams keep this #189-safe (no real master.db scoring):
 *   1. POST /api/transitions/score is route-MOCKED with a controllable `overall`
 *      + `explanation[]` (the `ctl` holder) — bands are deterministic.
 *   2. The ANCHOR is controlled via the sanctioned read-only bridge accessor
 *      `window.ACBridge.nowPlayingId()` (DESIGN: the one legacy edit in Unit A).
 *      Overriding it in-page is the same seam the inspector reads, so we drive
 *      no-anchor / anchor==self / fallback-to-prev-focus deterministically
 *      WITHOUT needing real audio playback.
 *
 * Workbench is default-on in local mode; we force the flag for an opted-out
 * localStorage. Runs against 127.0.0.1 (config baseURL), never localhost.
 */

// Mutable score response — tests set `ctl.overall` / `ctl.explanation` BEFORE
// the focus that fires the fetch. The route closes over this object.
type Ctl = { overall: number; explanation: string[] };

const BASE_SCORE = {
  bpm: 0, key: 0, energy: 0, bpm_a: 0, bpm_b: 0, key_a: "8A", key_b: "8A",
  end_energy_a: 0, start_energy_b: 0,
};

async function forceWorkbenchOn(page: Page) {
  await page.addInitScript(() => {
    try { localStorage.setItem("ac_workbench", "1"); } catch (_) {}
  });
}

async function bootWorkbench(page: Page) {
  await page.goto("/");
  await expect(page.locator("#app-status")).toBeVisible({ timeout: 10_000 });
  await expect(page.locator("body.wb-active")).toBeVisible({ timeout: 15_000 });
  // Wait for the library + the bridge to be live (the inspector resolves the
  // anchor + focused track through ACBridge.tracks()).
  await page.waitForFunction(
    () => (window as any).ACBridge?.tracks?.().length > 4,
    undefined,
    { timeout: 20_000 },
  );
  // Rows must have mounted (focusing is a grid-row click).
  await page.locator("#track-list .wb-row").first().waitFor({ state: "attached", timeout: 20_000 });
}

/** Override the anchor accessor in-page (the seam the inspector reads). */
async function setAnchor(page: Page, value: number | null) {
  await page.evaluate((v) => {
    const b = (window as any).ACBridge;
    if (b) b.nowPlayingId = () => v;
  }, value);
}

/** Rows clear of the sticky bar, with their track ids + click coordinates. */
async function clearRows(page: Page): Promise<Array<{ id: string; x: number; y: number }>> {
  const stickyBottom = await page
    .locator("#tracks-sticky")
    .evaluate((el) => el.getBoundingClientRect().bottom)
    .catch(() => 0);
  return await page.locator("#track-list .wb-row").evaluateAll((els, sb) => {
    const out: Array<{ id: string; x: number; y: number }> = [];
    for (const el of els) {
      const id = (el as HTMLElement).getAttribute("data-track-id");
      const cell = el.querySelector(".wb-c-title") as HTMLElement | null;
      if (!id || !cell) continue;
      const r = cell.getBoundingClientRect();
      if (r.top > (sb as number) + 8 && r.width > 0 && r.height > 0) {
        out.push({ id, x: r.left + r.width / 2, y: r.top + r.height / 2 });
      }
    }
    return out;
  }, stickyBottom);
}

async function nameOf(page: Page, id: string): Promise<string> {
  return await page.evaluate((tid) => {
    const t = ((window as any).ACBridge?.tracks?.() || []).find(
      (x: any) => String(x.id) === String(tid),
    );
    return t ? String(t.name || "") : "";
  }, id);
}

/** Locator for the "Transition in" section header inside the inspector. */
function transitionHeader(page: Page) {
  return page
    .locator("#wb-inspector-body .wb-insp-h")
    .filter({ hasText: "Transition in" });
}

/** Probe the score node: its mono-ness + computed colour channels. */
async function scoreProbe(page: Page, overall: number) {
  return await page.evaluate((score) => {
    const secs = [...document.querySelectorAll("#wb-inspector-body .wb-insp-section")];
    const sec = secs.find((s) => {
      const h = s.querySelector(".wb-insp-h");
      return h && (h.textContent || "").trim() === "Transition in";
    });
    if (!sec) return { present: false } as const;
    const leaves = [...sec.querySelectorAll("*")].filter((n) => n.children.length === 0);
    const scoreEl = leaves.find((n) => (n.textContent || "").trim() === String(score));
    let probe: { font: string; r: number; g: number; b: number } | null = null;
    if (scoreEl) {
      const cs = getComputedStyle(scoreEl as HTMLElement);
      const m = (cs.color.match(/\d+/g) || []).map(Number);
      probe = { font: cs.fontFamily.toLowerCase(), r: m[0], g: m[1], b: m[2] };
    }
    return { present: true, text: sec.textContent || "", score: probe } as const;
  }, overall);
}

const isGreenDominant = (p: { r: number; g: number; b: number }) =>
  p.g > p.r + 40 && p.g > p.b + 40;

test.describe("Unit A — inspector anchor-transition card", () => {
  let ctl: Ctl;

  test.beforeEach(async ({ page }) => {
    page.on("pageerror", (err) => console.error("[pageerror]", err.message));
    ctl = { overall: 92, explanation: ["Keys 8A→9A are compatible", "BPM 124→126 within range", "Energy handoff is smooth"] };
    await forceWorkbenchOn(page);
    // The score endpoint is mocked — deterministic bands, never a real scan.
    await page.route(/\/api\/transitions\/score$/, (r: Route) =>
      r.fulfill({ json: { ...BASE_SCORE, overall: ctl.overall, explanation: ctl.explanation } }),
    );
    await bootWorkbench(page);
  });

  // ── A-1: no anchor (nothing playing, no prior focus) → section absent ──
  test("A-1 no anchor & no prior focus → no Transition card", async ({ page }) => {
    await setAnchor(page, null);
    const rows = await clearRows(page);
    test.skip(rows.length < 1, "no clear rows in sandbox");
    await page.mouse.click(rows[0].x, rows[0].y); // first focus → no prior anchor
    await expect(page.locator("#wb-inspector-body .wb-insp-title")).toBeVisible();
    await page.waitForTimeout(600); // let any (wrongful) fetch settle
    await expect(transitionHeader(page)).toHaveCount(0);
  });

  // ── A-2: anchor == focused track → don't score a track against itself ──
  test("A-2 anchor == self → no Transition card", async ({ page }) => {
    const rows = await clearRows(page);
    test.skip(rows.length < 1, "no clear rows in sandbox");
    await setAnchor(page, Number(rows[0].id)); // now-playing IS the focused row
    await page.mouse.click(rows[0].x, rows[0].y);
    await expect(page.locator("#wb-inspector-body .wb-insp-title")).toBeVisible();
    await page.waitForTimeout(600);
    await expect(transitionHeader(page)).toHaveCount(0);
  });

  // ── A-3: fallback to previously-focused track as the anchor ──
  test("A-3 fallback to previously-focused → card shows 'from <prev>'", async ({ page }) => {
    await setAnchor(page, null); // nothing playing → fall back to prior focus
    const rows = await clearRows(page);
    test.skip(rows.length < 2, "need two clear rows");
    const anchorName = await nameOf(page, rows[0].id);
    test.skip(!anchorName, "anchor track has no resolvable name");

    await page.mouse.click(rows[0].x, rows[0].y); // focus A (becomes prior focus)
    await expect(page.locator("#wb-inspector-body .wb-insp-title")).toBeVisible();
    await page.mouse.click(rows[1].x, rows[1].y); // focus B → anchor = A

    await expect(transitionHeader(page)).toBeVisible({ timeout: 5_000 });
    const probe = await scoreProbe(page, ctl.overall);
    expect(probe.present).toBe(true);
    // "from <anchor title>" copy — the anchor is the previously-focused track.
    expect(probe.text).toContain("from");
    expect(probe.text).toContain(anchorName.slice(0, Math.min(anchorName.length, 12)));
  });

  // ── A-5: good band (overall ≥ 85) → mono score in the green signal token ──
  test("A-5 good band → mono green score + up to 3 reasons (screenshot light+dark)", async ({ page }) => {
    await setAnchor(page, null);
    ctl.overall = 92;
    ctl.explanation = ["Keys 8A→9A are compatible", "BPM 124→126 within range", "Energy handoff is smooth"];
    const rows = await clearRows(page);
    test.skip(rows.length < 2, "need two clear rows");

    await page.mouse.click(rows[0].x, rows[0].y);
    await expect(page.locator("#wb-inspector-body .wb-insp-title")).toBeVisible();
    await page.mouse.click(rows[1].x, rows[1].y);

    await expect(transitionHeader(page)).toBeVisible({ timeout: 5_000 });
    const probe = await scoreProbe(page, 92);
    expect(probe.present).toBe(true);
    expect(probe.score, "score node `92` not found in the section").not.toBeNull();
    // Data value is mono.
    expect(probe.score!.font).toContain("mono");
    // Green = signal: the good band resolves to the green token family.
    expect(
      isGreenDominant(probe.score!),
      `good band should be green; got rgb(${probe.score!.r},${probe.score!.g},${probe.score!.b})`,
    ).toBe(true);
    // Reasons (≤3) from the API explanation[] are shown.
    for (const reason of ctl.explanation) {
      expect(probe.text).toContain(reason.slice(0, 10));
    }

    await page.screenshot({ path: "test-results/anchor-good-light.png" });
    await page.locator("#theme-toggle").click();
    await expect(page.locator("html.dark")).toHaveCount(1);
    await expect(transitionHeader(page)).toBeVisible();
    await page.screenshot({ path: "test-results/anchor-good-dark.png" });
  });

  // ── A-6: ok band (70 ≤ overall < 85) → NOT green (amber/warn) ──
  test("A-6 ok band → score is NOT the green signal token", async ({ page }) => {
    await setAnchor(page, null);
    ctl.overall = 78;
    ctl.explanation = ["Tempo within range", "Keys are adjacent"];
    const rows = await clearRows(page);
    test.skip(rows.length < 2, "need two clear rows");

    await page.mouse.click(rows[0].x, rows[0].y);
    await expect(page.locator("#wb-inspector-body .wb-insp-title")).toBeVisible();
    await page.mouse.click(rows[1].x, rows[1].y);

    await expect(transitionHeader(page)).toBeVisible({ timeout: 5_000 });
    const probe = await scoreProbe(page, 78);
    expect(probe.score, "score node `78` not found").not.toBeNull();
    expect(probe.score!.font).toContain("mono");
    expect(
      isGreenDominant(probe.score!),
      "ok band must NOT use the green signal token",
    ).toBe(false);
    await page.screenshot({ path: "test-results/anchor-ok-light.png" });
  });

  // ── A-7: weak band (overall < 70) → muted, NOT green/amber ──
  test("A-7 weak band → muted score, NOT green", async ({ page }) => {
    await setAnchor(page, null);
    ctl.overall = 54;
    ctl.explanation = ["Tempo gap is wide"];
    const rows = await clearRows(page);
    test.skip(rows.length < 2, "need two clear rows");

    await page.mouse.click(rows[0].x, rows[0].y);
    await expect(page.locator("#wb-inspector-body .wb-insp-title")).toBeVisible();
    await page.mouse.click(rows[1].x, rows[1].y);

    await expect(transitionHeader(page)).toBeVisible({ timeout: 5_000 });
    const probe = await scoreProbe(page, 54);
    expect(probe.score, "score node `54` not found").not.toBeNull();
    expect(probe.score!.font).toContain("mono");
    expect(
      isGreenDominant(probe.score!),
      "weak band must NOT use the green signal token",
    ).toBe(false);
    await page.screenshot({ path: "test-results/anchor-weak-light.png" });
  });

  // ── A-11: rapid A→B→C selection → only the LAST focus's card is shown ──
  test("A-11 rapid A→B→C selection → exactly one card, for the last focus", async ({ page }) => {
    await setAnchor(page, null);
    ctl.overall = 88;
    ctl.explanation = ["Compatible keys"];
    const rows = await clearRows(page);
    test.skip(rows.length < 3, "need three clear rows");
    const bName = await nameOf(page, rows[1].id); // C's anchor = previously-focused (B)

    // Click three rows back-to-back with no settle between — exercises the
    // stale-ignore focus-token guard.
    await page.mouse.click(rows[0].x, rows[0].y);
    await page.mouse.click(rows[1].x, rows[1].y);
    await page.mouse.click(rows[2].x, rows[2].y);

    await expect(transitionHeader(page)).toBeVisible({ timeout: 5_000 });
    // Exactly one Transition section — no stacked/leftover cards.
    await expect(transitionHeader(page)).toHaveCount(1);
    const probe = await scoreProbe(page, 88);
    expect(probe.present).toBe(true);
    // The surviving card anchors to B (C's previously-focused), not A.
    if (bName) {
      expect(probe.text).toContain(bName.slice(0, Math.min(bName.length, 12)));
    }
  });
});

// ── A-10: release-mode (Discover detail) must NOT render the anchor card ──
//
// Re-uses the Discover place re-host: clicking a feed card drives
// renderReleaseInspector (mode 'release'). The anchor card is mode-'track' only.
test.describe("Unit A — A-10 release mode suppresses the anchor card", () => {
  // Comprehensive mock mirroring v2-discover-shell.spec.ts (the proven-green
  // pattern) so the feed reliably renders a card to click. Network-safe (#189).
  async function mockDiscover(page: Page) {
    await page.route("**/api/discover/saved", (r) => r.fulfill({ json: { items: [] } }));
    await page.route("**/api/discover/dismissed", (r) => r.fulfill({ json: { items: [] } }));
    await page.route(/\/api\/discover\/snoozed/, (r) => r.fulfill({ json: { items: [] } }));
    await page.route("**/api/discover/labels", (r) => r.fulfill({ json: { items: [{ label_id: 999, name: "Stones Throw" }] } }));
    await page.route("**/api/discover/blocked-artists", (r) => r.fulfill({ json: { items: [] } }));
    await page.route("**/api/discover/blocked-labels", (r) => r.fulfill({ json: { items: [] } }));
    await page.route("**/api/discover/token-status", (r) => r.fulfill({ json: { valid: true } }));
    await page.route(/\/api\/discover\/labels\/suggested/, (r) =>
      r.fulfill({ json: { items: [{ name: "Stones Throw", weight: 14.5 }, { name: "Hyperdub", weight: 7.2 }] } }));
    await page.route(/\/api\/discover\/labels\/search/, (r) =>
      r.fulfill({ json: { items: [{ id: 999, name: "Found Label" }] } }));
    await page.route(/\/api\/discover\/feed(\?|$)/, (r) => r.fulfill({
      status: 200,
      headers: { "Content-Type": "text/event-stream", "Cache-Control": "no-cache" },
      body:
        'event: progress\ndata: {"feeder":"artist"}\n\n' +
        'event: release\ndata: {"release_key":"rk1","source":"artist","release":{"id":101,"title":"Madvillainy","artist":"Madvillain","label":"Stones Throw","year":2004,"thumb":"","cover_image":""}}\n\n' +
        'event: release\ndata: {"release_key":"rk2","source":"label","release":{"id":102,"title":"Donuts","artist":"J Dilla","label":"Stones Throw","year":2006,"thumb":"","cover_image":""}}\n\n' +
        'event: done\ndata: {"releases_surfaced":2,"releases_seen":2,"duration_ms":1200}\n\n',
    }));
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
      r.fulfill({ json: {
        id, title: `Detail ${id}`, artist: `Artist ${id}`, label: "Stones Throw", label_id: 999,
        year: 2020, cover: "", styles: ["Hip Hop"], tracklist: [{ position: "A1", title: "Track One", duration: "5:30" }],
      } });
    });
    await page.route(/\/api\/youtube\/search/, (r) => r.fulfill({ json: { candidates: [] } }));
    await page.route("**/api/discover/stats", (r) => r.fulfill({ json: {
      total_scans: 0, avg_duration_ms: null, saves_per_scan: null, novelty_share: {},
      top_labels: [], top_artists: [], followed_count: 1, saved_count: 0, dismissed_count: 0,
      snoozed_count: 0, downloaded_count: 0, blocked_artist_count: 0, blocked_label_count: 0,
    } }));
    // Guard: the anchor card must never fetch in release mode. If it does, fail loud.
    await page.route(/\/api\/transitions\/score$/, (r) => r.fulfill({ json: { ...BASE_SCORE, overall: 99, explanation: ["SHOULD NOT RENDER"] } }));
  }

  test.beforeEach(async ({ page }) => {
    page.on("pageerror", (err) => console.error("[pageerror]", err.message));
    await forceWorkbenchOn(page);
    await mockDiscover(page);
    await page.goto("/");
    await expect(page.locator("#app-status")).toBeVisible({ timeout: 10_000 });
    await expect(page.locator("body.wb-active")).toBeVisible({ timeout: 15_000 });
  });

  test("A-10 Discover release detail shows NO Transition card", async ({ page }) => {
    await expect(page.locator("#wb-disc-place")).toBeVisible({ timeout: 10_000 });
    await page.locator("#wb-disc-place").click();
    // Don't rely on the auto-scan-on-open (non-deterministic under the single
    // sandbox server's load — #189 contention): explicitly click the feed's
    // Refresh CTA to fire the (mocked) scan, then wait for a card. The scan is
    // fully route-mocked, so no real Discogs/feeder traffic hits master.db.
    const refresh = page.locator("#disc-v2-refresh-btn");
    await expect(refresh).toBeVisible({ timeout: 10_000 });
    await refresh.click();
    const card = page.locator("#disc-v2-grid .disc-v2-card").first();
    await expect(card).toBeVisible({ timeout: 15_000 });
    await card.click();

    // Release detail re-hosted in the inspector…
    await expect(page.locator("#wb-inspector-body .wb-insp-title")).toBeVisible({ timeout: 3_000 });
    // …but NO anchor card (mode 'release' is suppressed).
    await page.waitForTimeout(600);
    await expect(
      page.locator("#wb-inspector-body .wb-insp-h").filter({ hasText: "Transition in" }),
    ).toHaveCount(0);
  });
});
