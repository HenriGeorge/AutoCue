import { test, expect } from "@playwright/test";
import { INVENTORY } from "./control-inventory";

/**
 * Regression guard for issue #170.
 *
 * The inventory used to reference three Discover v1 controls (`disc-since-year`,
 * `disc-max-artists`, `disc-scan-btn`) that were removed when Discover v2
 * replaced the legacy scan UI in PRs #166 / #167. Each stale row produced a
 * `control <id> is missing` failure in the per-control sweep — masked at the
 * time by a separate readiness-guard bug, but the underlying staleness is
 * the root cause of those row failures.
 *
 * This Node-side test pins the fix so the inventory cannot silently drift
 * back. Asserting "row present" without also asserting "specific stale rows
 * absent" would let an accidental revert (or a copy-paste tomorrow) reach
 * main again.
 *
 * Why a `.test.ts` filename: matches `per-control-sweep.selector.test.ts` —
 * Playwright's default testMatch discovers both `*.spec.ts` and `*.test.ts`,
 * and this convention flags "pure Node, no browser context" tests.
 */

const DISCOVER_ROW_IDS = new Set(
  INVENTORY.panelControls.discover.map((r) => r.id),
);

test.describe("control inventory — discover v2 alignment (issue #170)", () => {
  test("legacy v1 ids are gone from panelControls.discover", () => {
    // These ids existed in docs/index.html before Discover v2 landed and were
    // removed when the settings panel + new filter bar replaced the v1 scan
    // form. Without this fix, the per-control sweep would fail at row
    // enumeration with `control <id> is missing`.
    for (const stale of [
      "disc-since-year",
      "disc-max-artists",
      "disc-scan-btn",
    ]) {
      expect(
        DISCOVER_ROW_IDS.has(stale),
        `stale Discover v1 id '${stale}' must not appear in panelControls.discover`,
      ).toBe(false);
    }
  });

  test("Discover v2 filter-bar ids from PR #167 are present", () => {
    // The user-facing top-level Discover panel filter-bar controls. These are
    // the rows the per-control sweep exercises once the readiness-guard fix
    // (tracked separately) lets it reach the panel. If any of these ids is
    // missing, an obvious user-visible control is going untested.
    for (const id of [
      "disc-v2-settings-btn",
      "disc-v2-refresh-btn",
      "disc-v2-sort",
      "disc-v2-year",
      "disc-v2-year-custom",
      "disc-v2-search",
      "disc-v2-hide-saved",
      "disc-v2-hide-dismissed",
    ]) {
      expect(
        DISCOVER_ROW_IDS.has(id),
        `Discover v2 filter-bar id '${id}' missing from panelControls.discover`,
      ).toBe(true);
    }
  });

  test("download-section rows survive the rename", () => {
    // The download panel was untouched by Discover v2 — its three rows must
    // still be in the inventory after the rename.
    for (const id of ["dl-query", "dl-go-btn", "dl-dest-switch"]) {
      expect(
        DISCOVER_ROW_IDS.has(id),
        `download row '${id}' must still be inventoried after the v2 rename`,
      ).toBe(true);
    }
  });

  test("every discover row has a recognised kind", () => {
    // Boundary check: kind enums map 1:1 with how the per-control sweep
    // interacts with each row. A typo (`sselect`, `bttn`) would silently fall
    // into the helper's default branch and hide a real interaction bug.
    const allowed = new Set([
      "button",
      "checkbox",
      "select",
      "number",
      "search",
      "text",
      "password",
      "range",
    ]);
    for (const row of INVENTORY.panelControls.discover) {
      expect(
        allowed.has(row.kind),
        `row '${row.id}' has unrecognised kind '${row.kind}'`,
      ).toBe(true);
    }
  });
});
