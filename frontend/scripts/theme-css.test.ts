/**
 * The committed stylesheet, held to being what the theme actually resolves to.
 *
 * This is the test that makes `theme-css.ts` a projection rather than a copy
 * with extra steps. Everything else in that arrangement is machinery; without
 * this, somebody changes `theme.ts`, the sign-in pages keep yesterday's
 * colours, and nothing anywhere goes red.
 */

import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { PREAMBLE, STYLESHEET, stylesheet } from "./theme-css.ts";

const committed = () => readFileSync(STYLESHEET, "utf8");

describe("the generated theme stylesheet", () => {
  it("is what the theme resolves to today", () => {
    expect(committed()).toBe(stylesheet());
  });

  it("says it is generated, and what regenerates it", () => {
    // The file is 23 kilobytes of custom properties. Whoever opens it next is
    // looking for a colour to change, and the useful thing to tell them is
    // that this is not where to change it.
    expect(committed()).toContain(PREAMBLE);
    expect(committed()).toContain("npm run theme:css");
  });

  it("carries the values the sign-in pages are drawn from", () => {
    // Named rather than counted: a generator emitting an empty sheet would
    // satisfy every assertion above, because the committed file would be
    // empty too and the two would still agree.
    const css = committed();
    for (const property of [
      "--mui-palette-background-default",
      "--mui-palette-text-primary",
      "--mui-palette-primary-main",
      "--mui-font-body1",
      "--mui-shape-borderRadius",
    ]) {
      expect(css).toContain(property);
    }
  });

  it("follows the reader's light or dark preference", () => {
    // There is no React on a Django-rendered page to toggle anything, so the
    // media query is the only way these pages can match an app that honours
    // the same preference through CssBaseline. A generator that emitted only
    // the light scheme would leave the sign-in page white for somebody whose
    // app is dark, which is the exact "this looks like a different, broken
    // site" impression u1am is about.
    expect(committed()).toContain("@media (prefers-color-scheme: dark)");
  });
});
