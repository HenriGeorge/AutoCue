import { test, expect, Page, Route } from "@playwright/test";

/**
 * AutoCue 2.0 — DESIGN ↔ LIVE parity regression (FAST, deterministic).
 *
 * Codifies the parity verdicts the crew verified by hand against the frozen Claude-Design
 * authority (`AutoCue Workbench.dc.html`). The static design baselines live in
 * `tests/e2e/parity-baselines/design-*.png` (the .dc is frozen → reusable reference;
 * do NOT pixel-diff them against the live app — real data ≠ the 18 mock tracks).
 *
 * Instead this spec asserts the DETERMINISTIC parity rules that survive real data:
 *   • the 5 AutoCue token rules (two themes, green-is-signal-not-CTA, mono-for-data, pills)
 *   • the Bucket-1 structural fixes (inspector score ring fill, inspector phrase strip,
 *     Tag/Enrich ink CTAs, Discover green-wash source chips, Nightboard empty-state).
 *
 * Runs under the existing harness (sandbox DB copy; .env present so the Discogs token is
 * set). Each assertion can fail independently. Selectors align with `.crew/researcher.md`.
 * #189-safe: the one place that would trigger a real Discogs scan (Discover) is mocked.
 */

const GREEN_LIGHT = { r: 21, g: 154, b: 5 };   // --green  #159a05
const GREEN_DARK = { r: 40, g: 226, b: 20 };    // --green  #28e214
const RING_C = 251.3;                            // inspector score-ring circumference (r=40)

function parseRgb(s: string): { r: number; g: number; b: number; a: number } | null {
  const m = /rgba?\(\s*([\d.]+)[,\s]+([\d.]+)[,\s]+([\d.]+)(?:[,\s/]+([\d.]+))?\s*\)/.exec(s || "");
  if (!m) return null;
  return { r: +m[1], g: +m[2], b: +m[3], a: m[4] === undefined ? 1 : +m[4] };
}
function near(c: { r: number; g: number; b: number }, t: { r: number; g: number; b: number }, tol = 6) {
  return Math.abs(c.r - t.r) <= tol && Math.abs(c.g - t.g) <= tol && Math.abs(c.b - t.b) <= tol;
}
function isNeutral(c: { r: number; g: number; b: number }) {
  // ink family is grayscale (near-black --ink #0a0a0a or near-white --on-ink #fafafa)
  return Math.max(c.r, c.g, c.b) - Math.min(c.r, c.g, c.b) <= 24;
}

async function bootWorkbench(page: Page) {
  // workbench is default-on, but pin the flag so an opted-out localStorage can't strand us.
  await page.addInitScript(() => { try { localStorage.setItem("ac_workbench", "1"); } catch (_) {} });
  await page.goto("/");
  await expect(page.locator("#app-status")).toBeVisible({ timeout: 15_000 });
  await expect(page.locator("body.wb-active")).toBeVisible({ timeout: 15_000 });
}

/** Read a CSS custom property off :root, trimmed. */
function token(page: Page, name: string) {
  return page.evaluate(
    (n) => getComputedStyle(document.documentElement).getPropertyValue(n).trim(),
    name,
  );
}

/**
 * Focus a track that HAS analysis data and wait until its inspector populates.
 * The default grid sort is Album, whose head rows can be non-electronic tracks with
 * BPM 0 / no phrase data (no mono BPM chip, no mix score). The "Beat grid only" filter
 * (#beats-only-cb) surfaces only BPM>0 tracks → a populated inspector (BPM chip + mix score).
 */
async function focusBeatGridTrack(page: Page) {
  await expect(page.locator("#track-list .track-card").first()).toBeVisible({ timeout: 20_000 });
  await page.locator("#beats-only-cb").check();
  // find a visible card whose track has bpm>0 (confirmed via ACBridge), then evaluate-click
  // it — a Playwright .click() fights the Virtualizer (it recycles the row on
  // scroll-into-view, so actionability never settles).
  const id: string = await page.waitForFunction(() => {
    const AC = (window as any).ACBridge;
    if (!AC?.tracks) return false;
    const byId = new Map(AC.tracks().map((t: any) => [String(t.id), t]));
    const cards = Array.from(document.querySelectorAll("#track-list .track-card[data-track-id]")) as HTMLElement[];
    for (const c of cards) {
      const t: any = byId.get(String(c.dataset.trackId));
      if (t && Number(t.bpm) > 0) return c.dataset.trackId!;
    }
    return false;
  }, undefined, { timeout: 15_000 }).then((h) => h.jsonValue());
  await page.evaluate((tid) => {
    (document.querySelector(`#track-list .track-card[data-track-id="${tid}"]`) as HTMLElement | null)?.click();
  }, id);
  await expect(page.locator("body.wb-inspecting")).toBeVisible({ timeout: 10_000 });
}

