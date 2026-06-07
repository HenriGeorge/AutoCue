import { defineConfig, devices } from "@playwright/test";

/**
 * AutoCue QA harness config.
 *
 * Ports and the sandbox DB path are resolved in `globalSetup.ts`, which
 * writes the chosen values to env vars that this config (and the spec
 * files) read. Never hard-code 7432 here — that's the production port.
 *
 * SAFETY: globalSetup verifies via `/api/status` (with the
 * `X-AutoCue-Diagnostic: 1` header) that the server is bound to a sandbox
 * copy of master.db, NOT the user's real library. If verification fails,
 * the entire run aborts before any spec executes.
 */

const PORT = Number(process.env.AUTOCUE_PORT) || 0;
const PAGES_PORT = Number(process.env.AUTOCUE_PAGES_PORT) || 0;
const SANDBOX_DB = process.env.AUTOCUE_SANDBOX_DB || "";

if (!PORT || !PAGES_PORT || !SANDBOX_DB) {
  // These get filled in by globalSetup. If they're empty when a worker
  // imports this file directly (rare), fail loud rather than silently
  // hitting the real server.
  // eslint-disable-next-line no-console
  console.warn(
    "[autocue-qa] PORT / PAGES_PORT / SANDBOX_DB unset — globalSetup will populate them.",
  );
}

export default defineConfig({
  testDir: ".",
  globalSetup: "./globalSetup.ts",
  globalTeardown: "./globalTeardown.ts",
  globalTimeout: 300_000, // 5 min for full suite (covers slow DB copy / first scan)
  fullyParallel: false, // single shared sandbox DB; serial keeps state predictable
  forbidOnly: !!process.env.CI,
  retries: 0,
  workers: 1,
  reporter: [
    ["list"],
    ["json", { outputFile: "results.json" }],
    ["html", { open: "never" }],
  ],
  use: {
    baseURL: `http://localhost:${PORT}`,
    screenshot: "only-on-failure",
    video: "retain-on-failure",
    trace: "retain-on-failure",
  },
  webServer: [
    {
      // Local mode — `autocue serve` against the sandbox DB.
      command: `autocue serve --no-browser --port ${PORT} --db-path ${SANDBOX_DB}`,
      url: `http://localhost:${PORT}/api/status`,
      timeout: 60_000,
      reuseExistingServer: false, // always fresh, never hijack the user's running server
    },
    {
      // Pages mode — static serve of docs/ via Python stdlib (no npm dep).
      command: `python3 -m http.server ${PAGES_PORT} --directory ../../docs`,
      url: `http://localhost:${PAGES_PORT}/index.html`,
      timeout: 30_000,
      reuseExistingServer: false,
    },
  ],
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
});
