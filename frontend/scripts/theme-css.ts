/**
 * The app's theme, projected into CSS custom properties Django can serve.
 *
 * WHY THIS EXISTS. Every page under `/accounts/` -- sign in, the TOTP
 * challenge, enrolment, the reauthentication prompt -- is rendered by allauth
 * and served by Django, not by this application. They are the first screen
 * anybody meets and the one place the app asks for a credential, and until
 * inventory-tng-u1am they asked for no stylesheet at all and arrived as bare
 * browser-default HTML.
 *
 * WHY GENERATED RATHER THAN HAND-COPIED, which is the project owner's
 * decision. A second file holding the same colours typed out again is a copy
 * that drifts the first time anybody touches the theme, and nothing would
 * fail. Symlinking is not available -- the values live inside MUI's own
 * default theme rather than in `theme.ts`, the two Docker build contexts are
 * separate, and Django needs CSS where the theme is TypeScript -- so the copy
 * is made by a build step instead, and `theme-css.test.ts` fails when the
 * committed file is not what the theme now resolves to. Without that test this
 * is a copy with extra steps.
 *
 * WHY THE WHOLE SHEET rather than the handful of properties the sign-in pages
 * use. Choosing which properties to carry is exactly the hand-made decision
 * this is replacing: the chosen set would be right today and quietly
 * incomplete the moment a template used one more. MUI decides what the theme
 * contains; this decides nothing.
 *
 * Run it with `npm run theme:css`. The output is committed, following the same
 * argument `capture/` already makes for the guide screenshots: a build step
 * that edits the working tree is not something `npm test` should do, so the
 * artifact is in the repository and a test holds it honest.
 */

import { writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { createTheme } from "@mui/material/styles";
import { themeOptions } from "../src/theme.ts";

const HERE = dirname(fileURLToPath(import.meta.url));

/**
 * Where the stylesheet is written, and where Django collects it from.
 *
 * Under `backend/` rather than anywhere in this package, because it is served
 * by Django's staticfiles through WhiteNoise -- the same path the Django
 * admin's own CSS takes since inventory-tng-o1uj.1 -- and a file the frontend
 * image built would never reach the backend container that serves it.
 */
export const STYLESHEET = resolve(HERE, "../../backend/src/inventory/static/accounts/theme.css");

/**
 * Said at the top of the generated file, so that whoever opens it next knows
 * not to edit it. Ends with the command, because being told a file is
 * generated without being told what generates it is the frustrating half.
 */
export const PREAMBLE = [
  "/* GENERATED FILE -- DO NOT EDIT.",
  " *",
  " * The application's MUI theme, resolved into CSS custom properties so that",
  " * Django's /accounts/ pages are styled from the same values the app is.",
  " * Editing this by hand is undone by the next person who runs:",
  " *",
  " *   cd frontend && npm run theme:css",
  " *",
  " * Change frontend/src/theme.ts instead. frontend/scripts/theme-css.ts says",
  " * why this arrangement rather than a symlink or a hand-written copy.",
  " */",
].join("\n");

type Sheet = Record<string, unknown>;

/**
 * A property name as CSS spells it.
 *
 * MUI's sheet holds ordinary declarations alongside the custom properties --
 * `colorScheme` is one -- and it keys them the way JavaScript does. Written
 * out verbatim they are invalid CSS and every browser drops them silently,
 * which is the worst kind of wrong: no error, no effect, and nothing to
 * notice. Custom properties are already spelt exactly as they are used and
 * must not be touched, since `--mui-shape-borderRadius` really does carry a
 * capital R.
 */
function property(key: string): string {
  return key.startsWith("--") ? key : key.replace(/[A-Z]/g, (c) => `-${c.toLowerCase()}`);
}

/**
 * One CSS rule, formatted. Nested objects are a selector holding declarations;
 * `@media (prefers-color-scheme: dark)` arrives as one, which is what carries
 * the dark colour scheme.
 */
function rule(selector: string, body: Sheet, indent = ""): string {
  const inner = `${indent}  `;
  const lines = Object.entries(body).map(([key, value]) =>
    value !== null && typeof value === "object"
      ? rule(key, value as Sheet, inner)
      : `${inner}${property(key)}: ${String(value)};`,
  );
  return `${indent}${selector} {\n${lines.join("\n")}\n${indent}}`;
}

/**
 * The stylesheet text, as a pure function of the theme options.
 *
 * `colorSchemeSelector: "media"` rather than a class or a data attribute
 * because nothing on a Django-rendered page toggles a theme: there is no React
 * there to set an attribute, so the operating system's preference is the only
 * signal available -- and it is the one the app itself honours, since
 * `CssBaseline` follows it too.
 */
export function stylesheet(): string {
  const theme = createTheme({
    ...themeOptions,
    cssVariables: { colorSchemeSelector: "media" },
  });
  // `generateStyleSheets` is MUI's own; what it emits is the theme rather than
  // this file's idea of it, which is the whole point of generating.
  const sheets = (theme as unknown as { generateStyleSheets: () => Sheet[] }).generateStyleSheets();
  const rules = sheets
    .flatMap((sheet) => Object.entries(sheet))
    .filter(([, body]) => body !== null && typeof body === "object")
    .map(([selector, body]) => rule(selector, body as Sheet));
  return `${PREAMBLE}\n\n${rules.join("\n\n")}\n`;
}

// Written only when run as a command. Importing this from the test must not
// touch the working tree -- a test that rewrites the file it is checking
// always passes.
if (process.argv[1] && resolve(process.argv[1]) === resolve(fileURLToPath(import.meta.url))) {
  writeFileSync(STYLESHEET, stylesheet());
  process.stdout.write(`wrote ${STYLESHEET}\n`);
}