test.describe("Design↔Live parity (token rules)", () => {
  test.beforeEach(async ({ page }) => { await bootWorkbench(page); });

  test("two-theme palette: --bg + --green are the exact tokens in light AND dark", async ({ page }) => {
    // start light (fresh context → no persisted theme; defend anyway)
    if (await page.locator("html.dark").count()) await page.locator("#theme-toggle").click();
    await expect(page.locator("html.dark")).toHaveCount(0);
    expect(await token(page, "--bg")).toBe("#fafafa");
    expect(await token(page, "--green")).toBe("#159a05");

    // toggle dark
    await page.locator("#theme-toggle").click();
    await expect(page.locator("html.dark")).toHaveCount(1);
    expect(await token(page, "--bg")).toBe("#0c0a09");      // warm stone, not pure black
    expect(await token(page, "--green")).toBe("#28e214");
  });

  test("primary CTA (#nb-export-btn) background is the --ink family, NOT green; pill radius", async ({ page }) => {
    await page.locator("#nb-open-btn").click();
    await expect(page.locator("body.nb-active")).toBeVisible();
    const exp = page.locator("#nb-export-btn");
    await expect(exp).toBeVisible();
    const { bg, radius } = await exp.evaluate((el) => ({
      bg: getComputedStyle(el).backgroundColor,
      radius: getComputedStyle(el).borderRadius,
    }));
    const c = parseRgb(bg)!;
    expect(c, `CTA bg "${bg}" must parse`).not.toBeNull();
    expect(near(c, GREEN_LIGHT), `CTA bg ${bg} must not be --green light`).toBe(false);
    expect(near(c, GREEN_DARK), `CTA bg ${bg} must not be --green dark`).toBe(false);
    expect(isNeutral(c), `CTA bg ${bg} must be ink-family neutral (black/white)`).toBe(true);
    // pill: >= 999px (some pills compute as 9999px clamp)
    expect(parseFloat(radius)).toBeGreaterThanOrEqual(999);
    // and the design token itself is exactly 999px
    expect(await token(page, "--radius-pill")).toBe("999px");
  });

  test("data values render in JetBrains Mono; prose labels render in Inter (no mono leak)", async ({ page }) => {
    test.setTimeout(60_000);
    await focusBeatGridTrack(page);
    // a measured data chip (BPM) is mono
    const chipFont = await page.locator("#wb-inspector-body .wb-insp-chip.mono").first()
      .evaluate((el) => getComputedStyle(el).fontFamily);
    expect(chipFont).toMatch(/JetBrains Mono|monospace/i);
    // the ring number (a measured value) is mono
    const ringFont = await page.locator("#wb-insp-ring-num")
      .evaluate((el) => getComputedStyle(el).fontFamily);
    expect(ringFont).toMatch(/JetBrains Mono|monospace/i);
    // a prose label is sans (Inter) — no mono bleed into non-data UI
    const headFont = await page.locator("#wb-inspector-body .wb-insp-head").first()
      .evaluate((el) => getComputedStyle(el).fontFamily);
    expect(headFont).toMatch(/Inter|system-ui/i);
    expect(headFont).not.toMatch(/JetBrains Mono/i);
  });
});

