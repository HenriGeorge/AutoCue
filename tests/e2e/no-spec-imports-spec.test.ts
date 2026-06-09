import { test, expect } from "@playwright/test";
import * as fs from "node:fs";
import * as path from "node:path";
import { fileURLToPath } from "node:url";

/**
 * Regression guard for issue #112.
 *
 * Playwright aborts the entire test run during discovery if any test
 * file imports another test file:
 *
 *   Error: test file "X.test.ts" should not import test file "Y.spec.ts"
 *
 * When this happens, the safety preflight (`safety.spec.ts`) never
 * runs and the documented `npm test` entry point silently disables
 * the sandbox-DB guard. This is a critical failure mode — it disables
 * the entire safety net.
 *
 * This test scans every sibling `*.spec.ts` / `*.test.ts` file for
 * imports that resolve to another test file. If it finds one, it
 * fails with a precise file:line reference so the next contributor
 * can extract the shared symbol into a non-test helper module
 * (sibling pattern: `<name>.helpers.ts`).
 *
 * Detection runs in pure Node — no browser, no fixture cost. The
 * regression guard fails BEFORE Playwright's own discovery aborts,
 * giving a clearer error.
 */

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

function isTestFile(name: string): boolean {
  return name.endsWith(".spec.ts") || name.endsWith(".test.ts");
}

function resolveImport(fromFile: string, spec: string): string | null {
  // Only resolve relative imports — third-party packages can't be test files.
  if (!spec.startsWith(".")) return null;
  const fromDir = path.dirname(fromFile);
  const base = path.resolve(fromDir, spec);
  // Try common TS resolutions in order.
  const candidates = [
    base,
    base + ".ts",
    base + ".tsx",
    base + ".spec.ts",
    base + ".test.ts",
    path.join(base, "index.ts"),
  ];
  for (const c of candidates) {
    try {
      if (fs.statSync(c).isFile()) return c;
    } catch {
      // not a file — keep trying
    }
  }
  return null;
}

test.describe("test-file-discovery hygiene", () => {
  test("no test file imports another test file (regression for #112)", () => {
    const dir = __dirname;
    const entries = fs.readdirSync(dir);
    const offenders: Array<{ from: string; to: string; line: number }> = [];

    // Match `import ... from "..."` and `import "..."` forms.
    const importRe = /^\s*import\s+(?:[^"']*?from\s+)?["']([^"']+)["']/gm;

    for (const entry of entries) {
      if (!isTestFile(entry)) continue;
      const full = path.join(dir, entry);
      const src = fs.readFileSync(full, "utf8");
      const lines = src.split(/\r?\n/);
      for (let i = 0; i < lines.length; i++) {
        importRe.lastIndex = 0;
        const m = importRe.exec(lines[i] + "\n");
        if (!m) continue;
        const target = resolveImport(full, m[1]);
        if (!target) continue;
        if (isTestFile(path.basename(target))) {
          offenders.push({
            from: path.relative(dir, full),
            to: path.relative(dir, target),
            line: i + 1,
          });
        }
      }
    }

    expect(
      offenders,
      offenders.length
        ? `Test files must not import other test files (Playwright aborts discovery). ` +
            `Extract shared symbols into a sibling \`<name>.helpers.ts\` module. ` +
            `Offenders:\n` +
            offenders
              .map((o) => `  ${o.from}:${o.line} imports ${o.to}`)
              .join("\n")
        : "",
    ).toEqual([]);
  });
});
