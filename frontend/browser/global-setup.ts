/**
 * Before the browser tests: a database of their own, seeded
 * (backend/tests/browser_database.py), and the backend serving the built
 * frontend from it, on a port no other server holds. Afterwards both go.
 * What the tests need is passed on in the environment, which their workers
 * inherit; playwright.config.ts reads BROWSER_BASE_URL from it too.
 */
import { execFileSync, spawn } from "node:child_process";
import { rmSync } from "node:fs";
import { createServer } from "node:net";
import { fileURLToPath } from "node:url";

const BACKEND = fileURLToPath(new URL("../../backend/", import.meta.url));
const HOST = "127.0.0.1";

type Prepared = {
  database_url: string;
  username: string;
  password: string;
  phrase: string;
  labelled: string;
};

function database(...args: string[]): string {
  return execFileSync(
    "uv",
    ["run", "--quiet", "python", "tests/browser_database.py", ...args],
    { cwd: BACKEND, encoding: "utf8", stdio: ["ignore", "pipe", "inherit"] },
  );
}

export default async function globalSetup() {
  const port = await freePort();
  const video = process.env.CAMERA_VIDEO ?? "";
  const prepared: Prepared = JSON.parse(database("create", video));
  const cleanUp = () => {
    database("drop", prepared.database_url);
    rmSync(video, { force: true });
    rmSync(process.env.ADMIN_STATE ?? "", { force: true });
  };
  const base = `http://${HOST}:${port}`;
  process.env.BROWSER_BASE_URL = base;
  process.env.ADMIN_USERNAME = prepared.username;
  process.env.ADMIN_PASSWORD = prepared.password;
  process.env.REGISTRATION_PHRASE = prepared.phrase;
  process.env.LABELLED_ITEM = prepared.labelled;

  // In the run's own process group, so Ctrl-C stops it with the run; uv
  // passes a signal on to the server.
  const server = spawn(
    "uv",
    ["run", "python", "-m", "inventory_tng", "--host", HOST, "--port", port],
    {
      cwd: BACKEND,
      stdio: ["ignore", "inherit", "inherit"],
      env: {
        ...process.env,
        DATABASE_URL: prepared.database_url,
        APP_ENV: "development",
        // The build, as production serves it.
        VITE_DEV_SERVER: "",
        LOG_LEVEL: "WARNING",
      },
    },
  );
  const stopped = new Promise<string>((resolve) => {
    server.on("error", (error) => resolve(`did not start: ${error.message}`));
    server.on("exit", (code, signal) => resolve(`stopped: ${signal ?? code}`));
  });
  const teardown = async () => {
    server.kill();
    await stopped;
    cleanUp();
  };
  try {
    await healthy(new URL("/api/healthz", base), stopped);
  } catch (error) {
    await teardown();
    throw error;
  }
  return teardown;
}

/** A port nothing listens on now, from the system. */
function freePort(): Promise<string> {
  return new Promise((resolve, reject) => {
    const probe = createServer();
    probe.on("error", reject);
    probe.listen(0, HOST, () => {
      const address = probe.address();
      probe.close(() =>
        typeof address === "object" && address !== null
          ? resolve(String(address.port))
          : reject(new Error("no port")),
      );
    });
  });
}

/** Waits for the backend to answer, or fails if it stops first. */
async function healthy(url: URL, stopped: Promise<string>): Promise<void> {
  const answered = (async () => {
    const deadline = Date.now() + 30_000;
    while (Date.now() < deadline) {
      try {
        if ((await fetch(url)).ok) {
          return;
        }
      } catch {
        // Not listening yet.
      }
      await new Promise((resolve) => setTimeout(resolve, 200));
    }
    throw new Error(`the backend never answered at ${url}`);
  })();
  const died = stopped.then((why) => {
    throw new Error(`the backend ${why}`);
  });
  await Promise.race([answered, died]);
}
