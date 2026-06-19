import { test, expect, Route, Page } from "@playwright/test";

/**
 * AutoCue 2.0 — Review Dock (dev-only in-page human→AI feedback bridge).
 * Coverage map: crew/test-designer.md · DESIGN: crew/DESIGN.md (approved 2026-06-19).
 *
 * Scope here = the LAYOUT + render-gate + submit-flow truths jsdom cannot see
 * (the dock pinned at the viewport bottom, the ink-pill Send computed colour,
 * both themes) and the live POST contract. The pure DOM/state logic
 * (_derivePage, double-submit guard, fake-timer "✓ sent" clear) is covered by
 * the review-dock vitest; the file-append + 403 dev-gate by pytest.
 *
 * SAFETY: the dock is dev-only. It renders ONLY when local mode AND
 * localStorage.ac_review_dock === '1' (set BEFORE load via an init script).
 *
 * /api/review-note is ALWAYS route-mocked here — the real endpoint appends to
 * crew/REVIEW-NOTES.md, which the e2e must never write (DESIGN §VERIFY). The
 * real file append + the prod-403 gate are pytest's job (API-9 / S-1).
 *
 * Runs against 127.0.0.1 (config baseURL), never localhost. Run ALONE (#189).
 */

const PLACEHOLDER = "describe a change for this page…";

async function forceWorkbenchOn(page: Page) {
  await page.addInitScript(() => {
    try { localStorage.setItem("ac_workbench", "1"); } catch (_) {}
  });
}

async function bootLocalMode(page: Page) {
  await page.goto("/");
  await expect(page.locator("#app-status")).toBeVisible({ timeout: 10_000 });
  await expect(page.locator("body.wb-active")).toBeVisible({ timeout: 15_000 });
  await page.waitForFunction(
    () => !!(window as any).ACBridge && (window as any).ACBridge.isLocalMode?.() === true,
    undefined,
    { timeout: 15_000 },
  );
}

// ── Render-gate tests (flag-driven; each sets its OWN init script) ──
test.describe("Review Dock — render gates", () => {
  test("S-5 flag unset (local mode) → dock does NOT render", async ({ page }) => {
    await forceWorkbenchOn(page);
    await page.addInitScript(() => {
      try { localStorage.removeItem("ac_review_dock"); } catch (_) {}
    });
    await bootLocalMode(page);
    // Give initReviewDock() a beat to (not) mount.
    await page.waitForTimeout(400);
    await expect(page.locator(".review-dock")).toHaveCount(0);
  });
});

