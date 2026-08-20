/**
 * The QR decoder: WebAssembly, from this origin, loaded when somebody opens
 * the camera and not before.
 *
 * Why WASM rather than the browser's own `BarcodeDetector`, and why the
 * ponyfill class rather than the package's side-effect entry point, is settled
 * in docs/decisions/0011-qr-batch-scanning.md section 2 -- the short of it is
 * that half the desktop browsers a volunteer might open this on have no native
 * decoder at all, and one code path is worth more than a slightly faster one
 * on Android.
 *
 * Two of that section's three non-optional constraints are this file's whole
 * job:
 *
 * - **Self-hosted.** The ponyfill fetches its `.wasm` from a public CDN by
 *   default. Stock lives in a basement and the frontend image is meant to be
 *   self-contained, so the binary is imported as a Vite asset and the module
 *   is pointed at that URL instead. `frontend/nginx.conf.template` gzips
 *   `application/wasm` so it is not a megabyte on the wire.
 * - **Lazy.** `barcode-detector` is reached only through the dynamic import
 *   below, so the volunteer who browses the list and taps a stepper never
 *   downloads a decoder. The `?url` import below is a string, not the binary.
 */
// Reached directly, so `zxing-wasm` is a direct dependency pinned to the exact
// version `barcode-detector` pins: this binary and the JavaScript that
// instantiates it have to be one version, and a mismatch would only show up
// with a volunteer at a shelf. decoder.test.ts asserts the two pins agree.
import wasmUrl from "zxing-wasm/reader/zxing_reader.wasm?url";

/**
 * The slice of the `BarcodeDetector` interface this app uses.
 *
 * Narrow on purpose: adopting the native API later should be a change of
 * import here and nothing else, so nothing outside this file names the
 * ponyfill's types.
 *
 * `ImageData` is named alongside `CanvasImageSource` because it is what the
 * camera actually hands over -- see frame.ts for why, and note that the native
 * `BarcodeDetector` takes it too: both accept an `ImageBitmapSource`, which
 * `CanvasImageSource` alone does not cover.
 */
export interface CodeDetector {
  detect(source: CanvasImageSource | ImageData): Promise<Decoded[]>;
}

/** One code, as a detector reports it. Everything else it offers is unused. */
export interface Decoded {
  rawValue: string;
}

/**
 * Where Emscripten is told to find the binary.
 *
 * At module scope, and this matters: `prepareZXingModule` decides whether the
 * already-instantiated module can be kept by comparing this object with the
 * one it was last given. A fresh literal per call never compares equal, so it
 * would drop the cache and rebuild a 21 MiB WebAssembly heap -- on every stop
 * and start of the camera, and on every switch of lens, which is the first
 * thing the picker exists for somebody to do.
 *
 * Emscripten asks for every file it needs by name against a base URL; only the
 * binary is ours to redirect, and answering for anything else would hide a
 * future asset rather than serve it.
 */
const OVERRIDES = {
  locateFile: (path: string, prefix: string) =>
    path.endsWith(".wasm") ? wasmUrl : `${prefix}${path}`,
};

/**
 * The decoder, fetched and instantiated on first use and kept after that.
 *
 * The promise is held, not the result, because two frames may ask before the
 * first answer arrives and one download is the point. But a promise that
 * rejected is held just as firmly as one that resolved, and a rejection here
 * is the ordinary case rather than the strange one: a basement connection that
 * dropped, or an `index.html` that outlived the chunk it names after a deploy.
 * Kept, it makes the first failure permanent -- every later scan replays the
 * same rejection without asking the network again, and the only cure is a
 * reload the volunteer has no reason to think of.
 *
 * So the promise is forgotten on rejection, and the next call starts over.
 */
let detector: Promise<CodeDetector> | null = null;

export function loadDetector(): Promise<CodeDetector> {
  if (detector === null) {
    const attempt: Promise<CodeDetector> = (async () => {
      const { BarcodeDetector, prepareZXingModule } = await import("barcode-detector/pure");
      prepareZXingModule({ overrides: OVERRIDES });
      return new BarcodeDetector({ formats: ["qr_code"] });
    })().catch((reason: unknown) => {
      // Forgotten before rethrowing, so this call still fails and the next one
      // starts over. Only if it is still the current attempt: a later call may
      // have begun a fresh one, and dropping that would download twice.
      if (detector === attempt) {
        detector = null;
      }
      throw reason;
    });
    detector = attempt;
  }
  return detector;
}

/** Lets a test start again from nothing. Not used by the app. */
export function forgetDetector(): void {
  detector = null;
}
