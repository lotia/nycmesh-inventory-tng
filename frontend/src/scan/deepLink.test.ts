import { describe, expect, it } from "vitest";
import { codeFromPath, forgetDeepLink } from "./deepLink";

describe("codeFromPath", () => {
  it("reads the code out of a label's own URL", () => {
    expect(codeFromPath("/S/7QK3M2XV9A")).toBe("7QK3M2XV9A");
  });

  it("accepts the path however it arrived", () => {
    expect(codeFromPath("/s/7qk3m2xv9a")).toBe("7qk3m2xv9a");
    expect(codeFromPath("/S/7QK3M2XV9A/")).toBe("7QK3M2XV9A");
  });

  it("is not a deep link anywhere else in the app", () => {
    expect(codeFromPath("/")).toBeNull();
    expect(codeFromPath("/S")).toBeNull();
    expect(codeFromPath("/S/")).toBeNull();
    expect(codeFromPath("/Scanner/7QK3M2XV9A")).toBeNull();
    expect(codeFromPath("/S/7QK3M2XV9A/extra")).toBeNull();
  });

  it("decodes what a browser encoded on the way in", () => {
    expect(codeFromPath("/S/7QK3M2%58V9A")).toBe("7QK3M2XV9A");
  });

  it("survives an escape that is not one, rather than blanking the app", () => {
    // Read during render, so a throw here is a white screen instead of the
    // sentence saying nothing is labelled that.
    expect(codeFromPath("/S/7QK3M2XV%")).toBe("7QK3M2XV%");
  });
});

describe("forgetDeepLink", () => {
  it("puts the address bar back, so a reload does not scan again", () => {
    window.history.replaceState(null, "", "/S/7QK3M2XV9A");
    forgetDeepLink();
    expect(window.location.pathname).toBe("/");
  });
});
