import * as fs from "node:fs";

export default async function globalTeardown() {
  const dir = process.env.AUTOCUE_SANDBOX_DIR;
  if (dir && fs.existsSync(dir)) {
    fs.rmSync(dir, { recursive: true, force: true });
  }
}
