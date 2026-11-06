/**
 * A QR decoder: the browser's own BarcodeDetector where it has one (Chrome
 * on Android), else the barcode-detector ponyfill (iOS Safari), whose
 * WebAssembly comes from this origin, loaded once (docs/decisions/0010).
 */
import wasm from "zxing-wasm/reader/zxing_reader.wasm?url";

export type Detector = {
  detect(source: HTMLVideoElement): Promise<{ rawValue: string }[]>;
};

type DetectorClass = {
  new (options: { formats: "qr_code"[] }): Detector;
  getSupportedFormats(): Promise<readonly string[]>;
};

/** The ponyfill, loaded with its WebAssembly; tried again if that failed. */
let ponyfill: Promise<DetectorClass> | undefined;

export async function qrDetector(): Promise<Detector> {
  const native = (globalThis as { BarcodeDetector?: DetectorClass })
    .BarcodeDetector;
  if (
    native !== undefined &&
    (await native.getSupportedFormats()).includes("qr_code")
  ) {
    return new native({ formats: ["qr_code"] });
  }
  ponyfill ??= loadPonyfill();
  const Ponyfill = await ponyfill.catch((error: unknown) => {
    // Tried again next time, as after a dropped connection.
    ponyfill = undefined;
    throw error;
  });
  return new Ponyfill({ formats: ["qr_code"] });
}

async function loadPonyfill(): Promise<DetectorClass> {
  const { BarcodeDetector, prepareZXingModule } = await import(
    "barcode-detector/ponyfill"
  );
  // From this origin, not the CDN the library would otherwise use, and
  // loaded now, so a failure shows here rather than at every frame.
  await prepareZXingModule({
    overrides: {
      locateFile: (path: string, prefix: string) =>
        path.endsWith(".wasm") ? wasm : prefix + path,
    },
    fireImmediately: true,
  });
  return BarcodeDetector;
}
