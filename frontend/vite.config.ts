/**
 * Builds the scan page, the registration page and the stylesheet every page
 * shares (docs/decisions/0010 and 0014). Tailwind's plugin also reloads the
 * pages open on the dev server when a template it reads is saved.
 *
 * The backend serves the output under /static and reads the manifest to
 * write each page's tags, so the entries' names below are the ones the
 * backend asks for. In development the backend points at this dev server
 * instead, which serves the same paths.
 */
import { existsSync, readFileSync } from "node:fs";
import { parseEnv } from "node:util";
import preact from "@preact/preset-vite";
import tailwindcss from "@tailwindcss/vite";
import { defineConfig } from "vitest/config";

const DEFAULT_ORIGIN = "http://localhost:5173";

// Each page's entry: what the build bundles.
const pages = ["src/scan.tsx", "src/register.tsx"];
/**
 * VITE_DEV_SERVER as the backend reads it: from the shell, else from the
 * repository's .env and no other file, wherever Vite is started from.
 */
function devServer(): string {
  if (process.env.VITE_DEV_SERVER !== undefined) {
    return process.env.VITE_DEV_SERVER.trim();
  }
  const file = new URL("../.env", import.meta.url);
  if (!existsSync(file)) return "";
  return (parseEnv(readFileSync(file, "utf8")).VITE_DEV_SERVER ?? "").trim();
}

const url = new URL(devServer() || DEFAULT_ORIGIN);
// Only the origin, as the backend keeps it.
const { origin, hostname, port } = url;
if (!port || port === "0") {
  // The backend requires one too, and neither counts a scheme's default
  // port, so both always agree on the address.
  throw new Error(
    `VITE_DEV_SERVER needs a port, such as ${DEFAULT_ORIGIN}, not ${origin}`,
  );
}

export default defineConfig({
  plugins: [preact(), tailwindcss()],
  base: "/static/",
  // No public/ directory: the backend serves only the hashed files Vite
  // writes to assets/, so a file copied as it is would be found by the dev
  // server and missing from the build.
  publicDir: false,
  build: {
    manifest: true,
    rolldownOptions: {
      input: [...pages, "src/styles.css"],
    },
  },
  server: {
    // The pages come from the backend on another port, so assets that CSS
    // names must be fetched from here, not from the page's origin.
    origin,
    // An IPv6 address without the brackets a URL needs.
    host: hostname.replace(/^\[(.*)\]$/, "$1"),
    port: Number(port),
    strictPort: true,
  },
  test: {
    // Unit tests only: browser/ is Playwright's (playwright.config.ts).
    include: ["src/**/*.test.{ts,tsx}"],
    environment: "happy-dom",
    setupFiles: ["src/test-setup.ts"],
    // Files and tests in a random order, its seed printed, so none can
    // depend on another (CONTRIBUTING.md, Running the checks).
    sequence: { shuffle: true },
    // The floors and how they are measured: CONTRIBUTING.md, Coverage.
    coverage: {
      provider: "v8",
      include: ["src/**/*.{ts,tsx}"],
      exclude: [
        // Test helpers, not the app.
        "src/test-*.ts",
        // Generated from the backend's schema (npm run api).
        "src/api/**",
        // Pages' entries: wiring that runs only in a browser.
        ...pages,
        // The camera itself: browser/camera.spec.ts runs it, in Chromium.
        "src/camera.ts",
      ],
      reporter: ["text-summary", "cobertura"],
      // A point below what is reached, never lowered (autoUpdate).
      thresholds: {
        autoUpdate: (reached: number, previous: number) =>
          Math.max(previous, Math.floor(reached) - 1),
        statements: 98,
        branches: 93,
        functions: 98,
        lines: 98,
      },
    },
  },
});
