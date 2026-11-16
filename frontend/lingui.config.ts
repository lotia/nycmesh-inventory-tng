/**
 * Lingui's settings: where the scan page's catalogues are and how they are
 * written (docs/decisions/0022). The CLI reads them to extract, and Vite's
 * plugin to compile each catalogue a page imports.
 */
import { resolve } from "node:path";
import { defineConfig } from "@lingui/cli";
import { formatter } from "@lingui/format-po";

// Elsewhere only for mise run check-catalogues, which extracts into a copy.
const catalogues = resolve(
  import.meta.dirname,
  process.env.LINGUI_CATALOGUES ?? "src/locales",
);

export default defineConfig({
  sourceLocale: "en",
  locales: ["en", "pseudo"],
  // Accented, so a test sees any English not read through the catalogue.
  pseudoLocale: { locale: "pseudo" },
  fallbackLocales: { default: "en" },
  catalogs: [
    {
      path: `${catalogues}/{locale}`,
      include: ["<rootDir>/src"],
      exclude: ["**/*.test.*", "**/test-*"],
    },
  ],
  // Files only: a line moving does not change the catalogue.
  format: formatter({ lineNumbers: false }),
  orderBy: "messageId",
});
