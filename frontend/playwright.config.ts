/**
 * The browser tests, in browser/ (CONTRIBUTING.md, Running the checks).
 * WebKit runs in CI, and wherever PLAYWRIGHT_WEBKIT=1.
 */
import { defineConfig } from "@playwright/test";

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
    { name: "chromium", use: { browserName: "chromium" } },
    ...(webkit
      ? [{ name: "webkit", use: { browserName: "webkit" as const } }]
      : []),
  ],
});
