import { test, expect } from "@playwright/test";
import { buildIdSelector } from "./per-control-sweep.helpers";

/**
 * Regression guard for issue #20.
 *
 * `per-control-sweep.spec.ts` previously used `CSS.escape(row.id)` inside
 * the Node-side test body to build its locator. `CSS` is a browser-only
 * global — every per-control row test crashed with
 * `ReferenceError: CSS is not defined` before any assertion ran.
 *
 * These tests pin the selector construction to a Node-safe helper. If
 * anyone ever swaps `buildIdSelector` back to a browser-only API, the
 * first test below will throw the same `ReferenceError` in pure Node
 * (no browser involved) and fail loudly here long before the harness
 * loses 35 row tests again.
 *
 * Why a `.test.ts` filename: Playwright's default `testMatch` glob covers
 * both `*.spec.ts` and `*.test.ts`. Using `.test.ts` keeps it visibly
 * distinct from the behavioural specs while still being discovered by
 * `npm test`. None of these tests start a browser context — they assert
 * pure-function behaviour.
 */

test.describe("buildIdSelector (Node-safe)", () => {
  test("runs in Node without ReferenceError (regression for #20)", () => {
    // The fix only matters if this synchronous call does not throw.
    // The previous implementation referenced `CSS.escape(...)` which is
    // undefined in Node and threw before returning anything.
    expect(() => buildIdSelector("download-btn")).not.toThrow();
  });

  test("plain alphanumeric ids round-trip into [id=\"…\"] form", () => {
    expect(buildIdSelector("download-btn")).toBe('[id="download-btn"]');
    expect(buildIdSelector("tab-cues")).toBe('[id="tab-cues"]');
    expect(buildIdSelector("app-status")).toBe('[id="app-status"]');
  });

  test("escapes embedded double-quote and backslash so the attribute selector stays valid", () => {
    // Defensive — no current inventory id contains these, but the helper
    // must not produce a syntactically broken selector if one ever does.
    expect(buildIdSelector('weird"id')).toBe('[id="weird\\"id"]');
    expect(buildIdSelector("back\\slash")).toBe('[id="back\\\\slash"]');
  });

  test("does NOT reference the browser-only `CSS` global", () => {
    // Belt-and-braces: verify that even when we explicitly null out
    // `CSS` in this Node test context, the helper still works. If
    // anyone re-introduces `CSS.escape` here, this test will throw the
    // exact same ReferenceError the original bug produced.
    const g = globalThis as { CSS?: unknown };
    const prev = g.CSS;
    try {
      g.CSS = undefined;
      expect(() => buildIdSelector("hello-world")).not.toThrow();
    } finally {
      g.CSS = prev;
    }
  });
});
