import { test, expect } from "@playwright/test";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { buildIdSelector } from "./per-control-sweep.helpers";

const HERE = dirname(fileURLToPath(import.meta.url));

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

/**
 * Regression guard for issue #112.
 *
 * Playwright's runner refuses to discover a test file that imports
 * another test file:
 *   `test file "X.test.ts" should not import test file "Y.spec.ts"`
 * — and aborts the ENTIRE run during discovery (including the
 * `safety.spec.ts` preflight). Until #112 was fixed, this file
 * imported `buildIdSelector` from `./per-control-sweep.spec`, which
 * disabled `npm test` for every user and CI.
 *
 * This test reads its own source plus the sibling spec source and
 * fails if either re-introduces an import path pointing at a sibling
 * `.spec` / `.test` file. Pure-string assertion — runs in Node, no
 * browser context required.
 */
test.describe("e2e test-discovery invariant (regression for #112)", () => {
  const SELF = readFileSync(
    join(HERE, "per-control-sweep.selector.test.ts"),
    "utf8",
  );
  const SPEC = readFileSync(
    join(HERE, "per-control-sweep.spec.ts"),
    "utf8",
  );

  // Matches a real ES-module `import … from "./foo.spec"` (or .test, with
  // or without an explicit `.ts` extension) anchored at the start of a
  // line. Anchoring at line start avoids false positives where the same
  // text appears inside a string literal, comment, or error message
  // (this very file mentions `./foo.spec` in a doc string). The
  // forbidden shape is any sibling test file — `./foo.helpers` and
  // other non-spec siblings are intentionally allowed through.
  const FORBIDDEN_IMPORT =
    /^\s*import\b[^;]*?from\s+["']\.\/[^"']+\.(spec|test)(\.ts)?["']/m;

  test("selector.test.ts does not import a sibling spec/test file", () => {
    const match = SELF.match(FORBIDDEN_IMPORT);
    expect(
      match,
      `selector.test.ts must not import a sibling .spec/.test file ` +
        `(would break Playwright test discovery — issue #112). ` +
        `Offending import: ${match?.[0]}`,
    ).toBeNull();
  });

  test("per-control-sweep.spec.ts does not import a sibling spec/test file", () => {
    const match = SPEC.match(FORBIDDEN_IMPORT);
    expect(
      match,
      `per-control-sweep.spec.ts must not import a sibling .spec/.test file ` +
        `(would break Playwright test discovery — issue #112). ` +
        `Offending import: ${match?.[0]}`,
    ).toBeNull();
  });

  test("regex DOES match a real import-from-sibling-spec line (self-check)", () => {
    // Sanity check that the guard regex actually catches the pattern it
    // claims to. Without this, a future regex regression could let the
    // bug slip through silently with all guard tests still passing.
    const realImport = `import { foo } from "./per-control-sweep.spec";\n`;
    expect(FORBIDDEN_IMPORT.test(realImport)).toBe(true);

    const realTestImport = `import { foo } from "./bar.test.ts";\n`;
    expect(FORBIDDEN_IMPORT.test(realTestImport)).toBe(true);

    const helperImport = `import { foo } from "./per-control-sweep.helpers";\n`;
    expect(FORBIDDEN_IMPORT.test(helperImport)).toBe(false);
  });
});
