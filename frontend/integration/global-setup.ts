import { execFileSync } from "node:child_process";
import { fileURLToPath } from "node:url";

/**
 * Migrate and seed before any test runs, and publish what was seeded.
 *
 * Deliberately not part of the server's start command: Playwright reuses a
 * server that is already listening, and would then skip that command
 * entirely, leaving the suite to test whatever state a developer's database
 * happened to be in. This runs either way.
 *
 * Everything the seed prints is republished as `SEEDED_<KEY>`, whatever the
 * keys are, so the scene is described in exactly one place -- the command
 * itself -- and adding to it needs no change here. The tests read those
 * values rather than assuming a fresh database hands out 1, or keeping their
 * own copy of a credential the command wrote.
 */

const BACKEND = fileURLToPath(new URL("../../backend", import.meta.url));

function manage(...args: string[]) {
  return execFileSync("uv", ["run", "python", "src/manage.py", ...args], {
    cwd: BACKEND,
    encoding: "utf8",
  });
}

export default function globalSetup() {
  manage("migrate", "--noinput");
  // The flag is the seed's own guard against being run somewhere it should
  // not be; the command explains what passing it acknowledges.
  const seeded: Record<string, string | number> = JSON.parse(
    manage("seed_integration_data", "--i-know-this-creates-a-published-login"),
  );
  for (const [key, value] of Object.entries(seeded)) {
    process.env[`SEEDED_${key.toUpperCase()}`] = String(value);
  }
}
