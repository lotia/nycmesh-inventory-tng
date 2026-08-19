import { execFileSync } from "node:child_process";
import { fileURLToPath } from "node:url";

/**
 * Migrate and seed before any test runs.
 *
 * Deliberately not part of the server's start command: Playwright reuses a
 * server that is already listening, and would then skip that command
 * entirely, leaving the suite to test whatever state a developer's database
 * happened to be in. This runs either way.
 */

const BACKEND = fileURLToPath(new URL("../../backend", import.meta.url));

function manage(...args: string[]) {
  execFileSync("uv", ["run", "python", "src/manage.py", ...args], {
    cwd: BACKEND,
    stdio: "inherit",
  });
}

export default function globalSetup() {
  manage("migrate", "--noinput");
  manage("seed_integration_data");
}
