/**
 * The environment every other file in this suite assumes it has.
 *
 * Written because nothing here asserted it, and the day it stopped being true
 * the suite said `Cannot read properties of undefined (reading 'clear')` in 247
 * places at once and named no cause. These three are cheap and they fail
 * pointing at the environment rather than at whichever component was unlucky
 * enough to touch storage first. `test-setup.ts` says what the repair is.
 */
import { describe, expect, it } from "vitest";

describe("the environment the suite runs in", () => {
  for (const name of ["localStorage", "sessionStorage"] as const) {
    it(`has a ${name} that keeps what is put in it`, () => {
      const storage = window[name];
      expect(storage).toBeDefined();
      storage.clear();
      storage.setItem("inventory-tng", "kept");
      expect(storage.getItem("inventory-tng")).toBe("kept");
      storage.clear();
      expect(storage.getItem("inventory-tng")).toBeNull();
    });
  }

  // The lead that looked right and was not: jsdom withholds storage from an
  // opaque origin, so a document at `about:blank` has none. Vitest gives jsdom
  // a real URL already, on both node versions, which is why the fault was
  // somewhere else entirely.
  it("is a document with an origin of its own, not an opaque one", () => {
    expect(window.location.origin).not.toBe("null");
    expect(window.location.protocol).toMatch(/^https?:$/);
  });
});
