import { test, expect } from "@playwright/test";
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";

/**
 * SAFETY PREFLIGHT. Runs first. Aborts the whole run if the server is
 * pointed at anything other than the sandbox copy of master.db.
 *
 * If anything in here fails, do NOT add `.skip` to make it pass. Find the
 * misconfiguration and fix it. The safety check exists because AutoCue
 * writes directly to master.db on the Apply path.
 */

test.describe("safety preflight", () => {
  test("server reports the sandbox DB path via diagnostic header", async ({
    request,
  }) => {
    const r = await request.get("/api/status", {
      headers: { "X-AutoCue-Diagnostic": "1" },
    });
    expect(r.ok(), "/api/status failed").toBeTruthy();
    const body = await r.json();
    expect(body, "/api/status returned no db_path").toHaveProperty("db_path");
    expect(typeof body.db_path).toBe("string");

    const sandbox = process.env.AUTOCUE_SANDBOX_DB || "";
    expect(sandbox, "AUTOCUE_SANDBOX_DB not set").not.toBe("");

    const serverPath = fs.realpathSync(body.db_path);
    const sandboxPath = fs.realpathSync(sandbox);
    expect(serverPath, "server is not bound to the sandbox DB").toBe(
      sandboxPath,
    );
  });

  test("sandbox DB is not under the real Rekordbox library", async ({
    request,
  }) => {
    const r = await request.get("/api/status", {
      headers: { "X-AutoCue-Diagnostic": "1" },
    });
    const body = await r.json();
    const serverPath: string = body.db_path;
    const forbiddenPrefix = path.join(os.homedir(), "Library", "Pioneer");
    expect(
      serverPath.startsWith(forbiddenPrefix),
      `server is bound to a path under ${forbiddenPrefix} — refusing to run`,
    ).toBeFalsy();
  });

  test("diagnostic field is NOT exposed without the header", async ({
    request,
  }) => {
    const r = await request.get("/api/status");
    const body = await r.json();
    expect(body.db_path ?? null).toBeNull();
  });
});
