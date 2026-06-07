import { test, expect } from "@playwright/test";
import { INVENTORY, allInventoryIds, PANEL_NAMES } from "./control-inventory";

/**
 * Drift guard. Enumerates every interactive control with a stable id from the
 * live DOM (after clearing inline display:none on every section so nothing
 * collapsed-by-default escapes counting), then diffs against the inventory
 * JSON. Fails loudly in both directions:
 *
 *   1. DOM has IDs NOT in the inventory  → control added, matrix missed it
 *   2. Inventory has IDs NOT in the DOM  → control renamed / removed
 *
 * Failure output lists exact ids — one per line — so the maintainer knows
 * which side to fix.
 */

const SCANNABLE_TAGS = ["button", "input", "select", "textarea"] as const;

async function enumerateLiveIds(page: import("@playwright/test").Page): Promise<Set<string>> {
  // Clear inline display:none on every `*-section, *-body` so hidden-by-default
  // panels (auto-tag, modals, fold-out sub-panels) become enumerable. We do
  // this via JS, not by clicking headers, to avoid firing onclick handlers
  // that might mutate state.
  const ids = await page.evaluate(() => {
    const force = (sel: string) => {
      for (const el of Array.from(
        document.querySelectorAll<HTMLElement>(sel),
      )) {
        if (el.style.display === "none") el.style.display = "";
      }
    };
    force("[id$='-section']");
    force("[id$='-body']");
    force("[id$='-panel']");
    force("section");

    const out: string[] = [];
    for (const sel of ["button", "input", "select", "textarea"]) {
      for (const el of Array.from(
        document.querySelectorAll<HTMLElement>(sel),
      )) {
        const id = el.id;
        if (!id) continue;
        out.push(id);
      }
    }
    return Array.from(new Set(out)).sort();
  });
  return new Set(ids);
}

test.describe("control inventory drift guard", () => {
  test("live DOM matches inventory in both directions", async ({ page }) => {
    await page.goto("/");
    await expect(page.locator("#app-status")).toBeVisible({ timeout: 10_000 });
    await expect(page.locator("#tab-nav")).toBeVisible({ timeout: 10_000 });

    // Collect across all three tabs (each tab gates a different subtree of
    // controls).
    const seen = new Set<string>();
    for (const tabId of ["#tab-cues", "#tab-library", "#tab-discover"]) {
      await page.locator(tabId).click();
      // Per-tab readiness signal — match docs/qa_tester.md §3 in spirit.
      if (tabId === "#tab-cues") {
        await expect(page.locator("#tracks-section")).toBeAttached();
      } else if (tabId === "#tab-library") {
        await expect(page.locator("#health-section")).toBeAttached();
      } else {
        await expect(page.locator("#discover-section")).toBeAttached();
        await expect(page.locator("#download-section")).toBeAttached();
      }
      const tabIds = await enumerateLiveIds(page);
      for (const id of tabIds) seen.add(id);
    }

    const expected = allInventoryIds();

    const domExtras = [...seen].filter((id) => !expected.has(id)).sort();
    const inventoryStales = [...expected].filter((id) => !seen.has(id)).sort();

    // Allowlist: ids that exist in the DOM but are NOT user-facing controls
    // we want to track. Anything not here MUST be in the inventory.
    const ALLOWED_DOM_EXTRAS = new Set<string>([
      // Transient progress elements — appear only during specific operations.
      "phrase-progress-cancel",
      // The legacy XML upload zone — covered by Pages-mode spec.
      "xml-input",
      "audio-input",
      "anlz-input",
      // Backup multi-select UI — exercised by backup-related tests.
      "backup-select-all",
      // Form helpers / labels with id but no user click target.
      "discover-token",
      // Filter dialog modal internals — covered indirectly when the trigger
      // button is clicked; not first-class rows.
      "tag-filter-modal-search",
      "tag-filter-modal-apply",
      "key-filter-modal-apply",
      "genre-filter-modal-search",
      "genre-filter-modal-apply",
      "kbd-hint-modal-close",
      "restore-modal-close",
    ]);
    const unexpectedExtras = domExtras.filter(
      (id) => !ALLOWED_DOM_EXTRAS.has(id),
    );

    if (unexpectedExtras.length === 0 && inventoryStales.length === 0) return;

    const lines: string[] = [
      "Control inventory drift detected — fix one of the two lists below.",
      "",
    ];
    if (unexpectedExtras.length > 0) {
      lines.push(
        "DOM has IDs NOT in tests/e2e/control-inventory.json",
        "(add them, OR add to ALLOWED_DOM_EXTRAS if not user-facing):",
      );
      for (const id of unexpectedExtras) lines.push(`  #${id}`);
      lines.push("");
    }
    if (inventoryStales.length > 0) {
      lines.push(
        "tests/e2e/control-inventory.json has IDs NOT in the DOM",
        "(rename in JSON to match new id, OR remove if control was deleted):",
      );
      for (const id of inventoryStales) lines.push(`  #${id}`);
    }
    throw new Error(lines.join("\n"));
  });

  test("per-track testid attaches to the main track list only", async ({
    page,
  }) => {
    await page.goto("/");
    await expect(page.locator("#tab-cues")).toBeVisible();
    await page.locator("#tab-cues").click();
    // Wait for tracks to render. Empty libraries (CI smoke) still render the
    // container; we just confirm the testid is scoped correctly.
    await expect(page.locator("#track-list")).toBeAttached();

    // The testid must appear ONLY inside #track-list. If it appears anywhere
    // else (drag previews, modals), the per-track sampling picks unpredictable
    // elements.
    const outsideCount = await page.evaluate(() => {
      const all = Array.from(
        document.querySelectorAll<HTMLElement>("[data-testid='track-card']"),
      );
      const inList = new Set(
        Array.from(
          document.querySelectorAll<HTMLElement>(
            "#track-list [data-testid='track-card']",
          ),
        ),
      );
      return all.filter((el) => !inList.has(el)).length;
    });
    expect(
      outsideCount,
      "data-testid='track-card' must be scoped to #track-list descendants",
    ).toBe(0);
  });

  test("panel names exported match inventory keys", () => {
    expect([...PANEL_NAMES].sort()).toEqual(
      Object.keys(INVENTORY.panelControls).sort(),
    );
  });
});