// ── Gated-ON tests (flag '1' before load + route-mock) ──
test.describe("Review Dock — gated on (flag '1' + local mode)", () => {
  // Shared mock state: per-test hit log + controllable status.
  let review: { hits: any[]; status: number };

  test.beforeEach(async ({ page }) => {
    page.on("pageerror", (err) => console.error("[pageerror]", err.message));
    review = { hits: [], status: 200 };
    await forceWorkbenchOn(page);
    await page.addInitScript(() => {
      try { localStorage.setItem("ac_review_dock", "1"); } catch (_) {}
    });
    // ALWAYS mock the append endpoint — never write the real REVIEW-NOTES.md.
    await page.route("**/api/review-note", (route: Route) => {
      let body: any = null;
      try { body = route.request().postDataJSON(); } catch (_) { body = route.request().postData(); }
      review.hits.push(body);
      if (review.status >= 400) {
        return route.fulfill({ status: review.status, contentType: "application/json", body: JSON.stringify({ detail: "boom" }) });
      }
      return route.fulfill({ json: { ok: true } });
    });
    await bootLocalMode(page);
    await expect(page.locator(".review-dock")).toBeVisible({ timeout: 10_000 });
  });

  // ── S-6 — both gates satisfied → dock renders, pinned at the viewport bottom ──
  test("S-6 both gates → dock renders and is pinned at the viewport bottom", async ({ page }) => {
    const dock = page.locator(".review-dock");
    await expect(dock).toHaveCount(1);
    await expect(dock).toBeVisible();
    const geom = await dock.evaluate((el) => {
      const r = el.getBoundingClientRect();
      return { position: getComputedStyle(el as HTMLElement).position, bottom: r.bottom, vh: window.innerHeight };
    });
    expect(geom.position).toBe("fixed");
    expect(Math.abs(geom.bottom - geom.vh)).toBeLessThan(4); // bottom edge at viewport bottom
  });

  // ── U-1 — idle render: label, mono page badge, placeholder, ink-pill Send ──
  test("U-1 form sub-nodes: sr-only label, mono page badge, placeholder, Send", async ({ page }) => {
    const dock = page.locator(".review-dock");

    // Real sr-only label, associated with the input.
    const label = dock.locator('label[for="review-dock-input"]');
    await expect(label).toHaveCount(1);
    await expect(label).toHaveClass(/sr-only/);
    await expect(label).toContainText("Describe a change for this page");

    // Page badge is mono.
    const badge = dock.locator(".review-dock-page");
    await expect(badge).toHaveCount(1);
    const badgeFont = await badge.evaluate((el) => getComputedStyle(el as HTMLElement).fontFamily.toLowerCase());
    expect(badgeFont).toContain("mono");

    // Input + placeholder.
    const input = dock.locator("#review-dock-input");
    await expect(input).toHaveAttribute("placeholder", PLACEHOLDER);

    // Send is a button inside the dock.
    await expect(dock.locator("button")).toHaveCount(1);
  });

  // ── U-3 — empty submit → no request ──
  test("U-3 empty submit → 0 requests to /api/review-note", async ({ page }) => {
    const input = page.locator("#review-dock-input");
    await input.click();
    await input.fill("   "); // whitespace only
    await input.press("Enter");
    await page.waitForTimeout(500);
    expect(review.hits.length).toBe(0);
    // And a truly-empty submit too.
    await input.fill("");
    await input.press("Enter");
    await page.waitForTimeout(300);
    expect(review.hits.length).toBe(0);
  });

  // ── U-6 — type + submit → {ok:true} → "✓ sent" appears then clears; body asserted ──
  test("U-6 submit ok → posts {page,note}, shows '✓ sent' then clears", async ({ page }) => {
    const input = page.locator("#review-dock-input");
    const note = "tighten the dock spacing";
    await input.click();
    await input.fill(note);
    await input.press("Enter");

    // Confirmation appears (aria-live status node).
    const sent = page.locator(".review-dock-status");
    await expect(sent).toContainText("sent", { timeout: 5_000 });

    // The POST body carried {page, note}.
    expect(review.hits.length).toBeGreaterThanOrEqual(1);
    const body = review.hits[review.hits.length - 1];
    expect(body).toBeTruthy();
    expect(body.note).toBe(note);
    expect(typeof body.page).toBe("string");
    expect(body.page.length).toBeGreaterThan(0);

    // Input cleared on success.
    await expect(input).toHaveValue("");

    // "✓ sent" auto-clears (~2s per DESIGN); allow headroom.
    await expect(sent).not.toContainText("sent", { timeout: 4_000 });
  });

  // ── ST-5b — z-order above #action-bar (DESIGN §STYLE) ──
  // Split out from the layout test: DESIGN requires the dock z-index ABOVE the
  // action-bar. This currently FAILS (dock 140 < #action-bar 350) — kept as a
  // real, fixable parity check, not weakened to green.
  test("ST-5b dock z-index is above #action-bar (DESIGN §STYLE)", async ({ page }) => {
    const z = await page.evaluate(() => {
      const dock = document.querySelector(".review-dock") as HTMLElement;
      const ab = document.getElementById("action-bar");
      return {
        dockZ: parseInt(getComputedStyle(dock).zIndex || "0", 10) || 0,
        actionZ: ab ? (parseInt(getComputedStyle(ab).zIndex || "0", 10) || 0) : null,
      };
    });
    if (z.actionZ !== null) {
      expect(z.dockZ, `dock z-index ${z.dockZ} must be above #action-bar ${z.actionZ}`).toBeGreaterThan(z.actionZ);
    }
  });

  // ── U-7 — submit 500 → toast + input value preserved ──
  test("U-7 submit error (500) → toast shown, note preserved in the field", async ({ page }) => {
    review.status = 500;
    const input = page.locator("#review-dock-input");
    const note = "this should fail and be kept";
    await input.click();
    await input.fill(note);
    await input.press("Enter");

    // An error toast surfaces (window.showToast → .toast-item in #toast-stack).
    await expect(page.locator("#toast-stack .toast-item")).toHaveCount(1, { timeout: 5_000 });

    // The request was attempted, and the note is preserved (not lost on failure).
    expect(review.hits.length).toBeGreaterThanOrEqual(1);
    await expect(input).toHaveValue(note);
  });

  // ── U-9 — page recomputed at submit: enter Nightboard → posted page "nightboard" ──
  test("U-9 switch to Nightboard then submit → posted page is 'nightboard'", async ({ page }) => {
    // Enter the Nightboard full-bleed mode (the toolbar verb sets body.nb-active).
    await expect(page.locator("#nb-open-btn")).toBeVisible();
    await page.locator("#nb-open-btn").click();
    await expect(page.locator("body.nb-active")).toBeVisible();

    const input = page.locator("#review-dock-input");
    await input.fill("nightboard note"); // fill works even if the canvas overlays
    await input.press("Enter");

    await expect.poll(() => review.hits.length, { timeout: 5_000 }).toBeGreaterThanOrEqual(1);
    const body = review.hits[review.hits.length - 1];
    expect(body.page).toBe("nightboard");
  });

  // ── ST-1 — Send button bg == --ink, NOT --green ──
  test("ST-1 Send button background resolves to --ink, never --green", async ({ page }) => {
    const probe = await page.evaluate(() => {
      const send = document.querySelector(".review-dock button") as HTMLElement | null;
      if (!send) return null;
      const resolve = (token: string) => {
        const t = document.createElement("span");
        t.style.cssText = `position:absolute;background:var(${token})`;
        document.body.appendChild(t);
        const c = getComputedStyle(t).backgroundColor;
        t.remove();
        return c;
      };
      return {
        sendBg: getComputedStyle(send).backgroundColor,
        ink: resolve("--ink"),
        green: resolve("--green"),
      };
    });
    expect(probe, "Send button not found").not.toBeNull();
    expect(probe!.sendBg).toBe(probe!.ink); // ink pill
    expect(probe!.sendBg).not.toBe(probe!.green); // green is signal only
  });

  // ── ST-5 — fixed full-width bar pinned at the viewport bottom ──
  test("ST-5 dock is fixed, full-width, pinned at the viewport bottom", async ({ page }) => {
    const geom = await page.evaluate(() => {
      const dock = document.querySelector(".review-dock") as HTMLElement;
      const r = dock.getBoundingClientRect();
      return {
        position: getComputedStyle(dock).position,
        left: r.left, right: r.right, bottom: r.bottom,
        vw: window.innerWidth, vh: window.innerHeight,
      };
    });
    expect(geom.position).toBe("fixed");
    expect(geom.left).toBeLessThan(4);                 // flush left
    expect(Math.abs(geom.right - geom.vw)).toBeLessThan(4); // full width
    expect(Math.abs(geom.bottom - geom.vh)).toBeLessThan(4); // bottom edge at viewport bottom
  });

  // ── ST-8 — renders in both themes (light + html.dark) ──
  test("ST-8 dock renders in both light and dark themes (screenshots)", async ({ page }) => {
    await expect(page.locator("html.dark")).toHaveCount(0); // starts light
    await page.locator(".review-dock").scrollIntoViewIfNeeded().catch(() => {});
    await page.screenshot({ path: "test-results/review-dock-light.png" });

    await page.locator("#theme-toggle").click();
    await expect(page.locator("html.dark")).toHaveCount(1);
    await expect(page.locator(".review-dock")).toBeVisible();
    await page.screenshot({ path: "test-results/review-dock-dark.png" });
  });
});
