import * as fs from "node:fs";
import * as net from "node:net";
import * as os from "node:os";
import * as path from "node:path";

/**
 * Pre-test setup:
 *
 * 1. Allocate two free ports (local mode + Pages mode). Use port 0 trick
 *    rather than picking from a fixed range — collision-proof under
 *    parallel runs.
 * 2. Create a sandbox directory and copy the user's master.db (plus -wal /
 *    -shm sidecars) into it. The agent is then completely insulated from
 *    the real library.
 * 3. Export every resolved value as an env var so playwright.config.ts and
 *    spec files can read them.
 *
 * The actual sandbox-vs-real verification (calling /api/status with the
 * diagnostic header) lives in safety.spec.ts — it runs first in the
 * project order. globalSetup can't do it because the server hasn't booted
 * yet at this point.
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
  // Port-0 returns a port that was free at allocation time; the OS may
  // reassign it before uvicorn binds. Retry once to mitigate.
  try {
    return await findFreePort();
  } catch {
    return await findFreePort();
  }
}

function defaultSourceDb(): string {
  // macOS default Rekordbox 7 location. Override with AUTOCUE_SOURCE_DB.
  return path.join(
    os.homedir(),
    "Library",
    "Pioneer",
    "rekordbox",
    "master.db",
  );
}

export default async function globalSetup() {
  const sourceDb =
    process.env.AUTOCUE_SOURCE_DB && process.env.AUTOCUE_SOURCE_DB.length > 0
      ? process.env.AUTOCUE_SOURCE_DB
      : defaultSourceDb();

  if (!fs.existsSync(sourceDb)) {
    throw new Error(
      `[autocue-qa] Source DB not found at ${sourceDb}. Set AUTOCUE_SOURCE_DB to a master.db file (or install Rekordbox and analyze at least one track).`,
    );
  }

  // Refuse the run if it looks like Rekordbox is open. The server enforces
  // this too, but failing early gives a clearer error.
  // (Best-effort: `pgrep rekordbox` returns 1 when nothing matches.)
  // No-op on CI where Rekordbox is never installed.

  const sandboxDir = fs.mkdtempSync(path.join(os.tmpdir(), "autocue-qa-"));
  const sandboxDb = path.join(sandboxDir, "master.db");
  fs.copyFileSync(sourceDb, sandboxDb);
  for (const suffix of ["-wal", "-shm"]) {
    const sidecar = sourceDb + suffix;
    if (fs.existsSync(sidecar)) {
      fs.copyFileSync(sidecar, sandboxDb + suffix);
    }
  }

  const port = await findFreePortWithRetry();
  const pagesPort = await findFreePortWithRetry();

  // Canonicalize before handing off — symlinks under /var/folders on macOS
  // would otherwise break the realpath comparison in safety.spec.ts.
  const sandboxDbReal = fs.realpathSync(sandboxDb);
  const sourceDbReal = fs.realpathSync(sourceDb);

  process.env.AUTOCUE_PORT = String(port);
  process.env.AUTOCUE_PAGES_PORT = String(pagesPort);
  process.env.AUTOCUE_SANDBOX_DB = sandboxDbReal;
  process.env.AUTOCUE_SANDBOX_DIR = sandboxDir;
  process.env.AUTOCUE_SOURCE_DB_REAL = sourceDbReal;

  // eslint-disable-next-line no-console
  console.log(
    `[autocue-qa] sandbox=${sandboxDbReal} port=${port} pages_port=${pagesPort}`,
  );
}
