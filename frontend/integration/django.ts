import { execFileSync } from "node:child_process";
import { fileURLToPath } from "node:url";

/**
 * Running the backend's own Python, from the suite.
 *
 * Three files need it for three different reasons -- the scene is migrated and
 * seeded before anything runs, a one-time password is computed the way an
 * authenticator app would, and a label is minted for the camera to look at --
 * and all three want the same thing: this repository's backend environment,
 * from this repository's backend directory. Written once so that a change to
 * how the backend is invoked is one edit rather than three that can disagree.
 *
 * Synchronous on purpose. Every caller is setup rather than assertion, and a
 * subprocess is cheap next to starting a browser.
 */

/** The backend, found from this file rather than from the working directory. */
const BACKEND = fileURLToPath(new URL("../../backend", import.meta.url));

/** The backend's Python, with whatever arguments. */
export function uv(...args: string[]): string {
  return execFileSync("uv", ["run", "python", ...args], { cwd: BACKEND, encoding: "utf8" });
}

/** One `manage.py` command, answered with whatever it printed. */
export function manage(...args: string[]): string {
  return uv("src/manage.py", ...args);
}
