/**
 * What can be asserted about the decoder without a camera or a WASM runtime:
 * that it is not loaded until it is wanted, and that when it is loaded it
 * fetches its binary from this origin rather than from a CDN.
 *
 * The decoding itself is not exercised here -- jsdom has no camera and no
 * video pipeline to hand it frames. See docs/decisions/0011-qr-batch-scanning.md
 * section 2 for the two constraints this file exists to keep.
 */
import { readFileSync } from "node:fs";
import { describe, expect, it, vi } from "vitest";

/** Set when the ponyfill module is first evaluated, which is the whole point. */
const imported = vi.hoisted(() => ({ count: 0, overrides: null as unknown }));

vi.mock("barcode-detector/pure", () => {
  imported.count += 1;
  return {
    BarcodeDetector: class {
      constructor(readonly options: unknown) {}
      detect = async () => [];
    },
    prepareZXingModule: (options: unknown) => {
      imported.overrides = options;
    },
  };
});

/** The override the module is prepared with, as Emscripten will call it. */
function locateFile(): (path: string, prefix: string) => string {
  const { overrides } = imported.overrides as {
    overrides: { locateFile: (path: string, prefix: string) => string };
  };
  return overrides.locateFile;
}

describe("the decoder", () => {
  it("is not loaded by importing the module that knows how to load it", async () => {
    await import("./decoder");
    expect(imported.count).toBe(0);
  });

  it("is loaded on first use, and only then", async () => {
    const { loadDetector } = await import("./decoder");
    expect(imported.count).toBe(0);

    await loadDetector();
    expect(imported.count).toBe(1);
  });

  it("reads its WebAssembly from this origin, never from a CDN", async () => {
    const { loadDetector } = await import("./decoder");
    await loadDetector();

    const located = locateFile()("zxing_reader.wasm", "https://cdn.jsdelivr.net/npm/zxing-wasm/");
    expect(located).not.toMatch(/^https?:\/\//);
    expect(located).toMatch(/\.wasm$/);
  });

  it("bundles the same zxing-wasm the ponyfill will run", () => {
    // The binary is imported straight from `zxing-wasm`, while the JavaScript
    // that instantiates it comes through `barcode-detector`. Two versions
    // would fail at the one moment nothing here can see -- a volunteer at a
    // shelf with the camera open -- so the two pins are asserted equal, and
    // upgrading either without the other fails the build instead.
    const dependency = (path: string): string =>
      JSON.parse(readFileSync(new URL(path, import.meta.url), "utf8")).dependencies["zxing-wasm"];

    expect(dependency("../../package.json")).toBe(
      dependency("../../node_modules/barcode-detector/package.json"),
    );
  });

  it("leaves anything that is not the binary where the module asked for it", async () => {
    const { loadDetector } = await import("./decoder");
    await loadDetector();

    expect(locateFile()("zxing_reader.js", "/assets/")).toBe("/assets/zxing_reader.js");
  });

  it("keeps the decoder it built, rather than rebuilding a 21 MiB heap per lens switch", async () => {
    // prepareZXingModule keeps the instantiated module only while the
    // overrides object it was given compares equal, so a fresh literal per
    // call would drop it -- on every stop and start, and on every switch of
    // lens, which is the first thing the picker exists for.
    const { forgetDetector, loadDetector } = await import("./decoder");
    forgetDetector();

    const first = await loadDetector();
    const second = await loadDetector();

    expect(second).toBe(first);
  });
});
