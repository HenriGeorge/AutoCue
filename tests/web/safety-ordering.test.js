import { describe, it, expect } from "vitest";
import * as fs from "node:fs";
import * as path from "node:path";
import { fileURLToPath } from "node:url";

/**
 * Regression guard for issue #119.
 *
 * BEFORE: `safety.spec.ts` was discovered AFTER `per-control-sweep.spec.ts`
 * in alphabetical order, and `globalTimeout` was 300_000 ms (5 min). The
 * per-control sweep alone runs ~116 row-tests at ~10-15s each (20-29 min),
 * so the globalTimeout fired before the safety spec ever started — the
 * load-bearing sandbox-DB guard was silently being skipped.
 *
 * AFTER: the safety spec is renamed to `0-safety.spec.ts` so it sorts
 * first in Playwright's alphabetical spec discovery, and `globalTimeout`
 * is bumped to 1_800_000 ms (30 min) so a full sweep completes.
 *
 * These pure-Node assertions catch any regression that would either:
 *   (a) rename the safety spec back to something that sorts AFTER
 *       per-control-sweep, or
 *   (b) drop globalTimeout back below what a full sweep needs.
 *
 * Lives in tests/web/ (not tests/e2e/) because vitest is the leg that runs
 * pure-Node helpers; the Playwright e2e leg is for browser-driven specs.
 */

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const E2E_DIR = path.resolve(__dirname, "..", "e2e");

describe("issue #119 — safety spec ordering and globalTimeout", () => {
  it("a safety preflight spec exists in tests/e2e/", () => {
    const candidates = fs
      .readdirSync(E2E_DIR)
      .filter((f) => /safety\.spec\.ts$/.test(f));
    expect(
      candidates,
      "no *safety*.spec.ts file found in tests/e2e/",
    ).not.toEqual([]);
  });

  it("safety spec sorts BEFORE per-control-sweep.spec.ts (alphabetical discovery)", () => {
    // Playwright discovers specs in alphabetical filename order within a
    // project. The safety spec MUST come first so it runs before the
    // long-running per-control sweep can exhaust globalTimeout.
    const specs = fs
      .readdirSync(E2E_DIR)
      .filter((f) => f.endsWith(".spec.ts"))
      .sort();

    const safetyIdx = specs.findIndex((f) => /safety\.spec\.ts$/.test(f));
    const sweepIdx = specs.findIndex((f) => f === "per-control-sweep.spec.ts");

    expect(safetyIdx, "safety spec not found in spec list").toBeGreaterThanOrEqual(0);
    expect(sweepIdx, "per-control-sweep.spec.ts not found").toBeGreaterThanOrEqual(0);
    expect(
      safetyIdx,
      `safety spec must sort before 'per-control-sweep.spec.ts' (sorted indexes ${safetyIdx} vs ${sweepIdx})`,
    ).toBeLessThan(sweepIdx);
  });

  it("safety spec is the LITERAL first spec discovered", () => {
    const specs = fs
      .readdirSync(E2E_DIR)
      .filter((f) => f.endsWith(".spec.ts"))
      .sort();
    expect(specs.length, "no spec files found").toBeGreaterThan(0);
    expect(
      specs[0],
      `the first spec discovered must be the safety preflight; got '${specs[0]}'`,
    ).toMatch(/safety\.spec\.ts$/);
  });

  it("playwright.config.ts globalTimeout is at least 25 minutes", () => {
    // Per-control sweep budget: ~116 rows × ~10-15s = 20-29 min. We
    // assert ≥ 25 min (1_500_000 ms) so any reduction toward the broken
    // 5-min budget fails this guard loudly. Bumping above 30 min is fine
    // (the upper bound is "before-CI-job-timeout"), bumping below 25 min
    // is the regression we're protecting against.
    const cfg = fs.readFileSync(
      path.join(E2E_DIR, "playwright.config.ts"),
      "utf8",
    );
    const match = cfg.match(/globalTimeout\s*:\s*([0-9_]+)/);
    expect(match, "globalTimeout not found in playwright.config.ts").not.toBeNull();
    const raw = match[1].replace(/_/g, "");
    const value = Number(raw);
    expect(Number.isFinite(value), `globalTimeout parsed as NaN from '${match[1]}'`).toBe(true);
    expect(
      value,
      `globalTimeout=${value}ms is too small for a full per-control sweep (~20-29 min); must be ≥ 1_500_000`,
    ).toBeGreaterThanOrEqual(1_500_000);
  });
});
