/**
 * The browser tests, in browser/ (CONTRIBUTING.md, Running the checks).
 * WebKit runs in CI, and wherever PLAYWRIGHT_WEBKIT=1.
 */
import { execFileSync } from "node:child_process";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { defineConfig } from "@playwright/test";
import { adminState, inRun } from "./browser/prepared";

// This run's folder (backend/tests/browser_database.py) and port, chosen
// once, here in the main process, for the backend and the workers to inherit.
process.env.BROWSER_RUN ??= join(tmpdir(), `inventory-browser-${process.pid}`);
process.env.BROWSER_PORT ??= freePort();
const base = `http://127.0.0.1:${process.env.BROWSER_PORT}`;
// The administrator's session, signed in once for every test: signing in
// again for each would meet the sign-in rate limit.
const signedIn = adminState();

// A phone's width, the narrowest the pages are made for.
const phone = {
  viewport: { width: 360, height: 740 },
  isMobile: true,
  hasTouch: true,
};
const webkit = Boolean(process.env.CI) || process.env.PLAYWRIGHT_WEBKIT === "1";

export default defineConfig({
  testDir: "browser",
  // A database of its own, seeded, and the backend serving the build from it
  // (backend/tests/browser_database.py).
  webServer: {
    command: "uv run python tests/browser_database.py serve",
    cwd: "../backend",
    // Its log lines, a traceback among them, with the tests'.
    stdout: "pipe",
    url: `${base}/api/healthz`,
    timeout: 60_000,
    gracefulShutdown: { signal: "SIGTERM", timeout: 10_000 },
  },
  forbidOnly: Boolean(process.env.CI),
  reporter: process.env.CI ? "github" : "list",
  use: {
    baseURL: base,
    ...phone,
  },
  projects: [
    {
      name: "sign-in",
      testMatch: /sign-in\.setup\.ts/,
      use: { browserName: "chromium", channel: "chromium" },
    },
    {
      name: "chromium",
      dependencies: ["sign-in"],
      use: {
        browserName: "chromium",
        storageState: signedIn,
        // Full Chromium, headless: Playwright's lighter headless shell has
        // no camera, fake or not.
        channel: "chromium",
        permissions: ["camera"],
        launchOptions: {
          args: [
            "--use-fake-device-for-media-stream",
            // A label's QR code (browser_database.py).
            `--use-file-for-fake-video-capture=${inRun("camera.y4m")}`,
          ],
        },
      },
    },
    ...(webkit
      ? [
          {
            name: "webkit",
            dependencies: ["sign-in"],
            use: {
              browserName: "webkit" as const,
              storageState: signedIn,
            },
          },
        ]
      : []),
  ],
});

/**
 * A port nothing listens on now, from the system. The config is read
 * synchronously, so another Node process asks for it.
 */
function freePort(): string {
  return execFileSync(
    process.execPath,
    [
      "-e",
      `const s = require("node:net").createServer();
       s.listen(0, "127.0.0.1", () => {
         process.stdout.write(String(s.address().port));
         s.close();
       });`,
    ],
    { encoding: "utf8" },
  );
}
