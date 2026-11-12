/**
 * The run's folder, BROWSER_RUN (backend/tests/browser_database.py), and
 * what the backend prepared there for the tests: read when a test runs, as
 * it is written once the backend starts.
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";

export type Prepared = {
  username: string;
  password: string;
  phrase: string;
  labelled: string;
  /** Another administrator, with the same password. */
  other: string;
};

/** A file in this run's folder. */
export function inRun(name: string): string {
  return join(process.env.BROWSER_RUN ?? "", name);
}

/** The administrator's session, signed in once (sign-in.setup.ts). */
export function adminState(): string {
  return inRun("admin.json");
}

let read: Prepared | undefined;

export function prepared(): Prepared {
  read ??= JSON.parse(readFileSync(inRun("prepared.json"), "utf8")) as Prepared;
  return read;
}
