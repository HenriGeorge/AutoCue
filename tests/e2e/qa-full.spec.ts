import { test } from "@playwright/test";

/**
 * Full suite — write endpoints + every SSE. Gated by RUN_FULL=1.
 *
 * Stub for now. Extend incrementally as the agent encounters bugs that
 * need regression coverage. Every write-endpoint test in here MUST end
 * with a re-assertion of `safety.spec.ts`-style invariants (the server
 * is still bound to the sandbox DB) before declaring success.
 */

const RUN_FULL = process.env.RUN_FULL === "1";

test.describe("full suite (write endpoints + all SSE)", () => {
  test.skip(!RUN_FULL, "set RUN_FULL=1 to enable");

  test("placeholder — extend with write-endpoint coverage", async () => {
    // Intentionally empty. Add real tests as needed.
  });
});
