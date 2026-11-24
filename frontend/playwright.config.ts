/**
 * The browser tests, in browser-tests/ (CONTRIBUTING.md, Running the checks).
 * WebKit runs in CI, and wherever PLAYWRIGHT_WEBKIT=1: on this machine, or
 * in Playwright's container when PLAYWRIGHT_WEBKIT_SERVER names its server
 * (mise run browser-webkit).
 */
import { execFileSync } from "node:child_process";
import { tmpdir } from "node:os";
import { delimiter, join } from "node:path";
import { defineConfig, type Project } from "@playwright/test";
import {
  adminState,
  baseOf,
  inRun,
  SERVERS,
  type Server,
  serverEnv,
} from "./browser-tests/prepared";

// Each server's folder (backend/tests/helpers/browser_database.py) and
// port, chosen once, here in the main process, for the backends and the
// workers to inherit (browser-tests/prepared.ts).
process.env.BROWSER_RUN ??= join(tmpdir(), `inventory-browser-${process.pid}`);
process.env.BROWSER_CRAWL_RUN ??= join(
  tmpdir(),
  `inventory-browser-${process.pid}-crawl`,
);
if (!process.env.BROWSER_PORT || !process.env.BROWSER_CRAWL_PORT) {
  // Three, so that two are left besides a port set already, which the
  // system may hand out again while nothing listens on it.
  const preset = [process.env.BROWSER_PORT, process.env.BROWSER_CRAWL_PORT];
  const [port, crawlPort] = freePorts(3).filter(
    (each) => !preset.includes(each),
  );
  process.env.BROWSER_PORT ||= port;
  process.env.BROWSER_CRAWL_PORT ||= crawlPort;
}
// A phone's width, the narrowest the pages are made for.
const phone = {
  viewport: { width: 360, height: 740 },
  isMobile: true,
  hasTouch: true,
};
const webkit = Boolean(process.env.CI) || process.env.PLAYWRIGHT_WEBKIT === "1";
const webkitServer = process.env.PLAYWRIGHT_WEBKIT_SERVER;
// The pages that follow every link (phone-width.spec.ts).
const CRAWL = /phone-width\.spec\.ts/;
// Each browser, at a server, with the administrator's session signed in
// there once (browser-tests/sign-in.setup.ts).
const browsers = (
  server: Server,
): { name: string; use: NonNullable<Project["use"]> }[] => [
  {
    name: "chromium",
    use: {
      browserName: "chromium",
      baseURL: baseOf(server),
      storageState: adminState(server),
      // Full Chromium, headless: Playwright's lighter headless shell has
      // no camera, fake or not.
      channel: "chromium",
      permissions: ["camera"],
      launchOptions: {
        args: [
          "--use-fake-device-for-media-stream",
          // A label's QR code (browser_database.py).
          `--use-file-for-fake-video-capture=${inRun("camera.y4m", server)}`,
        ],
      },
    },
  },
  ...(webkit
    ? [
        {
          name: "webkit",
          use: {
            browserName: "webkit" as const,
            baseURL: baseOf(server),
            storageState: adminState(server),
            // In the container, the app on this machine's 127.0.0.1 is
            // reached through this connection.
            ...(webkitServer
              ? {
                  connectOptions: {
                    wsEndpoint: webkitServer,
                    exposeNetwork: "<loopback>",
                  },
                }
              : {}),
          },
        },
      ]
    : []),
];

export default defineConfig({
  testDir: "browser-tests",
  // Each server a database of its own, seeded, and the backend serving the
  // build from it (backend/tests/helpers/browser_database.py).
  webServer: SERVERS.map((server) => ({
    name: server,
    command: "uv run python -m helpers.browser_database serve",
    cwd: "../backend",
    // pytest's pythonpath (backend/pyproject.toml), so it imports the seed
    // from backend/devtools/ and the tests' helpers (CONTRIBUTING.md).
    env: { PYTHONPATH: [".", "tests"].join(delimiter), ...serverEnv(server) },
    // Its log lines, a traceback among them, with the tests'.
    stdout: "pipe" as const,
    url: `${baseOf(server)}/api/healthz`,
    timeout: 60_000,
    gracefulShutdown: { signal: "SIGTERM" as const, timeout: 10_000 },
  })),
  forbidOnly: Boolean(process.env.CI),
  reporter: process.env.CI ? "github" : "list",
  use: phone,
  projects: [
    {
      name: "sign-in",
      testMatch: /sign-in\.setup\.ts/,
      use: { browserName: "chromium", channel: "chromium" },
    },
    // The crawl reads every row there is, so it has a server of its own,
    // whose rows no test changes (browser-tests/prepared.ts).
    ...browsers("crawl").map(({ name, use }) => ({
      name: `${name} crawl`,
      testMatch: CRAWL,
      dependencies: ["sign-in"],
      use,
    })),
    ...browsers("shared").map(({ name, use }) => ({
      name,
      testIgnore: CRAWL,
      dependencies: ["sign-in"],
      use,
    })),
  ],
});

/**
 * Ports nothing listens on now, from the system, all held open together so
 * no two are the same. The config is read synchronously, so another Node
 * process asks for them.
 */
function freePorts(count: number): string[] {
  return execFileSync(
    process.execPath,
    [
      "-e",
      `const net = require("node:net");
       const servers = Array.from({ length: ${count} }, () => net.createServer());
       Promise.all(
         servers.map((s) => new Promise((done) => s.listen(0, "127.0.0.1", done))),
       ).then(() => {
         process.stdout.write(servers.map((s) => s.address().port).join(" "));
         for (const s of servers) s.close();
       });`,
    ],
    { encoding: "utf8" },
  ).split(" ");
}
