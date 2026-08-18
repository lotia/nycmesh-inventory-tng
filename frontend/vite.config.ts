/// <reference types="vitest/config" />
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    // Proxy API calls to Django in development so the browser sees a single
    // origin and no CORS preflight is involved locally.
    proxy: {
      "/api": {
        target: process.env.VITE_API_BASE_URL ?? "http://localhost:8000",
        changeOrigin: true,
      },
    },
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