test.describe("Design↔Live parity (Bucket-1 structure)", () => {
  test.beforeEach(async ({ page }) => { await bootWorkbench(page); });

  test("inspector score ring FILLS proportional to the mix score (not the empty state)", async ({ page }) => {
    test.setTimeout(60_000);
    // Mock mixability so the async chip resolves deterministically + fast (a cold sandbox
    // cache makes the live compute slow/flaky). This exercises the FIX wiring: the ring
    // re-renders from the RESOLVED score (the regressed build left it stuck at "–"/empty).
    await page.route(/\/api\/tracks\/\d+\/mixability/, (r) => r.fulfill({ json: {
      score: 72, components: { intro: 80, outro: 0, energy: 90, vocals: 50, structure: 70 },
      intro_bars: 4, outro_bars: 4, vocal_proxy: false, phrase_count: 8,
    } }));
    await focusBeatGridTrack(page);
    // wait for the ring number to become numeric — proves _updateScoreRing ran AFTER the
    // chip resolved (a regressed build stays "–").
    await page.waitForFunction(() => {
      const n = document.querySelector("#wb-insp-ring-num");
      return !!n && /^\d+$/.test((n.textContent || "").trim());
    }, undefined, { timeout: 15_000 });

    const { score, offset } = await page.evaluate(() => ({
      score: Number(document.querySelector("#wb-insp-ring-num")!.textContent!.trim()),
      offset: parseFloat(document.querySelector("#wb-insp-ring-arc")!.getAttribute("stroke-dashoffset") || "NaN"),
    }));
    expect(score).toBeGreaterThan(0);
    expect(score).toBeLessThanOrEqual(100);
    // arc draw is proportional: dashoffset ≈ C·(1 − score/100), and NOT the full-circle empty state.
    const expected = RING_C * (1 - score / 100);
    expect(Math.abs(offset - expected), `dashoffset ${offset} ≈ ${expected.toFixed(2)} for score ${score}`).toBeLessThan(2);
    expect(offset, "ring must not be the empty full-circumference state").toBeLessThan(RING_C - 1);
  });

  test("phrase-ready track → ACBridge.phraseState returns segments AND the inspector strip renders", async ({ page }) => {
    test.setTimeout(60_000);
    // Mock the phrase-cue lazy loader (POST /api/generate, mode:'phrase') so phrase segments
    // populate deterministically + fast (cold sandbox compute is slow/flaky). Echo cues for
    // exactly the requested ids → ACBridge.phraseState(id) returns them. This exercises the
    // FIX: the inspector reads window.ACBridge.phraseState (the old window.phraseCueState was
    // a phantom bare-`let` that always yielded the fallback).
    const MOCK_CUES = [
      { position_ms: 0, label: "Intro", slot: 0, name: "", confidence: 1, phrase_bars: 16 },
      { position_ms: 8000, label: "Build", slot: 1, name: "", confidence: 1, phrase_bars: 16 },
      { position_ms: 16000, label: "Drop", slot: 2, name: "", confidence: 1, phrase_bars: 16 },
      { position_ms: 24000, label: "Break", slot: 3, name: "", confidence: 1, phrase_bars: 16 },
      { position_ms: 32000, label: "Drop", slot: 4, name: "", confidence: 1, phrase_bars: 16 },
      { position_ms: 40000, label: "Outro", slot: 5, name: "", confidence: 1, phrase_bars: 16 },
    ];
    await page.route(/\/api\/generate(\?|$)/, async (r) => {
      let ids: any[] = [];
      try { ids = r.request().postDataJSON()?.track_ids || []; } catch (_) {}
      await r.fulfill({ json: { tracks: ids.map((id) => ({ id, mode_used: "phrase", cues: MOCK_CUES })) } });
    });

    await expect(page.locator("#track-list .track-card").first()).toBeVisible({ timeout: 20_000 });
    // surface phrase-ready cards: the lazy collector only queues has_phrase tracks, so the
    // Album-sorted head rows (no phrase data) would never trigger a /api/generate fetch.
    await page.locator("#phrase-only-cb").check();
    await page.waitForTimeout(300); // let the grid refilter to phrase-ready tracks
    // enable phrase analysis → the lazy loader fetches /api/generate for the visible cards.
    // evaluate-click the toggle: a Playwright .click() flakes ("element is not stable") because
    // the refilter's phrase-progress banner shifts the topbar-tools layout under it.
    await page.evaluate(() => (document.querySelector("#mode-phrase-btn") as HTMLElement | null)?.click());
    await page.evaluate(() => {
      try { (window as any)._collectPhraseLazyIds?.(); } catch (_) {}
      try { (window as any)._flushPhraseLazyQueue?.(); } catch (_) {}
    });
    // wait until a visible card has phrase segments (via the new ACBridge API) AND a real
    // duration (so the inspector strip can compute segment widths)
    const id: string = await page.waitForFunction(() => {
      const AC = (window as any).ACBridge;
      if (!AC?.tracks || !AC?.phraseState) return false;
      const byId = new Map(AC.tracks().map((t: any) => [String(t.id), t]));
      const cards = Array.from(document.querySelectorAll("#track-list .track-card[data-track-id]")) as HTMLElement[];
      for (const c of cards) {
        const tid = c.dataset.trackId!;
        const s = AC.phraseState(tid);
        const t: any = byId.get(String(tid));
        const tt = t ? (t.totalTime ?? t.total_time ?? t.duration ?? 0) : 0;
        if (Array.isArray(s) && s.length > 0 && tt > 0) return tid;
      }
      return false;
    }, undefined, { timeout: 20_000 }).then((h) => h.jsonValue());

    // the API returns real segments for that track
    const segLen = await page.evaluate((tid) => (window as any).ACBridge.phraseState(tid).length, id);
    expect(segLen).toBeGreaterThan(0);

    // focus it → the inspector renders the coloured phrase-structure strip (not the fallback).
    // Dispatch the card's click via evaluate: a Playwright .click() fights the Virtualizer
    // (it recycles the row on scroll-into-view, so the actionability retry never settles).
    await page.evaluate((tid) => {
      const el = document.querySelector(`#track-list .track-card[data-track-id="${tid}"]`) as HTMLElement | null;
      el?.click();
    }, id);
    await expect(page.locator("body.wb-inspecting")).toBeVisible();
    await expect(page.locator("#wb-inspector .wb-insp-phrase-section .phrase-strip")).toBeVisible({ timeout: 5_000 });
    const fallback = await page.locator("#wb-inspector .wb-insp-phrase-section").innerText();
    expect(fallback).not.toMatch(/No phrase analysis yet/i);
  });

  test("Tag & Enrich run buttons (#discogs-run-btn / #ce-run-btn) use the --ink background", async ({ page }) => {
    await page.locator("#wb-library-place").click();
    await expect(page.locator("body")).toHaveClass(/wb-place-library/);
    const inkHex = await token(page, "--ink");
    const ink = parseRgb(
      await page.locator("#discogs-run-btn").evaluate(() => {
        // resolve the hex token to an rgb baseline via a throwaway element
        const probe = document.createElement("span");
        probe.style.color = getComputedStyle(document.documentElement).getPropertyValue("--ink");
        document.body.appendChild(probe);
        const c = getComputedStyle(probe).color; probe.remove(); return c;
      }),
    )!;
    for (const id of ["#discogs-run-btn", "#ce-run-btn"]) {
      const btn = page.locator(id);
      await expect(btn, `${id} present`).toHaveCount(1);
      const { bg, radius } = await btn.evaluate((el) => ({
        bg: getComputedStyle(el).backgroundColor,
        radius: getComputedStyle(el).borderRadius,
      }));
      const c = parseRgb(bg)!;
      expect(near(c, ink), `${id} bg ${bg} == --ink ${inkHex} (${ink.r},${ink.g},${ink.b})`).toBe(true);
      expect(near(c, GREEN_LIGHT), `${id} must not be green`).toBe(false);
      expect(parseFloat(radius), `${id} is a pill`).toBeGreaterThanOrEqual(999);
    }
  });

  test("Discover source chip shows the --green-wash background when checked", async ({ page }) => {
    // mock the discover surface so no real Discogs scan runs (#189-safe + fast)
    await page.route(/\/api\/discover\/token-status/, (r: Route) => r.fulfill({ json: { valid: true } }));
    for (const p of ["saved", "dismissed", "snoozed", "labels", "blocked-artists", "blocked-labels"]) {
      await page.route(new RegExp(`/api/discover/${p}(\\?|$)`), (r: Route) => r.fulfill({ json: { items: [] } }));
    }
    await page.route(/\/api\/discover\/labels\/suggested/, (r: Route) => r.fulfill({ json: { items: [] } }));
    await page.route(/\/api\/discover\/stats/, (r: Route) => r.fulfill({ json: {} }));
    await page.route(/\/api\/discover\/feed(\?|$)/, (r: Route) => r.fulfill({
      status: 200, headers: { "Content-Type": "text/event-stream" },
      body: 'event: done\ndata: {"releases_surfaced":0,"releases_seen":0,"duration_ms":1}\n\n',
    }));

    await page.locator("#wb-disc-place").click();
    await expect(page.locator("body")).toHaveClass(/wb-place-disc/);
    const chip = page.locator(".disc-v2-chip:has(input[data-source='artist'])");
    await expect(chip).toBeVisible({ timeout: 10_000 });

    // ensure the Artist source is checked (default on; click the label if not)
    if (!(await page.locator("input[data-source='artist']").isChecked())) await chip.click();
    await expect(page.locator("input[data-source='artist']")).toBeChecked();

    const bg = await chip.evaluate((el) => getComputedStyle(el).backgroundColor);
    const c = parseRgb(bg)!;
    expect(c, `chip bg "${bg}" parses`).not.toBeNull();
    // green-wash = the --green rgb at a low alpha tint (not a solid green, not transparent)
    expect(near(c, GREEN_LIGHT), `checked chip bg ${bg} is the --green tint`).toBe(true);
    expect(c.a).toBeGreaterThan(0);
    expect(c.a).toBeLessThan(0.3);
  });

  test("Nightboard pre-build shows the #nb-stage.nb-empty empty-state", async ({ page }) => {
    await page.locator("#nb-open-btn").click();
    await expect(page.locator("body.nb-active")).toBeVisible();
    // before any build the stage carries the empty class + renders the empty-state block
    await expect(page.locator("#nb-stage.nb-empty")).toBeVisible();
    await expect(page.locator("#nb-stage .nb-empty-state")).toBeVisible();
    await expect(page.locator("#nb-timeline .nb-tile")).toHaveCount(0);
  });
});
