import { test, expect } from "@playwright/test";

/**
 * Pages mode smoke — the same docs/index.html served statically with no
 * API. Tests that:
 *   - The page loads without crashing
 *   - Local-mode-only panels (#tab-nav including #tab-discover) are NOT
 *     visible (the page detects no /api/status response and falls back to
 *     XML upload mode)
 *
 * The failed /api/status probe is EXPECTED in Pages mode — it's how the
 * page detects which mode to render in. We allow-list that one URL.
 */

test.describe("Pages mode", () => {
  test("loads under file:// equivalent (static http.server)", async ({
    page,
  }) => {
    const pagesPort = process.env.AUTOCUE_PAGES_PORT;
    expect(pagesPort, "AUTOCUE_PAGES_PORT not set").toBeTruthy();

    const errors: string[] = [];
    page.on("console", (msg) => {
      const text = msg.text();
      // Page tries /api/status to detect local mode — failure is expected.
      if (text.includes("/api/status")) return;
      if (/TypeError|ReferenceError|Uncaught/.test(text) && msg.type() === "error") {
        errors.push(text);
      }
    });
    page.on("pageerror", (err) => {
      if (err.message.includes("/api/status")) return;
      errors.push(`[pageerror] ${err.message}`);
    });

    await page.goto(`http://localhost:${pagesPort}/index.html`);

    // The XML upload drop zone is the Pages-mode entry point. It should
    // be present once the page settles. We accept either it being visible
    // or the page rendering without crashing — exact selector for the
    // drop zone is not asserted to keep this stable across UI tweaks.
    // The hard assertion is: NO uncaught exceptions, NO TypeErrors.
    await page.waitForTimeout(1500); // let the /api/status probe fail and the page settle

    expect(errors, "Pages-mode console / page errors").toEqual([]);

    // Local-mode tab nav must be HIDDEN in Pages mode (no API ⇒ no tabs).
    // Element exists in the DOM but with display:none until /api/status
    // succeeds. The visible check is the load-bearing assertion.
    await expect(page.locator("#tab-nav")).toBeHidden();
  });
});
