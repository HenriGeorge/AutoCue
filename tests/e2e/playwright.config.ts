import { defineConfig, devices } from "@playwright/test";
import * as fs from "node:fs";
import * as net from "node:net";
import * as os from "node:os";
import * as path from "node:path";

/**
 * AutoCue QA harness config.
 *
 * Setup happens at CONFIG LOAD TIME — port allocation and sandbox-DB
 * creation must finish before Playwright interpolates `webServer.command`,
 * which it does the moment this module is evaluated. Doing it in
 * `globalSetup` (a separate file) is too late: by then the webServer
 * command string has already been frozen with empty env values.
 *
 * SAFETY: `0-safety.spec.ts` is named with the `0-` prefix so Playwright's
 * alphabetical file discovery runs it FIRST, before the per-control sweep
 * (which can take 20+ minutes for a full library). It verifies via
 * `/api/status` + `X-AutoCue-Diagnostic: 1` that the server is bound to the
 * sandbox copy. That spec is the load-bearing safety check — this file is
 * only responsible for putting the right files / ports in place.
 *
 * `globalTimeout` is sized to fit the full sweep (~116 controls × 10-15s ≈
 * 20-30 min). Bumping below that without splitting the sweep into its own
 * Playwright project will silently abort runs mid-sweep — see issue #119.
 */

async function findFreePort(): Promise<number> {
  return new Promise((resolve, reject) => {
    const srv = net.createServer();
    srv.unref();
    srv.on("error", reject);
    srv.listen(0, "127.0.0.1", () => {
      const addr = srv.address();
      if (typeof addr === "object" && addr) {
        const p = addr.port;
        srv.close(() => resolve(p));
      } else {
        reject(new Error("could not allocate port"));
      }
    });
  });
}

async function findFreePortWithRetry(): Promise<number> {
  try {
    return await findFreePort();
  } catch {
    return await findFreePort();
  }
}

function defaultSourceDb(): string {
  return path.join(
    os.homedir(),
    "Library",
    "Pioneer",
    "rekordbox",
    "master.db",
  );
}

// Playwright re-imports this config in each worker process. Without
// memoization, every import would allocate fresh ports + create a new
// sandbox copy — the launched webServer would be on port A while the
// worker imported port B and connected to nothing. Cache via env so
// child processes inherit the parent's allocation.
let PORT: number;
let PAGES_PORT: number;
let sandboxDbReal: string;
let sourceDbReal: string;
let sandboxDir: string;

if (
  process.env.AUTOCUE_PORT &&
  process.env.AUTOCUE_PAGES_PORT &&
  process.env.AUTOCUE_SANDBOX_DB &&
  process.env.AUTOCUE_SANDBOX_DIR
) {
  PORT = Number(process.env.AUTOCUE_PORT);
  PAGES_PORT = Number(process.env.AUTOCUE_PAGES_PORT);
  sandboxDbReal = process.env.AUTOCUE_SANDBOX_DB;
  sandboxDir = process.env.AUTOCUE_SANDBOX_DIR;
  sourceDbReal = process.env.AUTOCUE_SOURCE_DB_REAL ?? "";
} else {
  const sourceDb =
    process.env.AUTOCUE_SOURCE_DB && process.env.AUTOCUE_SOURCE_DB.length > 0
      ? process.env.AUTOCUE_SOURCE_DB
      : defaultSourceDb();

  if (!fs.existsSync(sourceDb)) {
    throw new Error(
      `[autocue-qa] Source DB not found at ${sourceDb}. Set AUTOCUE_SOURCE_DB to a master.db file (or install Rekordbox and analyze at least one track).`,
    );
  }

  sandboxDir = fs.mkdtempSync(path.join(os.tmpdir(), "autocue-qa-"));
  const sandboxDb = path.join(sandboxDir, "master.db");
  fs.copyFileSync(sourceDb, sandboxDb);
  for (const suffix of ["-wal", "-shm"]) {
    const sidecar = sourceDb + suffix;
    if (fs.existsSync(sidecar)) {
      fs.copyFileSync(sidecar, sandboxDb + suffix);
    }
  }

  sandboxDbReal = fs.realpathSync(sandboxDb);
  sourceDbReal = fs.realpathSync(sourceDb);
  PORT = await findFreePortWithRetry();
  PAGES_PORT = await findFreePortWithRetry();

  process.env.AUTOCUE_PORT = String(PORT);
  process.env.AUTOCUE_PAGES_PORT = String(PAGES_PORT);
  process.env.AUTOCUE_SANDBOX_DB = sandboxDbReal;
  process.env.AUTOCUE_SANDBOX_DIR = sandboxDir;
  process.env.AUTOCUE_SOURCE_DB_REAL = sourceDbReal;

  // eslint-disable-next-line no-console
  console.log(
    `[autocue-qa] sandbox=${sandboxDbReal} port=${PORT} pages_port=${PAGES_PORT}`,
  );
}

export default defineConfig({
  testDir: ".",
  globalTeardown: "./globalTeardown.ts",
  globalTimeout: 1_800_000,
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  retries: 0,
  workers: 1,
  reporter: [
    ["list"],
    ["json", { outputFile: "results.json" }],
    ["html", { open: "never" }],
  ],
  use: {
    baseURL: `http://127.0.0.1:${PORT}`,
    screenshot: "only-on-failure",
    video: "retain-on-failure",
    trace: "retain-on-failure",
  },
  webServer: [
    {
      // Use `python -m autocue.cli` with PYTHONPATH pinned to the
      // worktree root, so the harness exercises THIS checkout's autocue
      // source — not whatever a global `pip install -e .` happens to
      // resolve. Keeps test runs isolated from the user's parallel
      // editing in the main checkout.
      command: `PYTHONPATH=../.. python3 -m autocue serve --no-browser --port ${PORT} --db-path ${sandboxDbReal}`,
      url: `http://127.0.0.1:${PORT}/api/status`,
      timeout: 60_000,
      reuseExistingServer: false,
    },
    {
      command: `python3 -m http.server ${PAGES_PORT} --directory ../../docs`,
      url: `http://127.0.0.1:${PAGES_PORT}/index.html`,
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
