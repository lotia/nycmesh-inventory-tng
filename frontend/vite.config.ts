/// <reference types="vitest/config" />
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// Where Django is listening. Everything below proxies to it; nothing is
// compiled into the bundle, so the browser only ever sees this server.
const BACKEND = process.env.VITE_API_BASE_URL ?? "http://localhost:8000";

// The paths that belong to Django rather than to the app. Which paths, and
// why, is in docs/architecture.md; nginx.conf.template proxies the same set in
// production and must be edited with this.
const DJANGO_PATHS = ["/api", "/admin", "/static"];

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    // Proxy Django's paths in development so the browser sees a single origin
    // and no CORS preflight is involved locally.
    //
    // No changeOrigin: the Host header must stay this server, because Django
    // compares it against the browser's Origin on every write and refuses the
    // write if they disagree. Django must therefore accept whatever host you
    // reach this server on -- see DJANGO_ALLOWED_HOSTS in .env.sample.
    proxy: Object.fromEntries(DJANGO_PATHS.map((path) => [path, { target: BACKEND }])),
  },
  build: {
    outDir: "dist",
    sourcemap: true,
  },
  // Coverage runs on every `npm test`, so a local run and a CI run enforce the
  // same rules. See DEVELOPERS.md "Testing and coverage".
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test-setup.ts"],
    // Unit tests only, and only where they live: next to the code. Vitest's
    // default glob would otherwise collect the Playwright suite, which has its
    // own runner and command -- see DEVELOPERS.md "Integration tests".
    include: ["src/**/*.test.{ts,tsx}"],
    coverage: {
      provider: "v8",
      reporter: ["text", "lcov"],
      // Show every file, including fully covered ones, so the report is a
      // complete picture rather than only a list of failures.
      skipFull: false,
      reportOnFailure: true,
      include: ["src/**/*.{ts,tsx}"],
      exclude: [
        // Bootstrap: mounts React onto the DOM, no behaviour of its own.
        "src/main.tsx",
        // Declarative configuration, not behaviour.
        "src/theme.ts",
        "src/**/*.test.{ts,tsx}",
        "src/test-setup.ts",
      ],
      // Applies to the code left after the exclusions above -- that is, code
      // that actually implements behaviour. Raising this is welcome; lowering
      // it needs a reason in the pull request.
      thresholds: {
        lines: 90,
        functions: 90,
        branches: 90,
        statements: 90,
      },
    },
  },
});
