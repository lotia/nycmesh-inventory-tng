import { defineConfig, devices } from "@playwright/test";

/**
 * Integration tests: a real browser, a real Vite server, a real Django.
 *
 * Why they exist, what they need, and how to run them are in DEVELOPERS.md
 * "Integration tests". Kept apart from the unit tests on purpose -- own
 * directory, own command, no part in the coverage threshold.
 */

const FRONTEND = "http://localhost:5173";
const BACKEND_PORT = 8000;

export default defineConfig({
  testDir: "./integration",
  // Migrating and seeding happen here rather than in the backend's start
  // command below, which a reused server would skip. See the file itself.
  globalSetup: "./integration/global-setup.ts",
  // A browser is slow and the servers are shared, so a failure here is worth
  // one retry before it is believed -- but only in CI, where flakiness costs
  // somebody else's time rather than yours.
  retries: process.env.CI ? 1 : 0,
  // One worker: the tests share one database and one seeded scene.
  workers: 1,
  reporter: process.env.CI ? "github" : "list",
  use: {
    baseURL: FRONTEND,
    trace: "retain-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: [
    {
      command: `cd ../backend && uv run python src/manage.py runserver ${BACKEND_PORT}`,
      port: BACKEND_PORT,
      reuseExistingServer: !process.env.CI,
      stdout: "pipe",
      stderr: "pipe",
    },
    {
      command: "npm run dev",
      url: FRONTEND,
      reuseExistingServer: !process.env.CI,
    },
  ],
});
