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

// A cold `uv run` and Vite's first pass over the MUI dependency tree are both
// slower than Playwright's 60s default, and overrunning it reports only
// "server failed to start".
const SERVER_BOOT_TIMEOUT = 120_000;

// A sign-in here completes a real one-time password, and the server accepts
// each code once for the thirty seconds it is valid -- so a test that signs in
// straight after another one waits for the next code. See integration/sign-in.ts.
// Playwright's 30s default would report that wait as a timeout.
const SIGN_IN_MAY_WAIT_FOR_A_FRESH_CODE = 90_000;

export default defineConfig({
  testDir: "./integration",
  timeout: SIGN_IN_MAY_WAIT_FOR_A_FRESH_CODE,
  // A committed `test.only` would otherwise leave this job green having run a
  // single test -- on the one suite that can see this class of bug at all.
  forbidOnly: !!process.env.CI,
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
      // --noreload: the autoreloader would restart the server underneath an
      // in-flight request whenever a file changed, which reads as a flake.
      command: `cd ../backend && uv run python src/manage.py runserver --noreload ${BACKEND_PORT}`,
      port: BACKEND_PORT,
      timeout: SERVER_BOOT_TIMEOUT,
      reuseExistingServer: !process.env.CI,
      stdout: "pipe",
      stderr: "pipe",
    },
    {
      command: "npm run dev",
      url: FRONTEND,
      timeout: SERVER_BOOT_TIMEOUT,
      reuseExistingServer: !process.env.CI,
    },
  ],
});
