import { defineConfig } from "@playwright/test";
import base from "./playwright.config";

/**
 * The screenshot run, on the same servers and the same scene as the tests.
 *
 * Everything about how those are started is `playwright.config.ts`'s, and this
 * spreads that config rather than restating any of it -- so a change to how
 * the servers boot or how the database is seeded reaches the pictures without
 * anybody remembering this file exists.
 *
 * What it changes is the two things that make this not a test: a directory of
 * its own, and a file suffix the test command's glob does not match. A capture
 * writes PNGs into the repository, which is not something `npm run
 * test:integration` may do -- see DEVELOPERS.md.
 */
export default defineConfig({
  ...base,
  testDir: "./capture",
  testMatch: "**/*.capture.ts",
  // A screenshot is deterministic or it is a bug, so a retry would only hide
  // one. It is also never run by CI.
  retries: 0,
  reporter: "list",
});
