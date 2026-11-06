/**
 * The browser tests, in browser/ (CONTRIBUTING.md, Running the checks).
 * WebKit runs in CI, and wherever PLAYWRIGHT_WEBKIT=1.
 */
import { tmpdir } from "node:os";
import { join } from "node:path";
import { defineConfig } from "@playwright/test";

// What Chromium's camera shows: a label's QR code, which global-setup.ts
// writes. Named once, here in the main process, for the workers to inherit.
process.env.CAMERA_VIDEO ??= join(
  tmpdir(),
  `inventory-camera-${process.pid}.y4m`,
);

// The administrator's session, signed in once (sign-in.setup.ts) for every
// test: signing in again for each would meet the sign-in rate limit.
process.env.ADMIN_STATE ??= join(
  tmpdir(),
  `inventory-admin-${process.pid}.json`,
);

// A phone's width, the narrowest the pages are made for.
const phone = {
  viewport: { width: 360, height: 740 },
  isMobile: true,
  hasTouch: true,
};
const webkit = Boolean(process.env.CI) || process.env.PLAYWRIGHT_WEBKIT === "1";

export default defineConfig({
  testDir: "browser",
  globalSetup: "./browser/global-setup.ts",
  forbidOnly: Boolean(process.env.CI),
  reporter: process.env.CI ? "github" : "list",
  use: {
    // Where global-setup.ts started the backend: the workers read it.
    baseURL: process.env.BROWSER_BASE_URL,
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
        storageState: process.env.ADMIN_STATE,
        // Full Chromium, headless: Playwright's lighter headless shell has
        // no camera, fake or not.
        channel: "chromium",
        permissions: ["camera"],
        launchOptions: {
          args: [
            "--use-fake-device-for-media-stream",
            `--use-file-for-fake-video-capture=${process.env.CAMERA_VIDEO}`,
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
              storageState: process.env.ADMIN_STATE,
            },
          },
        ]
      : []),
  ],
});
