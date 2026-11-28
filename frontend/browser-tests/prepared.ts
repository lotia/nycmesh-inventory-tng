/**
 * The run's servers (playwright.config.ts), each with its folder and port
 * (backend/tests/helpers/browser_database.py), and what the backend
 * prepared there for the tests: read when a test runs, as it is written
 * once the backend starts.
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
  /** An item recorded more than moved, whose Correct is always refused:
   * the place it came from is retired and holds nothing. */
  refused: string;
};

/**
 * Each server and its database: "shared" by every test but the crawl, and
 * the "crawl"'s own (phone-width.spec.ts), which reads every row there is,
 * so no test changes one under it.
 */
export const SERVERS = ["shared", "crawl"] as const;
export type Server = (typeof SERVERS)[number];

/** Where each server's folder and port are, set by playwright.config.ts. */
const ENV: Record<Server, { run: string; port: string }> = {
  shared: { run: "BROWSER_RUN", port: "BROWSER_PORT" },
  crawl: { run: "BROWSER_CRAWL_RUN", port: "BROWSER_CRAWL_PORT" },
};

/** A server's folder and port, as the backend reads them. */
export function serverEnv(server: Server): {
  BROWSER_RUN: string;
  BROWSER_PORT: string;
} {
  return {
    BROWSER_RUN: process.env[ENV[server].run] ?? "",
    BROWSER_PORT: process.env[ENV[server].port] ?? "",
  };
}

/** A server's address. */
export function baseOf(server: Server): string {
  return `http://127.0.0.1:${serverEnv(server).BROWSER_PORT}`;
}

/** A file in a server's folder. */
export function inRun(name: string, server: Server = "shared"): string {
  return join(serverEnv(server).BROWSER_RUN, name);
}

/** The administrator's session there, signed in once (sign-in.setup.ts). */
export function adminState(server: Server = "shared"): string {
  return inRun("admin.json", server);
}

const read: Partial<Record<Server, Prepared>> = {};

export function prepared(server: Server = "shared"): Prepared {
  read[server] ??= JSON.parse(
    readFileSync(inRun("prepared.json", server), "utf8"),
  ) as Prepared;
  return read[server];
}
