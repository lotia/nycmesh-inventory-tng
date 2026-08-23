/// <reference types="vitest/config" />
import { fileURLToPath } from "node:url";
import react from "@vitejs/plugin-react";
import { defineConfig, loadEnv } from "vite";

// The repository root, where the one .env this project has lives.
const REPO_ROOT = fileURLToPath(new URL("..", import.meta.url));

// The paths that belong to Django rather than to the app. Which paths, and
// why, is in docs/architecture.md; nginx.conf.template proxies the same set in
// production and must be edited with this.
const DJANGO_PATHS = ["api", "admin", "accounts", "static"];

// Anchored, and for the reason nginx.conf.template gives for anchoring the
// same set. Vite treats a key starting with `^` as a regular expression and
// tests it against the URL including its query string, which is why `?` ends
// the match as well as `/`.
const DJANGO_PROXY = `^/(${DJANGO_PATHS.join("|")})($|[/?])`;

export default defineConfig(({ mode }) => ({
  plugins: [react()],
  server: {
    port: 5173,
    // Proxy Django's paths in development so the browser sees a single origin
    // and no CORS preflight is involved locally.
    //
    // Where Django is listening is read from the repository-root .env, which
    // Vite would not otherwise look at: its own env loading covers
    // frontend/ and feeds the bundle, not this file. A shell variable still
    // wins, so a one-off override needs no edit. Nothing here is compiled
    // into the bundle, so the browser only ever sees this server.
    //
    // No changeOrigin: the Host header must stay this server, because Django
    // compares it against the browser's Origin on every write and refuses the
    // write if they disagree. Django must therefore accept whatever host you
    // reach this server on -- see DJANGO_ALLOWED_HOSTS in .env.sample.
    proxy: {
      [DJANGO_PROXY]: {
        target:
          process.env.VITE_API_BASE_URL ??
          loadEnv(mode, REPO_ROOT, "VITE_").VITE_API_BASE_URL ??
          "http://localhost:8000",
      },
    },
  },
  build: {
    outDir: "dist",
    sourcemap: true,
  },
  // Coverage runs on every `npm test`, for the reason the backend's
  // `pyproject.toml` gives about its own. See DEVELOPERS.md "Testing and
  // coverage".
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test-setup.ts"],
    // Unit tests only, and only where they live: next to the code. Vitest's
    // default glob would otherwise collect the Playwright suite, which has its
    // own runner and command -- see DEVELOPERS.md "Integration tests".
    //
    // `capture/` is here for its own arithmetic and its own list of pictures,
    // both of which are ordinary units. The driver beside them is a Playwright
    // file and is named `.capture.ts` so that this glob leaves it alone.
    include: ["src/**/*.test.{ts,tsx}", "capture/**/*.test.ts"],
    coverage: {
      provider: "v8",
      reporter: ["text", "lcov"],
      // Show every file, including fully covered ones, so the report is a
      // complete picture rather than only a list of failures.
      skipFull: false,
      reportOnFailure: true,
      // `capture/` is measured for the same reason its tests are run: the
      // arithmetic behind a crop and the list the guides are checked against
      // are ordinary units, and leaving them out of this list was an exclusion
      // nothing recorded. What is genuinely out of reach is named below.
      include: ["src/**/*.{ts,tsx}", "capture/**/*.ts"],
      exclude: [
        // Bootstrap: mounts React onto the DOM, no behaviour of its own.
        "src/main.tsx",
        // Declarative configuration, not behaviour.
        "src/theme.ts",
        "src/**/*.test.{ts,tsx}",
        "src/test-setup.ts",
        // Scaffolding the tests render through, and never shipped: it imports
        // vitest, so nothing outside a test can reach it. Excluded for the
        // same reason as test-setup.ts, which its filename does not say.
        "src/testHarness.tsx",
        "capture/**/*.test.ts",
        // The three files in capture/ that only a browser reaches. The driver
        // is a Playwright spec run by `npm run capture:guides`; `drive.ts` is
        // gestures against a live `Page`; `scene.ts` shells out to
        // `manage.py`. Covering them from here would mean asserting that
        // Playwright was called the way it was called, which is a restatement
        // of the code rather than a test of it -- what they actually do is
        // proved by the capture run and by
        // `integration/guide-controls.spec.ts`, both of which fail loudly.
        "capture/*.capture.ts",
        "capture/drive.ts",
        "capture/scene.ts",
      ],
      // Over what the exclusions above leave, and held to the same bar as the
      // backend's `fail_under`.
      thresholds: {
        lines: 90,
        functions: 90,
        branches: 90,
        statements: 90,
      },
    },
  },
}));
