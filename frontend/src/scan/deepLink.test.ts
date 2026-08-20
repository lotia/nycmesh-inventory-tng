import { describe, expect, it } from "vitest";
import { codeFromPath, codeFromScan, forgetDeepLink } from "./deepLink";

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

describe("the code a scanner read", () => {
  it("unwraps the deep link a label's own symbol carries", () => {
    // What zxing hands back from one of this project's stickers, exactly as
    // backend/src/inventory/labels.py mints it: uppercase, scheme and all.
    // Sent to the resolver as it stands, this 404s -- and did, until an
    // integration test pointed a real camera at a real label.
    expect(codeFromScan("HTTPS://INVENTORY.NYCMESH.NET/S/7QK3M2XV9A")).toBe("7QK3M2XV9A");
  });

  it("leaves a code somebody typed, or a gun wedged, exactly as it is", () => {
    expect(codeFromScan("7QK3M2XV9A")).toBe("7QK3M2XV9A");
    expect(codeFromScan("not a code at all")).toBe("not a code at all");
  });

  it("takes the path shape from any host, because the client cannot know ours", () => {
    // Why the host is not checked is on `codeFromScan`.
    expect(codeFromScan("https://inventory.example.org/s/7QK3M2XV9A")).toBe("7QK3M2XV9A");
    expect(codeFromScan("http://localhost:5173/S/7QK3M2XV9A")).toBe("7QK3M2XV9A");
  });

  it("hands on a QR that belongs to somebody else, to be refused as a code", () => {
    // Not ours, so it resolves to nothing and the volunteer is told so --
    // which is better than a scanner that silently ignores a sticker.
    expect(codeFromScan("https://example.com/promo")).toBe("https://example.com/promo");
    expect(codeFromScan("WIFI:S:mesh;T:WPA;P:hunter2;;")).toBe("WIFI:S:mesh;T:WPA;P:hunter2;;");
  });
});

describe("forgetDeepLink", () => {
  it("puts the address bar back, so a reload does not scan again", () => {
    window.history.replaceState(null, "", "/S/7QK3M2XV9A");
    forgetDeepLink();
    expect(window.location.pathname).toBe("/");
  });
});
