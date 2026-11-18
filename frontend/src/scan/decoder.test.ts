import { afterEach, beforeEach, expect, test, vi } from "vitest";

const library = vi.hoisted(() => ({
  prepare: vi.fn(async () => {}),
  Ponyfill: class {
    constructor(readonly options: { formats: string[] }) {}
    async detect() {
      return [];
    }
  },
}));

vi.mock("zxing-wasm/reader/zxing_reader.wasm?url", () => ({
  default: "/static/assets/zxing_reader.wasm",
}));
vi.mock("barcode-detector/ponyfill", () => ({
  BarcodeDetector: library.Ponyfill,
  prepareZXingModule: library.prepare,
}));

/** decoder.ts afresh, with nothing loaded yet. */
async function decoder() {
  vi.resetModules();
  return import("./decoder");
}

function browserDetector(formats: string[]) {
  class Native {
    static getSupportedFormats = async () => formats;
    async detect() {
      return [];
    }
  }
  vi.stubGlobal("BarcodeDetector", Native);
  return Native;
}

beforeEach(() => {
  library.prepare.mockReset();
  library.prepare.mockImplementation(async () => {});
});

afterEach(() => {
  vi.unstubAllGlobals();
});

test("the browser's own detector, where it reads QR codes", async () => {
  const Native = browserDetector(["qr_code", "ean_13"]);
  const { qrDetector } = await decoder();
  expect(await qrDetector()).toBeInstanceOf(Native);
  expect(library.prepare).not.toHaveBeenCalled();
});

test("else the ponyfill, with its WebAssembly from this origin", async () => {
  browserDetector(["ean_13"]);
  const { qrDetector } = await decoder();
  expect(await qrDetector()).toBeInstanceOf(library.Ponyfill);
  const [options] = library.prepare.mock.calls[0] as unknown as [
    {
      overrides: { locateFile: (path: string, prefix: string) => string };
      fireImmediately: boolean;
    },
  ];
  expect(options.fireImmediately).toBe(true);
  const { locateFile } = options.overrides;
  expect(locateFile("zxing_reader.wasm", "https://cdn.example/")).toBe(
    "/static/assets/zxing_reader.wasm",
  );
  expect(locateFile("other.js", "/here/")).toBe("/here/other.js");
});

test("the ponyfill loads once", async () => {
  const { qrDetector } = await decoder();
  await qrDetector();
  await qrDetector();
  expect(library.prepare).toHaveBeenCalledTimes(1);
});

test("a ponyfill that failed to load is tried again", async () => {
  library.prepare.mockRejectedValueOnce(new Error("offline"));
  const { qrDetector } = await decoder();
  await expect(qrDetector()).rejects.toThrow("offline");
  expect(await qrDetector()).toBeInstanceOf(library.Ponyfill);
  expect(library.prepare).toHaveBeenCalledTimes(2);
});
